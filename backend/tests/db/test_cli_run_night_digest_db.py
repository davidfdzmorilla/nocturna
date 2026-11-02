"""T84.r: `run-night` real (cli.main) con el redactor del resumen semanal, contra PostgreSQL.

Siembra con commits reales el histórico del archivo (semana 2026-W40, con un cambio de
referencia) y sustituye el SDK por `FakeLLMProvider`: ninguna llamada a Claude. Comprueba
de punta a punta el interruptor `[archive_digest] writer_enabled`, la tabla
`archive_digest_text`, el `AgentCall` del redactor sin ítem y que una segunda noche no
vuelve a redactar. Fichero en español.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from sqlalchemy import select
from test_archive_repository import save, snapshot, solution

from nocturna import cli
from nocturna.application.agents.prompt_loader import load_prompt
from nocturna.application.budget import BudgetGuard
from nocturna.application.use_cases import run_night as run_night_module
from nocturna.domain.archive import SnapshotKind
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow, RunRow
from nocturna.infrastructure.db.repositories import SqlAlchemyArchiveRepository
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"
# Miércoles de la W42 de 2026, 03:00 hora de Madrid: dentro de la ventana, W40 cerrada.
_WITHIN_WINDOW = datetime(2026, 10, 14, 3, 0, tzinfo=ZoneInfo("Europe/Madrid"))
DIGEST_JSON = {
    "title": "Un cambio de referencia en el archivo de exoplanetas",
    "summary": "Esta semana el archivo cambió la referencia de un planeta.\n\nNada más.",
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


def _config(monkeypatch: pytest.MonkeyPatch, *, digest_enabled: bool):
    config = load_pipeline_config()
    digest = config.archive_digest.model_copy(
        update={"writer_enabled": digest_enabled, "since_week": "2026-W40"}
    )
    config = config.model_copy(update={"archive_digest": digest})
    monkeypatch.setattr(cli, "load_pipeline_config", lambda *a, **k: config)
    return config


def _seed_week_40(factory) -> None:
    """Semana W40 de 2026: un cambio de referencia, un planeta nuevo y una pérdida."""
    with unit_of_work(factory) as session:
        repo = SqlAlchemyArchiveRepository(session)
        a = solution("A b", "Ref A", is_default=True, mass=1.0)
        gone = solution("G b", "Ref G", is_default=True)
        save(repo, snapshot(rows=2), [a, gone])
        a2 = solution("A b", "Ref A2", is_default=True, mass=3.0)
        n = solution("N b", "Ref N", is_default=True)
        save(
            repo,
            snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=1),
            [a2, n],
            removal_scope=frozenset({"A b", "N b", "G b"}),
        )


def _digest_rows(factory) -> list[sa.Row]:
    with factory() as session:
        return list(
            session.execute(
                sa.text(
                    "select week, status, title, summary, input_sha256, prompt_version, run_id "
                    "from archive_digest_text order by week"
                )
            )
        )


def _writer_calls(factory) -> list[AgentCallRow]:
    with factory() as session:
        return list(
            session.execute(
                select(AgentCallRow).where(AgentCallRow.agent == AgentRole.WRITER)
            ).scalars()
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


def test_interruptor_encendido_redacta_la_semana_cerrada_y_la_guarda(
    db_session_factory, fake, monkeypatch, capsys
) -> None:
    _config(monkeypatch, digest_enabled=True)
    _seed_week_40(db_session_factory)
    fake.respond(AgentRole.WRITER, json=DIGEST_JSON, tokens_in=700, tokens_out=300)

    code = cli.main(["run-night"])

    assert code == 0
    (row,) = _digest_rows(db_session_factory)
    assert (row.week, row.status) == ("2026-W40", "written")
    assert row.title == DIGEST_JSON["title"] and row.summary == DIGEST_JSON["summary"]
    assert row.prompt_version == "writer-digest-v1" and len(row.input_sha256) == 64
    (call,) = _writer_calls(db_session_factory)
    assert call.item_id is None and call.prompt_version == "writer-digest-v1"
    assert call.run_id == row.run_id and (call.tokens_in, call.tokens_out) == (700, 300)
    (request,) = [c for c in fake.calls if c.role is AgentRole.WRITER]
    assert request.system_prompt == load_prompt("writer-digest-v1")
    assert "<weekly_digest>" in request.prompt and "2026-W40" in request.prompt
    assert request.model == load_pipeline_config().models.writer
    assert AgentRole.EDITOR not in [c.role for c in fake.calls]  # sin candidatos no hay Editor
    with db_session_factory() as session:
        run = session.execute(select(RunRow)).scalars().one()
        assert run.status.value == "completed" and run.tokens_used == 1000
    ends = [
        e
        for e in _json_events(capsys.readouterr().err, "night.phase_end")
        if e.get("phase") == "writer_digest"
    ]
    assert len(ends) == 1 and ends[0]["ok"] == 1


def test_una_segunda_noche_no_vuelve_a_redactar_la_semana(
    db_session_factory, fake, monkeypatch
) -> None:
    _config(monkeypatch, digest_enabled=True)
    _seed_week_40(db_session_factory)
    fake.respond(AgentRole.WRITER, json=DIGEST_JSON, tokens_in=700, tokens_out=300)
    assert cli.main(["run-night"]) == 0
    before = _digest_rows(db_session_factory)

    # Si la segunda noche llamara al redactor, la cola vacía del fake haría fallar el test.
    assert cli.main(["run-night"]) == 0

    assert _digest_rows(db_session_factory) == before
    assert len(_writer_calls(db_session_factory)) == 1


def test_interruptor_apagado_cero_llamadas_cero_authorize_y_ninguna_fila(
    db_session_factory, fake, monkeypatch
) -> None:
    _config(monkeypatch, digest_enabled=False)
    _seed_week_40(db_session_factory)
    roles_authorized: list[AgentRole] = []
    real_authorize = BudgetGuard.authorize

    def _spy(self, role, *args, **kwargs):
        roles_authorized.append(role)
        return real_authorize(self, role, *args, **kwargs)

    monkeypatch.setattr(BudgetGuard, "authorize", _spy)

    code = cli.main(["run-night"])

    assert code == 0
    assert _writer_calls(db_session_factory) == [] and _digest_rows(db_session_factory) == []
    assert AgentRole.WRITER not in roles_authorized
    assert [c for c in fake.calls if c.role is AgentRole.WRITER] == []


def test_json_invalido_dos_veces_deja_la_fila_failed_y_no_se_reintenta_en_la_noche_siguiente(
    db_session_factory, fake, monkeypatch
) -> None:
    config = _config(monkeypatch, digest_enabled=True)
    _seed_week_40(db_session_factory)
    for _ in range(config.limits.max_calls_per_item):
        fake.respond(AgentRole.WRITER, raw="no es json", tokens_in=100, tokens_out=10)

    assert cli.main(["run-night"]) == 0

    (row,) = _digest_rows(db_session_factory)
    assert row.status == "failed" and row.title is None and row.summary is None
    assert len(_writer_calls(db_session_factory)) == config.limits.max_calls_per_item
    assert cli.main(["run-night"]) == 0  # cola vacía: llamar de nuevo fallaría
    assert len(_writer_calls(db_session_factory)) == config.limits.max_calls_per_item


def test_la_semana_en_curso_no_se_redacta(db_session_factory, fake, monkeypatch) -> None:
    _config(monkeypatch, digest_enabled=True)
    with unit_of_work(db_session_factory) as session:
        repo = SqlAlchemyArchiveRepository(session)
        # snapshot del lunes de la W42 (12 de octubre): la semana sigue abierta el 14
        save(repo, snapshot(offset_days=11, rows=1), [solution("A b", "Ref A", is_default=True)])

    assert cli.main(["run-night"]) == 0

    assert _digest_rows(db_session_factory) == [] and _writer_calls(db_session_factory) == []
