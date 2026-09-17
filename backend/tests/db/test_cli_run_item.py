"""Tests de `nocturna.cli::main` para `run-item <item_id>`, contra PostgreSQL real.

`cli.py` construye su propio `AgentSDKProvider` dentro de `_run_item`, con un
import perezoso a nivel de función (`from
nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider`, ver
docstring de `cli.py`, "T41, paso 8"): no hay ningún parámetro de `main()`
para sustituirlo. La costura disponible sin tocar `cli.py` es parchear el
nombre `AgentSDKProvider` en el módulo `agent_sdk_provider` *antes* de que
ese import se ejecute -- el import perezoso resuelve el atributo en el
momento de la llamada, no en el momento en que Python compiló `cli.py`, así
que ve el parche. `fake_provider`, más abajo, hace exactamente eso con un
`FakeLLMProvider` (`tests/fakes/llm.py`): ninguna llamada de este fichero
llega a Claude de verdad. Como cinturón sobre tirantes, la guarda
`_no_claude` de `tests/conftest.py` (autouse, no marcada `manual`) también
bloquea el transporte real de `claude_agent_sdk` para todo este fichero.

Tampoco hay ningún parámetro para inyectar un reloj falso: `_run_item`
construye su propio `SystemClock` vía `cli.system_clock_from_config`,
resuelto contra la hora real del sistema. `_ClockStub`, más abajo, parchea
`cli.SystemClock` -- el nombre que `system_clock_from_config` resuelve en el
momento de la llamada -- por un doble que ignora la zona horaria real y
siempre devuelve un `FakeClock` fijo: así los tests son deterministas sin
importar a qué hora del día real se lancen, el mismo problema que documenta
`tests/manual/test_sdk_smoke.py` en su punto (d).

Como en `tests/db/test_cli_dry_run_db.py`, esto obliga a los tests a correr
contra `nocturna_test` (fixtures de `tests/db/conftest.py`), porque
`run-item` persiste de verdad: crea/reutiliza un `Run`, autoriza contra
`BudgetGuard`, y guarda `Reading`/`AgentCall`/el nuevo estado del `Item`.

Códigos de salida cubiertos aquí (`cli.py`, docstring de `_run_item`):
`0` éxito · `1` lectura fallida (no ejercitado aquí: ver `test_read_item.py`
para los escenarios de `LLMError`/JSON inválido, que no dependen del CLI) ·
`2` UUID mal formado · `3` ítem inexistente o ya leído · `4` denegación de
`BudgetGuard` (aquí, fuera de ventana).
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from factories import make_item
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from sqlalchemy import select

from nocturna import cli
from nocturna.cli import main
from nocturna.domain.entities import AgentCallStatus, ItemStatus, Run, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.models import AgentCallRow, ItemRow, ReadingRow, RunRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider

_MADRID = ZoneInfo("Europe/Madrid")
# `config/pipeline.toml`: window.start=00:00, window.hard_stop=04:45,
# window.timezone=Europe/Madrid. `is_within_window` compara la hora de
# pared del `datetime` recibido, con independencia de su `tzinfo` (ver
# `application/budget.py`), así que basta con que el campo `hour`/`minute`
# caiga dentro/fuera del rango; se fija además la zona real de la
# configuración por higiene, no porque la comparación la use.
_WITHIN_WINDOW = datetime(2026, 1, 15, 2, 0, tzinfo=_MADRID)
_OUTSIDE_WINDOW = datetime(2026, 1, 15, 12, 0, tzinfo=_MADRID)


class _ClockStub:
    """Sustituye a `SystemClock`: ignora los argumentos reales y devuelve `fixed_now`.

    `cli.system_clock_from_config` llama `SystemClock(ZoneInfo(...))`;
    parcheando el nombre `SystemClock` en el módulo `cli` por una instancia
    de esta clase, esa llamada cae aquí (`__call__`) y siempre entrega el
    mismo `FakeClock`, sin tocar `datetime.now()`.
    """

    def __init__(self, fixed_now: datetime) -> None:
        self._fixed_now = fixed_now

    def __call__(self, *_args: object, **_kwargs: object) -> FakeClock:
        return FakeClock(self._fixed_now)


def _valid_reading_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "summary": "Resumen generado por el FakeLLMProvider.",
        "objects": ["NGC 1234"],
        "claims": ["Una afirmación de prueba."],
        "interest_score": 4,
    }
    defaults.update(overrides)
    return defaults


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    """Apunta la `Settings()` que construye `main()` a `nocturna_test`, como
    hace `tests/db/test_cli_dry_run_db.py::_use_test_database`."""
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


@pytest.fixture(autouse=True)
def _within_window_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reloj fijo dentro de la ventana de ejecución, por defecto.

    El único test que necesita otra hora (`test_fuera_de_la_ventana...`)
    sobrescribe este parcheo con su propio `monkeypatch.setattr(cli, ...)`
    dentro del cuerpo del test; `monkeypatch` apila las sustituciones, así
    que la última en aplicarse gana.
    """
    monkeypatch.setattr(cli, "SystemClock", _ClockStub(_WITHIN_WINDOW))


