"""T85.e: `run-night` real (cli.main) con el redactor del archivo, contra PostgreSQL de test.

Siembra HD 202206 c (masa y periodo) con `ComputeSolutionTensions` real y sustituye el SDK
por `FakeLLMProvider`: ninguna llamada a Claude. Se comprueba de punta a punta el
interruptor `[solution_tensions] writer_enabled`, el tope `max_writer_calls_per_night`, la
persistencia de los `Finding` `solution_tension` (CHECK e índice único reales) y el prompt
del Editor (`editor-v3` / `editor-v4`).
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from helpers.solution_tension_smoke import SeededSolutionTension, seed_hd202206c_solution_tension
from helpers.solution_tension_writer import catalog_solution, make_solution_tension
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from nocturna import cli
from nocturna.application.budget import BudgetGuard
from nocturna.application.use_cases import run_night as run_night_module
from nocturna.domain.entities import (
    Finding,
    FindingType,
    ItemStatus,
    MeasuredParameter,
    MeasurementUnit,
    RunStatus,
)
from nocturna.domain.llm import AgentRole
from nocturna.domain.solution_tension import solution_tension_data_from
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow, FindingRow, ItemRow, RunRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemySolutionTensionRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"
_WITHIN_WINDOW = datetime(2026, 10, 15, 2, 0, tzinfo=ZoneInfo("Europe/Madrid"))
ARCHIVE_JSON = {
    "title": "HD 202206 c: dos soluciones del archivo que no coinciden",
    "level_curious": "Nivel curioso de prueba.",
    "level_amateur": "Nivel aficionado de prueba.",
    "level_technical": "Nivel técnico de prueba.",
}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    assert test_database_url.rsplit("/", 1)[-1] == "nocturna_test"
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)
    monkeypatch.setattr(cli, "SystemClock", lambda *a, **k: FakeClock(_WITHIN_WINDOW))
    monkeypatch.setattr(run_night_module._logger, "disabled", False)
    real = httpx.AsyncClient

    def _client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=(FIXTURES_DIR / "oai" / "list_records_empty.xml").read_bytes()
            )
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeLLMProvider:
    provider = FakeLLMProvider()
    monkeypatch.setattr(agent_sdk_provider, "AgentSDKProvider", lambda: provider)
    return provider


def _config(monkeypatch: pytest.MonkeyPatch, *, writer_enabled: bool):
    config = load_pipeline_config()
    config = config.model_copy(
        update={
            "solution_tensions": config.solution_tensions.model_copy(
                update={"writer_enabled": writer_enabled}
            )
        }
    )
    monkeypatch.setattr(cli, "load_pipeline_config", lambda *a, **k: config)
    return config


def _seed(db_session_factory) -> SeededSolutionTension:
    return seed_hd202206c_solution_tension(
        db_session_factory, load_pipeline_config(), run_budget_tokens=26_000
    )


def _seed_third_eligible(db_session_factory, seeded: SeededSolutionTension) -> None:
    """Tercera tensión elegible (radio, σ 20) sobre las mismas dos soluciones y el mismo Item."""
    base = make_solution_tension(seeded.item.id, sigma=20.0, planet="HD 202206 c")
    third = replace(
        base,
        parameter=MeasuredParameter.RADIUS,
        new=catalog_solution(
            "Cao et al. 2026",
            1.2,
            0.01,
            parameter=MeasuredParameter.RADIUS,
            unit=MeasurementUnit.R_JUP,
            is_default=True,
            releasedate=base.new.releasedate,
            planet="HD 202206 c",
        ),
        reference=catalog_solution(
            "Otro et al. 2020",
            0.8,
            0.02,
            parameter=MeasuredParameter.RADIUS,
            unit=MeasurementUnit.R_JUP,
            planet="HD 202206 c",
        ),
        period_check=None,
        new_solution_key=seeded.mass.new_solution_key,
        reference_solution_key=seeded.mass.reference_solution_key,
    )
    with unit_of_work(db_session_factory) as session:
        SqlAlchemySolutionTensionRepository(session).add(third)


class _EditorSpy:
    """Respuesta del Editor que aprueba todo lo pendiente y anota lo que vio en la base."""

    def __init__(self, fake: FakeLLMProvider, factory) -> None:
        self.unpublished_solution_tensions: list[tuple[object, object]] = []
        self._factory = factory
        fake.respond_with(AgentRole.EDITOR, build=self._build, tokens_in=1500, tokens_out=100)

    def _build(self, _request: object) -> dict:
        with self._factory() as session:
            rows = session.execute(select(FindingRow)).scalars().all()
        self.unpublished_solution_tensions = [
            (r.published_at, r.solution_tension_id)
            for r in rows
            if r.type is FindingType.SOLUTION_TENSION
        ]
        return {
            "publish": [
                {"candidate_id": str(r.id), "confidence": 0.8, "reason": "motivo"}
                for r in rows
                if r.published_at is None
            ]
        }


def _writer_ok(fake: FakeLLMProvider, n: int) -> None:
    for _ in range(n):
        fake.respond(AgentRole.WRITER, json=ARCHIVE_JSON, tokens_in=700, tokens_out=300)


def _agent_calls(factory, role: AgentRole) -> list[AgentCallRow]:
    with factory() as session:
        return list(
            session.execute(select(AgentCallRow).where(AgentCallRow.agent == role)).scalars()
        )


def _json_events(stderr: str, event: str) -> list[dict]:
    out = []
    for line in stderr.splitlines():
        try:
            data = json.loads(line)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("event") == event:
            out.append(data)
    return out


def test_interruptor_encendido_redacta_las_dos_tensiones_y_el_editor_es_v4(
    db_session_factory, fake, monkeypatch, capsys
) -> None:
    config = _config(monkeypatch, writer_enabled=True)
    assert config.limits.max_writer_calls_per_night == 2
    seeded = _seed(db_session_factory)
    _writer_ok(fake, 2)
    editor = _EditorSpy(fake, db_session_factory)

    code = cli.main(["run-night"])

    assert code == 0
    calls = _agent_calls(db_session_factory, AgentRole.WRITER)
    assert [c.prompt_version for c in calls] == ["writer-archive-v1", "writer-archive-v1"]
    with db_session_factory() as session:
        findings = list(session.execute(select(FindingRow)).scalars())
        item = session.get(ItemRow, seeded.item.id)
        run = session.execute(select(RunRow).where(RunRow.status == RunStatus.COMPLETED)).scalar()
    assert len(findings) == 2 and {f.type for f in findings} == {FindingType.SOLUTION_TENSION}
    assert all(f.solution_tension_id is not None and f.solution_tension for f in findings)
    assert {f.solution_tension_id for f in findings} == {
        t.id for t in _all_tensions(db_session_factory)
    }
    # Sin publicar hasta que el Editor decide; después, publicados por él.
    assert len(editor.unpublished_solution_tensions) == 2
    assert all(published is None for published, _ in editor.unpublished_solution_tensions)
    assert all(f.published_at is not None for f in findings)
    assert item is not None and item.status is ItemStatus.NEW
    assert run is not None
    (editor_call,) = [c for c in fake.calls if c.role is AgentRole.EDITOR]
    assert editor_call.prompt.count("type: solution_tension") == 2
    (editor_row,) = _agent_calls(db_session_factory, AgentRole.EDITOR)
    assert editor_row.prompt_version == "editor-v4"
    assert all(c.prompt_version != "editor-v4" for c in calls)
    assert capsys.readouterr().err  # el log JSON existe; el detalle lo cubren otros tests


def _all_tensions(factory):
    with unit_of_work(factory) as session:
        return SqlAlchemySolutionTensionRepository(session).all()


def test_los_findings_del_archivo_respetan_el_check_y_el_indice_unico_reales(
    db_session_factory, fake, monkeypatch
) -> None:
    _config(monkeypatch, writer_enabled=True)
    seeded = _seed(db_session_factory)
    _writer_ok(fake, 2)
    _EditorSpy(fake, db_session_factory)
    assert cli.main(["run-night"]) == 0
    data = solution_tension_data_from(
        seeded.mass, threshold_sigma=3.0, archive_url="https://archive.example/x"
    )

    def _finding(**kw) -> Finding:
        base = {
            "item_id": seeded.item.id,
            "run_id": seeded.run_id,
            "type": FindingType.SOLUTION_TENSION,
            "title": "t",
            "level_curious": "c",
            "level_amateur": "a",
            "level_technical": "t",
            "solution_tension": data,
            "solution_tension_id": seeded.mass.id,
        }
        base.update(kw)
        return Finding(**base)

    with pytest.raises(IntegrityError, match="uq_findings_solution_tension_id_type"):
        with unit_of_work(db_session_factory) as session:
            SqlAlchemyFindingRepository(session).add(_finding())
    # El dominio ya impide construirlo; el CHECK de la base lo impide por SQL directo.
    for column, constraint in (
        ("solution_tension_id", "solution_tension_id_iff_type"),
        ("solution_tension", "solution_tension_iff_type"),
    ):
        with pytest.raises(IntegrityError, match=constraint):
            with unit_of_work(db_session_factory) as session:
                session.execute(
                    sa.text(f"update findings set {column} = null where type = 'solution_tension'")
                )
                session.flush()


def test_con_una_tercera_elegible_el_tope_deja_una_sin_redactar_por_call_limit(
    db_session_factory, fake, monkeypatch, capsys
) -> None:
    _config(monkeypatch, writer_enabled=True)
    seeded = _seed(db_session_factory)
    _seed_third_eligible(db_session_factory, seeded)  # la de menor σ queda sin redactar
    assert len(_all_tensions(db_session_factory)) == 3
    _writer_ok(fake, 3)  # una de más: si el tope fallara, se consumiría
    _EditorSpy(fake, db_session_factory)

    assert cli.main(["run-night"]) == 0

    assert len(_agent_calls(db_session_factory, AgentRole.WRITER)) == 2
    with db_session_factory() as session:
        written = {f.solution_tension_id for f in session.execute(select(FindingRow)).scalars()}
    unwritten = [t for t in _all_tensions(db_session_factory) if t.id not in written]
    assert len(written) == 2 and len(unwritten) == 1
    assert unwritten[0].sigma == min(t.sigma for t in _all_tensions(db_session_factory))
    ends = [
        e
        for e in _json_events(capsys.readouterr().err, "night.phase_end")
        if e.get("phase") == "writer_archive"
    ]
    assert len(ends) == 1 and ends[0]["stop_reason"] == "call_limit_reached"
    assert ends[0]["ok"] == 2


def test_interruptor_apagado_cero_llamadas_cero_authorize_y_editor_v3(
    db_session_factory, fake, monkeypatch
) -> None:
    _config(monkeypatch, writer_enabled=False)
    _seed(db_session_factory)
    editor = _EditorSpy(fake, db_session_factory)
    roles_authorized: list[AgentRole] = []
    real_authorize = BudgetGuard.authorize

    def _spy(self, role, *args, **kwargs):
        roles_authorized.append(role)
        return real_authorize(self, role, *args, **kwargs)

    monkeypatch.setattr(BudgetGuard, "authorize", _spy)

    code = cli.main(["run-night"])

    assert code == 0
    assert _agent_calls(db_session_factory, AgentRole.WRITER) == []
    assert AgentRole.WRITER not in roles_authorized
    assert [c for c in fake.calls if c.role is AgentRole.WRITER] == []
    with db_session_factory() as session:
        assert (
            session.execute(
                select(FindingRow).where(FindingRow.type == FindingType.SOLUTION_TENSION)
            ).first()
            is None
        )
    assert editor.unpublished_solution_tensions == []
    editor_rows = _agent_calls(db_session_factory, AgentRole.EDITOR)
    assert [r.prompt_version for r in editor_rows] == ["editor-v3"] or editor_rows == []
