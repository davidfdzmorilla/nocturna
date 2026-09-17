"""Tests de `nocturna.cli::main` para `run-night` sin `--dry-run`, contra PostgreSQL real.

T44, paso 3: `run-night` sin `--dry-run` deja de devolver `2` y ejecuta la
noche completa (`_run_night_for_real`). Mismas costuras que
`tests/db/test_cli_run_item.py`: `AgentSDKProvider` se sustituye parcheando
`nocturna.infrastructure.llm.agent_sdk_provider.AgentSDKProvider` (import
perezoso de `_run_night_for_real`), `SystemClock` se sustituye parcheando
`cli.SystemClock` (`system_clock_from_config` lo resuelve en el momento de
la llamada), y la ingesta arXiv se sustituye sirviendo `httpx.MockTransport`
en vez de tocar la red de verdad (`_patch_arxiv_transport`, igual que
`tests/db/test_cli_dry_run_db.py`) -- `feed_empty.xml` para no interferir
con los ítems que cada test siembra a mano.

Códigos de salida cubiertos aquí (`cli.py`, docstring de
`_run_night_for_real`): `0` completed, `7` partial, `8` killed, `1`
excepción escapada de `RunNight` (cerrada `FAILED`, sin relanzar). Los de
`run-item`, 1-6, no se tocan (ver `test_cli_run_item.py`).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pytest
from factories import make_item
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from sqlalchemy import select

from nocturna import cli
from nocturna.application.budget import effective_nightly_tokens
from nocturna.cli import main
from nocturna.domain.entities import ItemStatus, Run, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import ItemRow, RunRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"
_MADRID = ZoneInfo("Europe/Madrid")
# `config/pipeline.toml`: window.start=00:00, window.hard_stop=04:45,
# window.timezone=Europe/Madrid (ver tests/db/test_cli_run_item.py, mismo
# patrón, mismos comentarios sobre por qué la zona real no importa aquí).
_WITHIN_WINDOW = datetime(2026, 1, 15, 2, 0, tzinfo=_MADRID)
_OUTSIDE_WINDOW = datetime(2026, 1, 15, 12, 0, tzinfo=_MADRID)


def _empty_feed_response() -> httpx.Response:
    return httpx.Response(200, content=(FIXTURES_DIR / "feed_empty.xml").read_bytes())


def _patch_empty_arxiv_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Sirve `feed_empty.xml` a cualquier petición arXiv, sin tocar la red.

    Mismo patrón que `tests/db/test_cli_dry_run_db.py::_patch_arxiv_transport`
    pero fijo a una respuesta vacía: estos tests siembran los `Item` que
    necesitan a mano, y no quieren que la ingesta real de la noche añada
    ítems inesperados a la mezcla.
    """
    real_async_client = httpx.AsyncClient

    def _fake_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(lambda request: _empty_feed_response())
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client)


class _ClockStub:
    """Sustituye a `SystemClock`, igual que `test_cli_run_item.py::_ClockStub`."""

    def __init__(self, fixed_now: datetime) -> None:
        self._fixed_now = fixed_now

    def __call__(self, *_args: object, **_kwargs: object) -> FakeClock:
        return FakeClock(self._fixed_now)


def _valid_reading_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "summary": "Resumen generado por el FakeLLMProvider.",
        "objects": ["NGC 1234"],
        "claims": ["Una afirmación de prueba."],
        "interest_score": 5,
    }
    defaults.update(overrides)
    return defaults


def _valid_popularizer_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "title": "Un titular generado por el FakeLLMProvider",
        "level_curious": "Nivel curioso de prueba.",
        "level_amateur": "Nivel aficionado de prueba.",
        "level_technical": "Nivel técnico de prueba.",
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


@pytest.fixture(autouse=True)
def _within_window_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "SystemClock", _ClockStub(_WITHIN_WINDOW))


@pytest.fixture(autouse=True)
def _empty_arxiv_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_empty_arxiv_feed(monkeypatch)


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> FakeLLMProvider:
    fake = FakeLLMProvider()
    monkeypatch.setattr(agent_sdk_provider, "AgentSDKProvider", lambda: fake)
    return fake


def _seed_item(db_session_factory, **overrides: object) -> UUID:
    item = make_item(**overrides)
    with unit_of_work(db_session_factory) as session:
        added = SqlAlchemyItemRepository(session).add_many([item])
    assert added == 1
    return item.id


def _seed_running_run(db_session_factory, *, started_at: datetime, budget_tokens: int) -> UUID:
    run = Run(started_at=started_at, budget_tokens=budget_tokens)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyRunRepository(session).add(run)
    return run.id


@contextmanager
def _read_session(db_session_factory) -> Generator[Any, None, None]:
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def _all_runs(db_session_factory) -> list[RunRow]:
    with _read_session(db_session_factory) as session:
        return list(session.execute(select(RunRow)).scalars().all())


def _item_status(db_session_factory, item_id: UUID) -> ItemStatus:
    with _read_session(db_session_factory) as session:
        row = session.get(ItemRow, item_id)
        assert row is not None
        return row.status