@pytest.fixture
def fake_provider(monkeypatch: pytest.MonkeyPatch) -> FakeLLMProvider:
    """Sustituye `AgentSDKProvider` por un `FakeLLMProvider` compartido.

    Ver docstring del módulo: parchea el nombre en
    `infrastructure.llm.agent_sdk_provider`, que es donde `_run_item` lo
    resuelve con su import perezoso. La misma instancia se reutiliza en
    todas las invocaciones de `main(["run-item", ...])` dentro de un test
    (por ejemplo, dos ejecuciones sucesivas sobre el mismo ítem), así que
    `fake.calls` acumula across ambas y sirve para afirmar cuántas llamadas
    reales al Reader hubo en total.
    """
    fake = FakeLLMProvider()
    monkeypatch.setattr(agent_sdk_provider, "AgentSDKProvider", lambda: fake)
    return fake


def _seed_item(db_session_factory, **overrides: object) -> UUID:
    item = make_item(**overrides)
    with unit_of_work(db_session_factory) as session:
        added = SqlAlchemyItemRepository(session).add_many([item])
    assert added == 1
    return item.id


@contextmanager
def _read_session(db_session_factory) -> Generator[object, None, None]:
    session = db_session_factory()
    try:
        yield session
    finally:
        session.close()


def _all_runs(db_session_factory) -> list[RunRow]:
    with _read_session(db_session_factory) as session:
        return list(session.execute(select(RunRow)).scalars().all())


def _all_agent_calls(db_session_factory) -> list[AgentCallRow]:
    with _read_session(db_session_factory) as session:
        return list(session.execute(select(AgentCallRow)).scalars().all())


def _all_readings(db_session_factory) -> list[ReadingRow]:
    with _read_session(db_session_factory) as session:
        return list(session.execute(select(ReadingRow)).scalars().all())


def _item_status(db_session_factory, item_id: UUID) -> ItemStatus:
    with _read_session(db_session_factory) as session:
        row = session.get(ItemRow, item_id)
        assert row is not None
        return row.status


def _seed_running_run(db_session_factory, *, started_at: datetime, budget_tokens: int) -> UUID:
    run = Run(started_at=started_at, budget_tokens=budget_tokens)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyRunRepository(session).add(run)
    return run.id


# --- UUID mal formado: código 2 -------------------------------------------


def test_uuid_mal_formado_devuelve_2(db_session_factory: object) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["run-item", "esto-no-es-un-uuid"])

    assert exc_info.value.code == 2
    assert _all_runs(db_session_factory) == [], (
        "un UUID mal formado lo rechaza argparse antes de que _run_item cree ningún Run"
    )


# --- Ítem inexistente: código 3, sin AgentCall, Run cerrado partial --------


