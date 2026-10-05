"""T82: `run-item <id> --reader v3 --force` contra PostgreSQL (`nocturna_test`).

Mismo montaje que `test_cli_run_item.py`: `AgentSDKProvider` sustituido por un
`FakeLLMProvider` y reloj fijo. Ningún test llama a Claude.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from factories import make_finding, make_item, make_reading
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider

from nocturna import cli
from nocturna.application.budget import DenyReason, terminal_status_for
from nocturna.cli import main
from nocturna.domain.entities import ItemStatus, Run, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.models import AgentCallRow, FindingRow, ItemRow, ReadingRow, RunRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider

_MADRID = ZoneInfo("Europe/Madrid")
_WITHIN = datetime(2026, 1, 15, 2, 0, tzinfo=_MADRID)
_OUTSIDE = datetime(2026, 1, 15, 12, 0, tzinfo=_MADRID)
_ABSTRACT = "The planet Kepler-0000 b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."


class _ClockStub:
    def __init__(self, fixed_now: datetime) -> None:
        self._fixed_now = fixed_now

    def __call__(self, *_a: object, **_k: object) -> FakeClock:
        return FakeClock(self._fixed_now)


def _v3_json() -> dict:
    return {
        "summary": "Resumen v3 del FakeLLMProvider.",
        "objects": ["Kepler-0000"],
        "claims": ["Una afirmación."],
        "interest_score": 4,
        "measurements": [
            {
                "planet_name": "Kepler-0000 b",
                "parameter": "mass",
                "value": 2.8,
                "err_plus": 0.5,
                "err_minus": 0.5,
                "unit": "M_jup",
                "limit": "none",
                "origin": "this_work",
                "evidence": "2.8 (+0.5/-0.5) M_jup",
            }
        ],
    }


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


@pytest.fixture(autouse=True)
def _within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "SystemClock", _ClockStub(_WITHIN))


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> FakeLLMProvider:
    fake = FakeLLMProvider()
    monkeypatch.setattr(agent_sdk_provider, "AgentSDKProvider", lambda: fake)
    return fake


def _seed(factory, *, status: ItemStatus = ItemStatus.DISCARDED, reading: bool = True, **item_kw):
    item_kw.setdefault("exoplanet_match", True)
    item_kw.setdefault("abstract", _ABSTRACT)
    item = make_item(status=status, **item_kw)
    previous = make_reading(item.id, prompt_version="reader-v2") if reading else None
    with unit_of_work(factory) as session:
        SqlAlchemyItemRepository(session).add_many([item])
        session.flush()
        if previous is not None:
            SqlAlchemyReadingRepository(session).add(previous)
    return item.id, previous


def _rows(factory, model):
    with factory() as session:
        return list(session.execute(sa.select(model)).scalars().all())


def _status(factory, item_id: UUID) -> ItemStatus:
    with factory() as session:
        return session.get(ItemRow, item_id).status


def _argv(item_id: UUID) -> list[str]:
    return ["run-item", str(item_id), "--reader", "v3", "--force"]


def test_camino_feliz_devuelve_0_sustituye_la_lectura_y_no_toca_findings_ni_estado(
    db_session_factory, fake_provider
):
    item_id, previous = _seed(db_session_factory, status=ItemStatus.PUBLISHED)
    with unit_of_work(db_session_factory) as session:
        run = Run(started_at=_WITHIN, budget_tokens=1)
        SqlAlchemyRunRepository(session).add(run)
        session.flush()
        finding = make_finding(item_id, run.id, title="Publicado")
        SqlAlchemyFindingRepository(session).add(finding)
        run.finish(RunStatus.COMPLETED, _WITHIN)
        SqlAlchemyRunRepository(session).save(run)
    before_findings = [
        (f.id, f.title, f.published_at) for f in _rows(db_session_factory, FindingRow)
    ]
    fake_provider.respond(AgentRole.READER, json=_v3_json(), tokens_in=1500, tokens_out=300)

    code = main(_argv(item_id))

    assert code == 0
    assert len(fake_provider.calls) == 1
    assert fake_provider.calls[0].role is AgentRole.READER
    assert _status(db_session_factory, item_id) is ItemStatus.PUBLISHED
    after_findings = [
        (f.id, f.title, f.published_at) for f in _rows(db_session_factory, FindingRow)
    ]
    assert after_findings == before_findings
    readings = _rows(db_session_factory, ReadingRow)
    assert len(readings) == 2
    current = [r for r in readings if r.superseded_at is None]
    old = [r for r in readings if r.superseded_at is not None]
    assert len(current) == 1 and len(old) == 1
    assert old[0].id == previous.id and current[0].prompt_version == "reader-v3"
    calls = _rows(db_session_factory, AgentCallRow)
    assert [c.agent for c in calls] == [AgentRole.READER]
    assert calls[0].prompt_version == "reader-v3" and calls[0].item_id == item_id
    runs = [r for r in _rows(db_session_factory, RunRow) if r.id == calls[0].run_id]
    assert len(runs) == 1
    assert runs[0].status is RunStatus.COMPLETED and "reread" in runs[0].notes
    assert runs[0].finished_at is not None


@pytest.mark.parametrize("extra", [["--force"], ["--reader", "v3"]])
def test_banderas_sueltas_devuelven_2(db_session_factory, fake_provider, extra):
    item_id, _ = _seed(db_session_factory)
    with pytest.raises(SystemExit) as exc_info:
        main(["run-item", str(item_id), *extra])
    assert exc_info.value.code == 2
    assert fake_provider.calls == []
    assert _rows(db_session_factory, RunRow) == []


def test_fuera_de_ventana_devuelve_4_sin_run_ni_agentcall(
    monkeypatch, db_session_factory, fake_provider
):
    monkeypatch.setattr(cli, "SystemClock", _ClockStub(_OUTSIDE))
    item_id, _ = _seed(db_session_factory)

    code = main(_argv(item_id))

    assert code == 4
    assert fake_provider.calls == []
    assert _rows(db_session_factory, RunRow) == []
    assert _rows(db_session_factory, AgentCallRow) == []
    assert len(_rows(db_session_factory, ReadingRow)) == 1


def test_run_running_devuelve_9_sin_gastar_y_sin_tocar_el_run(db_session_factory, fake_provider):
    item_id, _ = _seed(db_session_factory)
    running = Run(started_at=_WITHIN, budget_tokens=300_000)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyRunRepository(session).add(running)

    code = main(_argv(item_id))

    assert code == 9
    assert fake_provider.calls == []
    assert _rows(db_session_factory, AgentCallRow) == []
    (run_row,) = _rows(db_session_factory, RunRow)
    assert run_row.id == running.id and run_row.status is RunStatus.RUNNING
    assert run_row.finished_at is None and run_row.notes == ""


@pytest.mark.parametrize(
    "case", ["inexistente", "new", "failed", "categoria", "sin_match", "con_medidas"]
)
def test_no_elegible_devuelve_3_sin_run(db_session_factory, fake_provider, case):
    if case == "inexistente":
        item_id = uuid4()
    elif case == "new":
        item_id, _ = _seed(db_session_factory, status=ItemStatus.NEW, reading=False)
    elif case == "failed":
        item_id, _ = _seed(db_session_factory, status=ItemStatus.FAILED, reading=False)
    elif case == "categoria":
        item_id, _ = _seed(db_session_factory, categories=["astro-ph.GA"])
    elif case == "sin_match":
        item_id, _ = _seed(db_session_factory, exoplanet_match=False)
    else:
        item = make_item(status=ItemStatus.READ, exoplanet_match=True, abstract=_ABSTRACT)
        with unit_of_work(db_session_factory) as session:
            SqlAlchemyItemRepository(session).add_many([item])
            session.flush()
            SqlAlchemyReadingRepository(session).add(make_reading(item.id, measurements=()))
        item_id = item.id

    code = main(_argv(item_id))

    assert code == 3
    assert fake_provider.calls == []
    assert _rows(db_session_factory, RunRow) == []
    assert _rows(db_session_factory, AgentCallRow) == []


def test_json_invalido_devuelve_1_run_partial_y_la_previa_sigue_vigente(
    db_session_factory, fake_provider
):
    item_id, previous = _seed(db_session_factory, status=ItemStatus.READ)
    fake_provider.respond(AgentRole.READER, raw="roto", tokens_in=100, tokens_out=10)
    fake_provider.respond(AgentRole.READER, raw="roto", tokens_in=100, tokens_out=10)

    code = main(_argv(item_id))

    assert code == 1
    assert _status(db_session_factory, item_id) is ItemStatus.READ
    (reading,) = _rows(db_session_factory, ReadingRow)
    assert reading.id == previous.id and reading.superseded_at is None
    assert len(_rows(db_session_factory, AgentCallRow)) == 2
    (run,) = _rows(db_session_factory, RunRow)
    assert run.status is RunStatus.PARTIAL and "reread" in run.notes


def test_run_item_sin_banderas_sobre_item_no_new_sigue_saliendo_con_3(
    db_session_factory, fake_provider
):
    item_id, _ = _seed(db_session_factory, status=ItemStatus.READ)

    code = main(["run-item", str(item_id)])

    assert code == 3
    assert fake_provider.calls == []
    assert len(_rows(db_session_factory, ReadingRow)) == 1


class _FlippingClock:
    """Reloj dentro de ventana hasta que se llama a `flip()` (cruza `hard_stop`)."""

    def __init__(self) -> None:
        self._now = _WITHIN

    def now(self) -> datetime:
        return self._now

    def flip(self) -> None:
        self._now = _OUTSIDE


def _only_run(factory) -> RunRow:
    (run,) = _rows(factory, RunRow)
    return run


def test_presupuesto_insuficiente_devuelve_4_y_cierra_el_run_segun_terminal_status(
    monkeypatch, db_session_factory, fake_provider
):
    monkeypatch.setattr(cli, "effective_nightly_tokens", lambda *_a, **_k: 5_000)
    item_id, previous = _seed(db_session_factory, status=ItemStatus.READ)

    code = main(_argv(item_id))

    assert code == 4
    assert fake_provider.calls == []
    run = _only_run(db_session_factory)
    assert run.status is terminal_status_for(DenyReason.BUDGET_EXHAUSTED)
    assert run.status is RunStatus.PARTIAL and run.finished_at is not None
    (reading,) = _rows(db_session_factory, ReadingRow)
    assert reading.id == previous.id and reading.superseded_at is None


def test_cruzar_hard_stop_entre_comprobacion_y_authorize_devuelve_4_y_run_killed(
    monkeypatch, db_session_factory, fake_provider
):
    clock = _FlippingClock()
    monkeypatch.setattr(cli, "SystemClock", lambda *_a, **_k: clock)
    original = cli._new_run_unless_running

    def _open_then_cross_hard_stop(*args, **kwargs):
        run_id = original(*args, **kwargs)
        clock.flip()
        return run_id

    monkeypatch.setattr(cli, "_new_run_unless_running", _open_then_cross_hard_stop)
    item_id, _ = _seed(db_session_factory, status=ItemStatus.READ)

    code = main(_argv(item_id))

    assert code == 4
    assert fake_provider.calls == []
    run = _only_run(db_session_factory)
    assert run.status is terminal_status_for(DenyReason.OUTSIDE_WINDOW)
    assert run.status is RunStatus.KILLED and run.finished_at is not None


@pytest.mark.parametrize("boom", [RuntimeError("fallo inesperado"), KeyboardInterrupt()])
def test_excepcion_inesperada_cierra_el_run_failed_y_se_propaga(
    monkeypatch, db_session_factory, fake_provider, boom
):
    def _raise(*_a: object, **_k: object) -> None:
        raise boom

    monkeypatch.setattr(SqlAlchemyReadingRepository, "supersede", _raise)
    item_id, _ = _seed(db_session_factory, status=ItemStatus.READ)
    fake_provider.respond(AgentRole.READER, json=_v3_json(), tokens_in=1500, tokens_out=300)

    with pytest.raises(type(boom)):
        main(_argv(item_id))

    run = _only_run(db_session_factory)
    assert run.status is RunStatus.FAILED and run.finished_at is not None


def test_avisa_por_stderr_de_que_abre_un_run_con_presupuesto_completo(
    db_session_factory, fake_provider, capsys
):
    item_id, _ = _seed(db_session_factory, status=ItemStatus.READ)
    fake_provider.respond(AgentRole.READER, json=_v3_json(), tokens_in=1500, tokens_out=300)

    assert main(_argv(item_id)) == 0

    err = capsys.readouterr().err
    assert "abre un Run nuevo" in err
    assert "presupuesto de noche completo" in err
    assert "no está acotado por nightly_tokens" in err


def test_carrera_por_uq_runs_status_running_devuelve_9_sin_traza(
    monkeypatch, db_session_factory, fake_provider
):
    item_id, _ = _seed(db_session_factory, status=ItemStatus.READ)
    running = Run(started_at=_WITHIN, budget_tokens=300_000)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyRunRepository(session).add(running)
    # Simula que el Run ajeno aparece justo después de `current()`.
    monkeypatch.setattr(SqlAlchemyRunRepository, "current", lambda self: None)

    code = main(_argv(item_id))

    assert code == 9
    assert fake_provider.calls == []
    (run_row,) = _rows(db_session_factory, RunRow)
    assert run_row.id == running.id and run_row.status is RunStatus.RUNNING