# --- Camino feliz: código 0, Run completed con contadores y notas ----------


def test_camino_feliz_devuelve_0_y_cierra_el_run_completed_con_contadores(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
    capsys: pytest.CaptureFixture[str],
) -> None:
    item_id = _seed_item(db_session_factory, status=ItemStatus.NEW)
    fake_provider.respond(
        AgentRole.READER, json=_valid_reading_json(), tokens_in=1000, tokens_out=200
    )
    fake_provider.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=800, tokens_out=150
    )
    fake_provider.respond(
        AgentRole.EDITOR,
        json={
            "publish": [{"item_id": str(item_id), "confidence": 0.8, "reason": "Motivo de prueba."}]
        },
        tokens_in=1200,
        tokens_out=100,
    )

    code = main(["run-night"])

    captured = capsys.readouterr()
    assert code == 0
    assert "Resumen de la noche" in captured.out
    assert "estado: completed" in captured.out

    assert _item_status(db_session_factory, item_id) is ItemStatus.PUBLISHED

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1
    run = runs[0]
    assert run.status is RunStatus.COMPLETED
    assert run.finished_at is not None
    assert run.items_read == 1
    assert run.findings_published == 1
    assert run.notes != ""


def test_camino_feliz_budget_tokens_es_el_presupuesto_efectivo_no_un_literal(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """Cierra la decisión abierta nº 57 de `docs/OPEN_DECISIONS.md`: el
    `Run` que `run-night` crea usa `effective_nightly_tokens(policy, now)`,
    nunca `policy.nightly_tokens` a pelo ni un literal escrito a mano."""
    config = load_pipeline_config()
    policy = cli.budget_policy_from_config(config)
    expected = effective_nightly_tokens(policy, _WITHIN_WINDOW.astimezone(UTC))

    code = main(["run-night"])

    assert code == 0
    runs = _all_runs(db_session_factory)
    assert len(runs) == 1
    assert runs[0].budget_tokens == expected


# --- Run huérfano: se cierra KILLED, se abre uno nuevo ---------------------


def test_run_huerfano_running_se_cierra_killed_y_se_abre_uno_nuevo(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    orphan_id = _seed_running_run(
        db_session_factory, started_at=_WITHIN_WINDOW, budget_tokens=300_000
    )

    code = main(["run-night"])

    assert code == 0, "sin ítems que leer, la noche cierra completed (NO_CANDIDATES)"
    assert fake_provider.calls == [], "sin candidatos, ningún agente se llama"

    runs = _all_runs(db_session_factory)
    assert len(runs) == 2, "el huérfano se cierra y run-night abre el suyo propio"
    by_id = {run.id: run for run in runs}

    orphan = by_id[orphan_id]
    assert orphan.status is RunStatus.KILLED
    assert orphan.finished_at is not None
    assert "quedó RUNNING de un proceso anterior" in orphan.notes

    new_run = next(run for run in runs if run.id != orphan_id)
    assert new_run.status is RunStatus.COMPLETED
    assert new_run.finished_at is not None


# --- Excepción escapada de RunNight: código 1, Run failed, sin relanzar ----


def test_excepcion_de_run_night_devuelve_1_y_cierra_el_run_failed(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Ningún camino de `_run_night_for_real` debe dejar escapar la
    excepción de `RunNight` sin cerrar el Run: se cierra `FAILED` aquí
    mismo, con el traceback en el log JSON (por `stderr`, vía
    `configure_json_logging`), y `main()` devuelve `1` en vez de propagar
    la excepción (a diferencia de `run-item`, ver el docstring del módulo)."""

    class _BoomRunNight:
        def __init__(self, **_kwargs: object) -> None:
            pass

        async def __call__(self):
            raise RuntimeError("fallo inyectado para el test")

    monkeypatch.setattr(cli, "RunNight", _BoomRunNight)
    # `alembic/env.py::run_migrations_online` llama a `fileConfig(...)` con
    # `disable_existing_loggers=True` (su valor por defecto) al migrar el
    # esquema de `nocturna_test` (fixture `db_session_factory`): deshabilita
    # cualquier logger `nocturna.*` no declarado en `alembic.ini`, incluido
    # `nocturna.cli`. Deuda documentada en `docs/TECHNICAL_DEBT.md` (T41/T42);
    # mismo parcheo que ya usan `test_read_item.py`/`test_agent_runner.py`
    # para sus propios `_logger`.
    monkeypatch.setattr(cli._logger, "disabled", False)

    code = main(["run-night"])

    captured = capsys.readouterr()
    assert code == 1
    assert fake_provider.calls == []

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1
    assert runs[0].status is RunStatus.FAILED
    assert runs[0].finished_at is not None

    # El traceback va al log JSON (stderr), no se traga en silencio.
    assert "night.failed" in captured.err or "RuntimeError" in captured.err


# --- Presupuesto agotado en el Popularizer: código 7, Run partial ----------


def test_presupuesto_agotado_en_el_popularizer_devuelve_7_y_cierra_el_run_partial(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """Único camino de esta suite que deja `RunNightResult.status` en
    `PARTIAL` de punta a punta (el paso 3 lo dejó sin cubrir). Con la
    configuración real (`config/pipeline.toml`: `nightly_tokens=300_000`,
    `editor_reserve_tokens=60_000`, `popularizer_estimated_tokens=7_000`),
    el fondo disponible para Reader+Popularizer es `300_000 - 60_000 =
    240_000`. Un Reader que gasta de verdad `235_000` tokens -- autorizado
    porque `authorize` solo comprueba `reader_estimated_tokens=6_000` ANTES
    de la llamada, nunca el gasto real -- deja solo `5_000` disponibles,
    por debajo de los `7_000` que el Popularizer necesita: `BudgetGuard` lo
    deniega con `BUDGET_EXHAUSTED`, que
    `terminal_status_for` traduce a `PARTIAL` -- no `OUTSIDE_WINDOW`, así
    que `RunNight` no marca `_skip_editor`, y la noche sigue hacia el
    Editor. Sin ningún `Finding` candidato (el Popularizer nunca llegó a
    producir uno), el Editor resuelve `NO_CANDIDATES` sin autorizar
    nada -- ni una respuesta que programarle a `fake_provider` para el rol
    `editor` -- y ese desenlace de éxito no sube de severidad un `PARTIAL`
    ya fijado (`RunNight._degrade`, modelo de degradación monótona)."""
    item_id = _seed_item(db_session_factory, status=ItemStatus.NEW)
    fake_provider.respond(
        AgentRole.READER,
        json=_valid_reading_json(interest_score=5),
        tokens_in=235_000,
        tokens_out=0,
    )

    code = main(["run-night"])

    assert code == 7
    assert len(fake_provider.calls) == 1, (
        "BudgetGuard deniega al Popularizer antes de llamarlo; el Editor, con cero "
        "candidatos, tampoco llega a llamarse"
    )
    assert fake_provider.calls[0].role is AgentRole.READER

    assert _item_status(db_session_factory, item_id) is ItemStatus.READ, (
        "el Reader dejó el Item READ; la denegación de presupuesto del Popularizer no lo "
        "toca -- ni PUBLISHED ni DISCARDED"
    )

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1
    run = runs[0]
    assert run.status is RunStatus.PARTIAL
    assert run.finished_at is not None
    assert run.items_read == 1
    assert run.findings_published == 0


# --- Fuera de ventana desde el arranque: código 8, Run killed --------------


def test_fuera_de_la_ventana_devuelve_8_y_cierra_el_run_killed(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """Único camino de esta suite que deja `RunNightResult.status` en
    `KILLED` de punta a punta (el paso 3 lo dejó sin cubrir). Con el reloj
    fuera de `window.hard_stop` desde el primer instante
    (`_OUTSIDE_WINDOW`), `_current_or_new_run_night` igualmente abre el
    `Run` (no comprueba la ventana por sí sola), pero el primer intento de
    autorizar al Reader sobre el único `Item` sembrado deniega con
    `OUTSIDE_WINDOW` antes de llamar al proveedor: `terminal_status_for`
    traduce ese motivo a `KILLED` (corte incondicional, `CLAUDE.md`) y
    `RunNight` marca `self._skip_editor = True`, así que ni el Popularizer
    ni el Editor se llegan a intentar -- `fake_provider.calls` se queda
    vacío del todo."""
    monkeypatch.setattr(cli, "SystemClock", _ClockStub(_OUTSIDE_WINDOW))
    item_id = _seed_item(db_session_factory, status=ItemStatus.NEW)

    code = main(["run-night"])

    assert code == 8
    assert fake_provider.calls == [], (
        "BudgetGuard deniega al Reader por OUTSIDE_WINDOW antes de llamar al proveedor"
    )
    assert _item_status(db_session_factory, item_id) is ItemStatus.NEW, (
        "una denegación de BudgetGuard no toca el Item"
    )

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1
    run = runs[0]
    assert run.status is RunStatus.KILLED
    assert run.finished_at is not None


# --- --dry-run: sin Run, sin llamada a ningún agente ------------------------


def test_dry_run_no_crea_run_ni_llama_a_ningun_agente(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """`--dry-run` sigue sin crear ningún `Run` ni invocar a ningún agente
    tras T44 paso 3: solo ingesta (servida aquí por `feed_empty.xml`, vía
    `_empty_arxiv_feed`, autouse) y el plan de gasto textual de
    `_run_night_dry_run`. `AgentSDKProvider` está sustituido por
    `fake_provider` en todo este fichero, así que una llamada real
    dejaría constancia en `fake_provider.calls` -- se queda vacío. El
    comportamiento de que `claude_agent_sdk` no entra en `sys.modules`
    durante `--dry-run` es un test aparte, en subproceso
    (`tests/db/test_cli_dry_run_db.py::test_claude_agent_sdk_no_se_importa_durante_dry_run`,
    T20): no se toca ni se duplica aquí."""
    code = main(["run-night", "--dry-run"])

    assert code == 0
    assert fake_provider.calls == []
    assert _all_runs(db_session_factory) == []