def test_item_inexistente_devuelve_3_sin_agent_call_y_cierra_el_run_como_partial(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    code = main(["run-item", str(uuid4())])

    assert code == 3
    assert fake_provider.calls == []
    assert _all_agent_calls(db_session_factory) == []
    assert _all_readings(db_session_factory) == []

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1, "run-item crea un Run cuando no había ninguno RUNNING"
    run = runs[0]
    assert run.status is RunStatus.PARTIAL, (
        "sin Reading que mostrar, el Run que este proceso creó se cierra 'partial', "
        "nunca 'completed'"
    )
    assert run.finished_at is not None


# --- Ítem ya leído (segunda ejecución sobre el mismo id): código 3 --------


def test_item_ya_leido_en_segunda_ejecucion_devuelve_3_sin_integrity_error_ni_llamada(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """Sin la guarda de estado de `ReadItem` (item.status is NEW antes de
    autorizar nada), una segunda ejecución de `run-item` sobre el mismo id
    intentaría escribir una segunda `Reading` para el mismo `item_id` y
    chocaría con `uq_readings_item_id` (`IntegrityError`). La guarda dispara
    antes de tocar el presupuesto o el proveedor, así que ese índice nunca
    llega a alcanzarse -- exactamente lo que `OPEN_DECISIONS.md` fija como
    motivo para no resolver la unicidad de otra forma."""
    fake_provider.respond(
        AgentRole.READER, json=_valid_reading_json(), tokens_in=1200, tokens_out=300
    )
    item_id = _seed_item(db_session_factory)

    first_code = main(["run-item", str(item_id)])
    assert first_code == 0
    assert len(fake_provider.calls) == 1
    assert len(_all_readings(db_session_factory)) == 1

    second_code = main(["run-item", str(item_id)])  # no debe lanzar IntegrityError

    assert second_code == 3
    assert len(fake_provider.calls) == 1, "la segunda ejecución no debe llamar al proveedor"
    assert len(_all_readings(db_session_factory)) == 1, "ninguna Reading duplicada"
    assert len(_all_agent_calls(db_session_factory)) == 1, "ningún AgentCall nuevo"
    assert _item_status(db_session_factory, item_id) is ItemStatus.READ

    runs = _all_runs(db_session_factory)
    assert len(runs) == 2, "cada invocación sin un Run RUNNING previo crea uno nuevo"
    statuses = {run.status for run in runs}
    assert statuses == {RunStatus.COMPLETED, RunStatus.PARTIAL}


# --- Fuera de la ventana horaria: código 4 ---------------------------------


def test_fuera_de_la_ventana_horaria_devuelve_4(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    monkeypatch.setattr(cli, "SystemClock", _ClockStub(_OUTSIDE_WINDOW))
    item_id = _seed_item(db_session_factory)

    code = main(["run-item", str(item_id)])

    assert code == 4
    assert fake_provider.calls == [], "BudgetGuard deniega antes de llamar al proveedor"
    assert _all_agent_calls(db_session_factory) == []
    assert _all_readings(db_session_factory) == []
    assert _item_status(db_session_factory, item_id) is ItemStatus.NEW, (
        "una denegación de BudgetGuard no toca el Item: ni mark_read() ni mark_failed()"
    )

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1, "run-item crea un Run cuando no había ninguno RUNNING"
    run = runs[0]
    assert run.status is RunStatus.KILLED, (
        "OUTSIDE_WINDOW se cierra con terminal_status_for(exc.reason) == KILLED, no "
        "PARTIAL: pasar de hard_stop es un corte incondicional (CLAUDE.md), no una "
        "noche que se queda corta por otro motivo -- T60 los mirará por separado "
        "(application/budget.py::terminal_status_for)"
    )
    assert run.finished_at is not None


# --- Éxito: código 0, Reading persistida, Run completed --------------------


def test_exito_persiste_reading_marca_item_read_y_cierra_el_run_como_completed(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    fake_provider.respond(
        AgentRole.READER,
        json=_valid_reading_json(interest_score=5),
        tokens_in=1500,
        tokens_out=400,
    )
    item_id = _seed_item(db_session_factory)

    code = main(["run-item", str(item_id)])

    assert code == 0
    assert len(fake_provider.calls) == 1
    assert fake_provider.calls[0].role is AgentRole.READER

    assert _item_status(db_session_factory, item_id) is ItemStatus.READ

    readings = _all_readings(db_session_factory)
    assert len(readings) == 1
    reading = readings[0]
    assert reading.item_id == item_id
    assert reading.interest_score == 5
    assert reading.tokens_in == 1500
    assert reading.tokens_out == 400

    calls = _all_agent_calls(db_session_factory)
    assert len(calls) == 1
    assert calls[0].status is AgentCallStatus.OK
    assert calls[0].agent is AgentRole.READER
    assert calls[0].tokens_in == 1500
    assert calls[0].tokens_out == 400

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1, "run-item crea un Run cuando no había ninguno RUNNING"
    run = runs[0]
    assert run.status is RunStatus.COMPLETED, (
        "con una Reading producida, el Run que este proceso creó se cierra 'completed'"
    )
    assert run.finished_at is not None
    assert calls[0].run_id == run.id


# --- Run reutilizado: se deja abierto, nunca lo cierra run-item ------------


def test_run_ya_running_se_reutiliza_y_se_deja_abierto(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """Simula la noche de T44 en marcha: un `Run` `RUNNING` ya existe antes
    de invocar `run-item`. Cerrarlo sería un bug (cortaría la noche de otro
    proceso); dejarlo abierto y reutilizado es lo único correcto (T41,
    "Hecho cuando")."""
    fake_provider.respond(
        AgentRole.READER, json=_valid_reading_json(), tokens_in=900, tokens_out=200
    )
    existing_run_id = _seed_running_run(
        db_session_factory, started_at=_WITHIN_WINDOW, budget_tokens=300_000
    )
    item_id = _seed_item(db_session_factory)

    code = main(["run-item", str(item_id)])

    assert code == 0
    runs = _all_runs(db_session_factory)
    assert len(runs) == 1, "run-item no crea un segundo Run cuando ya había uno RUNNING"
    run = runs[0]
    assert run.id == existing_run_id
    assert run.status is RunStatus.RUNNING, "run-item no cierra un Run que no ha creado él"
    assert run.finished_at is None

    calls = _all_agent_calls(db_session_factory)
    assert len(calls) == 1
    assert calls[0].run_id == existing_run_id, "el gasto se contabiliza contra el Run reutilizado"
