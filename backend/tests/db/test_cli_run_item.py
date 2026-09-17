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
`BudgetGuard` (fuera de ventana para el Reader, o presupuesto agotado antes
del Popularizer, T42) · `5` lectura correcta pero divulgación fallida (T42:
el Popularizer no produjo un `Finding` -- JSON inválido agotados los
reintentos, timeout, límite de tasa o error del proveedor).

**T43: el Editor se encadena tras el Popularizer.** El único test de esta
suite que produce un candidato real (`test_exito_encadena_el_editor_...`)
programa también una respuesta para `AgentRole.EDITOR`, o la llamada real
agotaría su cola en `FakeLLMProvider` -- ver el docstring de ese test. El
`6` (Editor fallido con candidatos, o `EDITOR_ALREADY_CALLED`) no está
ejercitado aquí: los escenarios de fallo del Editor y de denegación de
`BudgetGuard` sobre él son ampliación de la batería de base de datos, fuera
del alcance de T43 (paso 5).

**T42: interest_score y el umbral del Popularizer.** `_valid_reading_json()`
usa `interest_score=4`, igual al umbral por defecto de
`config/pipeline.toml` (`limits.popularizer_min_interest_score = 4`): un
`Item` leído con esa puntuación por defecto SÍ dispara el Popularizer. Los
tests que no quieren ejercitar el Popularizer (porque no les interesa su
resultado) fijan `interest_score` explícitamente POR DEBAJO del umbral, para
que `PopularizeReading` descarte el ítem sin llamar al proveedor -- ni una
respuesta más que programar en `fake_provider`, ni una llamada de más que
contar. Los que sí quieren la cadena completa programan también una
respuesta para `AgentRole.POPULARIZER`.
"""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest
from factories import make_finding, make_item
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from sqlalchemy import select

from nocturna import cli
from nocturna.application.agents.prompt_loader import EDITOR_PROMPT_VERSION
from nocturna.cli import main
from nocturna.domain.entities import AgentCall, AgentCallStatus, ItemStatus, Run, RunStatus
from nocturna.domain.errors import LLMError
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow, FindingRow, ItemRow, ReadingRow, RunRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyFindingRepository,
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


def _all_findings(db_session_factory) -> list[FindingRow]:
    with _read_session(db_session_factory) as session:
        return list(session.execute(select(FindingRow)).scalars().all())


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
    motivo para no resolver la unicidad de otra forma. Esa guarda es de
    `ReadItem`, dispara con `item.status is not ItemStatus.NEW` sin
    importar cuál sea ese estado -- así que a este test no le interesa el
    Popularizer (T42): `interest_score` se fija a propósito POR DEBAJO del
    umbral (`SKIPPED_LOW_SCORE`, `Item` -> `DISCARDED`) para no tener que
    programarle ninguna respuesta a `AgentRole.POPULARIZER`; el segundo
    `run-item` dispara la misma `InvalidTransition` (código 3) desde
    `DISCARDED` que dispararía desde `READ`."""
    fake_provider.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=2), tokens_in=1200, tokens_out=300
    )
    item_id = _seed_item(db_session_factory)

    first_code = main(["run-item", str(item_id)])
    assert first_code == 0
    assert len(fake_provider.calls) == 1, (
        "interest_score por debajo del umbral: el Popularizer no se llama"
    )
    assert len(_all_readings(db_session_factory)) == 1
    assert _item_status(db_session_factory, item_id) is ItemStatus.DISCARDED

    second_code = main(["run-item", str(item_id)])  # no debe lanzar IntegrityError

    assert second_code == 3
    assert len(fake_provider.calls) == 1, "la segunda ejecución no debe llamar al proveedor"
    assert len(_all_readings(db_session_factory)) == 1, "ninguna Reading duplicada"
    assert len(_all_agent_calls(db_session_factory)) == 1, "ningún AgentCall nuevo"
    assert _item_status(db_session_factory, item_id) is ItemStatus.DISCARDED, (
        "el primer run-item ya dejó el Item DISCARDED (interest_score bajo); la guarda de "
        "ReadItem no lo cambia -- InvalidTransition dispara igual desde DISCARDED que desde "
        "READ, y es justo eso lo que este test verifica"
    )

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


# --- Éxito: código 0, Reading+Finding+Editor encadenados, Run completed ----


def test_exito_encadena_el_editor_y_publica_el_finding_cerrando_el_run_como_completed(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """`interest_score=5` (por encima del umbral) encadena el Popularizer
    tras el Reader; el `Finding` sin publicar que deja el Popularizer es el
    único candidato de la noche, así que T43 encadena también al Editor
    sobre él -- se programa respuesta para los tres roles.

    Antes de T43 este test (entonces
    `test_exito_persiste_reading_y_finding_sin_publicar_y_cierra_el_run_como_completed`)
    terminaba en el Popularizer y afirmaba `Finding` sin publicar / `Item`
    `READ`: ese desenlace era correcto solo porque el Popularizer era
    entonces la última etapa. Con el Editor encadenado (`cli.py`, "T43,
    paso 5"), un candidato real sin respuesta programada para
    `AgentRole.EDITOR` agotaría la cola del fake y haría fallar este mismo
    test con `ResponseQueueExhausted`; se programa una decisión de
    publicación y las aserciones del final pasan a describir el desenlace
    real de la noche completa (`Finding` publicado, `Item` `PUBLISHED`),
    no el de una etapa intermedia."""
    fake_provider.respond(
        AgentRole.READER,
        json=_valid_reading_json(interest_score=5),
        tokens_in=1500,
        tokens_out=400,
    )
    fake_provider.respond(
        AgentRole.POPULARIZER,
        json=_valid_popularizer_json(),
        tokens_in=1000,
        tokens_out=350,
    )
    item_id = _seed_item(db_session_factory)
    fake_provider.respond(
        AgentRole.EDITOR,
        json={
            "publish": [
                {"item_id": str(item_id), "confidence": 0.8, "reason": "hallazgo relevante"}
            ]
        },
        tokens_in=600,
        tokens_out=120,
    )

    code = main(["run-item", str(item_id)])

    assert code == 0
    assert len(fake_provider.calls) == 3
    assert fake_provider.calls[0].role is AgentRole.READER
    assert fake_provider.calls[1].role is AgentRole.POPULARIZER
    assert fake_provider.calls[2].role is AgentRole.EDITOR

    assert _item_status(db_session_factory, item_id) is ItemStatus.PUBLISHED, (
        "el Editor aprobó el único candidato de la noche: el Item pasa a PUBLISHED"
    )

    readings = _all_readings(db_session_factory)
    assert len(readings) == 1
    reading = readings[0]
    assert reading.item_id == item_id
    assert reading.interest_score == 5
    assert reading.tokens_in == 1500
    assert reading.tokens_out == 400

    findings = _all_findings(db_session_factory)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.item_id == item_id
    assert finding.confidence == 0.8
    assert finding.published_at is not None
    assert finding.title == _valid_popularizer_json()["title"]

    calls = _all_agent_calls(db_session_factory)
    assert len(calls) == 3
    reader_call, popularizer_call, editor_call = calls[0], calls[1], calls[2]
    assert reader_call.status is AgentCallStatus.OK
    assert reader_call.agent is AgentRole.READER
    assert reader_call.tokens_in == 1500
    assert reader_call.tokens_out == 400
    assert popularizer_call.status is AgentCallStatus.OK
    assert popularizer_call.agent is AgentRole.POPULARIZER
    assert popularizer_call.tokens_in == 1000
    assert popularizer_call.tokens_out == 350
    assert editor_call.status is AgentCallStatus.OK
    assert editor_call.agent is AgentRole.EDITOR
    assert editor_call.tokens_in == 600
    assert editor_call.tokens_out == 120

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1, "run-item crea un Run cuando no había ninguno RUNNING"
    run = runs[0]
    assert run.status is RunStatus.COMPLETED, (
        "con una Reading producida, una divulgación y una decisión del Editor con éxito, "
        "el Run que este proceso creó se cierra 'completed'"
    )
    assert run.finished_at is not None
    assert reader_call.run_id == run.id
    assert popularizer_call.run_id == run.id
    assert editor_call.run_id == run.id
    assert finding.run_id == run.id


# --- Puntuación baja: código 0, Item DISCARDED, cero Finding ---------------


def test_puntuacion_baja_no_llama_al_popularizer_descarta_el_item_y_no_deja_finding(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """Bajo el umbral (`config/pipeline.toml`,
    `limits.popularizer_min_interest_score = 4`), `PopularizeReading`
    descarta el `Item` sin invocar al proveedor. Solo se programa respuesta
    para el Reader: si el código llamara al Popularizer por error,
    `FakeLLMProvider` fallaría con `ResponseQueueExhausted` antes de llegar
    a ninguna de estas aserciones."""
    fake_provider.respond(
        AgentRole.READER,
        json=_valid_reading_json(interest_score=2),
        tokens_in=1000,
        tokens_out=200,
    )
    item_id = _seed_item(db_session_factory)

    code = main(["run-item", str(item_id)])

    assert code == 0
    assert len(fake_provider.calls) == 1
    assert fake_provider.calls[0].role is AgentRole.READER
    assert _item_status(db_session_factory, item_id) is ItemStatus.DISCARDED
    assert _all_findings(db_session_factory) == []

    calls = _all_agent_calls(db_session_factory)
    assert len(calls) == 1, "solo el AgentCall del Reader; el Popularizer nunca se llamó"

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1
    assert runs[0].status is RunStatus.COMPLETED, (
        "SKIPPED_LOW_SCORE no es un fallo (código 0): el Run se cierra 'completed' igual "
        "que si hubiera divulgado"
    )


# --- Popularizer fallido: código 5, Reading intacta, Run partial -----------


def test_popularizer_fallido_devuelve_5_deja_la_reading_intacta(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """Un fallo transitorio del Popularizer (`LLMError`, no reintentable)
    tras una lectura con éxito: código 5 (distinto del 1 de una lectura
    fallida), la `Reading` ya persistida no se toca, y no se deja ningún
    `Finding`."""
    fake_provider.respond(
        AgentRole.READER,
        json=_valid_reading_json(interest_score=5),
        tokens_in=1200,
        tokens_out=300,
    )
    fake_provider.fail(
        AgentRole.POPULARIZER, error=LLMError("fallo del Popularizer", tokens_in=200, tokens_out=10)
    )
    item_id = _seed_item(db_session_factory)

    code = main(["run-item", str(item_id)])

    assert code == 5
    assert len(fake_provider.calls) == 2
    assert fake_provider.calls[1].role is AgentRole.POPULARIZER

    readings = _all_readings(db_session_factory)
    assert len(readings) == 1, "la Reading ya persistida por ReadItem no se pierde"
    assert readings[0].interest_score == 5
    assert _all_findings(db_session_factory) == []

    assert _item_status(db_session_factory, item_id) is ItemStatus.READ, (
        "AGENT_ERROR es un fallo transitorio del Popularizer: el Item se queda READ, "
        "reintentable otra noche -- no se descarta"
    )

    calls = _all_agent_calls(db_session_factory)
    assert len(calls) == 2
    assert calls[1].status is AgentCallStatus.ERROR
    assert calls[1].agent is AgentRole.POPULARIZER

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1
    assert runs[0].status is RunStatus.PARTIAL, (
        "lectura correcta pero divulgación fallida: el Run que este proceso creó debe "
        "cerrarse PARTIAL, no COMPLETED -- un Run marcado COMPLETED aquí mentiría "
        "precisamente en el sitio donde T60 (resumen de la noche) va a mirar para "
        "decidir si hubo algo que revisar"
    )


# --- Denegación de presupuesto en el Popularizer: código 4, rol correcto ---


def test_denegacion_de_presupuesto_en_el_popularizer_devuelve_4_y_nombra_el_rol_correcto(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Presupuesto suficiente para que el Reader se autorice
    (`reader_estimated_tokens=6000` de `config/pipeline.toml` cabe en los
    6500 tokens disponibles tras la reserva del Editor) pero no para el
    Popularizer (`popularizer_estimated_tokens=7000` no cabe en los 5400
    tokens que quedan tras el gasto real del Reader). `_print_budget_denial`
    debe nombrar "Popularizer" en su mensaje, no "Reader" a pelo."""
    fake_provider.respond(
        AgentRole.READER,
        json=_valid_reading_json(interest_score=5),
        tokens_in=1000,
        tokens_out=100,
    )
    _seed_running_run(db_session_factory, started_at=_WITHIN_WINDOW, budget_tokens=66_500)
    item_id = _seed_item(db_session_factory)

    code = main(["run-item", str(item_id)])

    assert code == 4
    assert len(fake_provider.calls) == 1, "BudgetGuard deniega al Popularizer antes de llamarlo"
    assert fake_provider.calls[0].role is AgentRole.READER

    readings = _all_readings(db_session_factory)
    assert len(readings) == 1, "la Reading ya persistida por ReadItem no se pierde"
    assert _all_findings(db_session_factory) == []
    assert _item_status(db_session_factory, item_id) is ItemStatus.READ

    err = capsys.readouterr().err
    assert "Popularizer" in err, (
        "_print_budget_denial debe nombrar el rol denegado, no 'Reader' a pelo"
    )
    assert "Reader" not in err


# --- Run reutilizado: se deja abierto, nunca lo cierra run-item ------------


def test_run_ya_running_se_reutiliza_y_se_deja_abierto(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """Simula la noche de T44 en marcha: un `Run` `RUNNING` ya existe antes
    de invocar `run-item`. Cerrarlo sería un bug (cortaría la noche de otro
    proceso); dejarlo abierto y reutilizado es lo único correcto (T41,
    "Hecho cuando"). A este test no le interesa el Popularizer (T42): fija
    `interest_score` a propósito POR DEBAJO del umbral para que
    `PopularizeReading` no llegue a invocar al proveedor -- así el único
    `AgentCall` sigue siendo el del Reader, que es lo único que esta
    aserción de "gasto contabilizado contra el Run reutilizado" necesita
    comprobar."""
    fake_provider.respond(
        AgentRole.READER,
        json=_valid_reading_json(interest_score=2),
        tokens_in=900,
        tokens_out=200,
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


# --- Editor denegado por presupuesto: código 4, Run partial, Finding sin publicar


def test_editor_denegado_por_presupuesto_devuelve_4_run_partial_y_finding_sin_publicar(
    db_session_factory: object,
    fake_provider: FakeLLMProvider,
) -> None:
    """El Editor ve el presupuesto COMPLETO del Run (`BudgetGuard.
    _available_tokens`, sin restar su propia reserva -- esa reserva es
    justo la suya), pero `spent` es el MISMO contador acumulado para los
    tres roles (regla 2 de `application/budget.py`). Si Reader y
    Popularizer gastan de verdad muy por encima de lo que `authorize`
    comprobó contra sus propias estimaciones (`reader_estimated_tokens`/
    `popularizer_estimated_tokens` de `config/pipeline.toml`, nunca el
    gasto real, que no se conoce hasta después de la llamada), el
    presupuesto puede agotarse antes de que le toque al Editor aunque
    ninguno de los dos haya sido denegado en su momento -- exactamente el
    modo de fallo que ese fichero documenta junto a esas dos claves. No se
    siembra ningún Run: `run-item` debe crear el suyo propio
    (`run_reused=False`) para que se cierre `PARTIAL` al terminar."""
    fake_provider.respond(
        AgentRole.READER,
        json=_valid_reading_json(interest_score=5),
        tokens_in=200_000,
        tokens_out=0,
    )
    fake_provider.respond(
        AgentRole.POPULARIZER,
        json=_valid_popularizer_json(),
        tokens_in=96_000,
        tokens_out=0,
    )
    item_id = _seed_item(db_session_factory)

    code = main(["run-item", str(item_id)])

    assert code == 4
    assert len(fake_provider.calls) == 2, (
        "el Editor nunca llega a llamarse: BudgetGuard lo deniega antes"
    )
    assert fake_provider.calls[0].role is AgentRole.READER
    assert fake_provider.calls[1].role is AgentRole.POPULARIZER

    readings = _all_readings(db_session_factory)
    assert len(readings) == 1

    findings = _all_findings(db_session_factory)
    assert len(findings) == 1
    assert findings[0].published_at is None, "el Finding candidato se queda sin publicar"
    assert findings[0].confidence is None

    assert _item_status(db_session_factory, item_id) is ItemStatus.READ, (
        "el Editor nunca llegó a decidir: el Item se queda READ, ni PUBLISHED ni DISCARDED"
    )

    runs = _all_runs(db_session_factory)
    assert len(runs) == 1, "run-item crea un Run cuando no había ninguno RUNNING"
    assert runs[0].status is RunStatus.PARTIAL, (
        "denegación de presupuesto para el Editor con un candidato sin decidir: PARTIAL, "
        "nunca COMPLETED (BUDGET_EXHAUSTED pasa por terminal_status_for)"
    )
    assert runs[0].finished_at is not None


# --- EDITOR_ALREADY_CALLED: código 6, Run partial, sin ValueError ----------


def test_editor_ya_llamado_devuelve_6_run_partial_sin_value_error(
    db_session_factory: object,
) -> None:
    """`EDITOR_ALREADY_CALLED` es el único motivo de `BudgetDenied` que NO
    pasa por `terminal_status_for` (le lanzaría `ValueError` a propósito,
    ver `application/budget.py`, docstring de `terminal_status_for`):
    `cli._edit_one_night` lo distingue en el sitio de llamada, antes de
    invocarla (`cli.py`, "T43, paso 5"; cierra la decisión abierta nº 72 de
    `docs/OPEN_DECISIONS.md`).

    No hay forma de alcanzar `EDITOR_ALREADY_CALLED` con una única
    invocación de `run-item` de punta a punta: `max_attempts` del Editor se
    ata a `limits.max_editor_calls_per_night` (2, `config/pipeline.toml`),
    así que sus dos intentos internos (el reintento por JSON inválido)
    nunca agotan el propio tope del guard dentro de una sola llamada a
    `EditNight` -- el conteo llega a 2 exactamente cuando el bucle ya ha
    terminado. Solo una llamada sobre un Run que YA tiene 2 `AgentCall` de
    rol `editor` registradas lo dispara: el escenario real de una noche de
    T44 con el Editor ya agotado, reproducido aquí sembrando esas dos
    filas a mano y llamando a `cli._edit_one_night` directamente contra
    PostgreSQL real -- mismo `AgentWorkFactory`
    (`cli._agent_work_factory`) y mismo `BudgetPolicy`
    (`cli.budget_policy_from_config`) que usaría `run-item`."""
    config = load_pipeline_config()
    policy = cli.budget_policy_from_config(config)
    clock = FakeClock(_WITHIN_WINDOW)

    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        items = SqlAlchemyItemRepository(session)
        agent_calls = SqlAlchemyAgentCallRepository(session)
        findings = SqlAlchemyFindingRepository(session)

        run = Run(started_at=_WITHIN_WINDOW, budget_tokens=policy.nightly_tokens)
        runs.add(run)
        item = make_item(status=ItemStatus.READ)
        items.add_many([item])
        session.flush()
        run_id = run.id

        finding = make_finding(item.id, run_id)
        findings.add(finding)

        for _ in range(2):
            agent_calls.add(
                AgentCall(
                    run_id=run_id,
                    item_id=None,
                    agent=AgentRole.EDITOR,
                    model=config.models.editor,
                    tokens_in=100,
                    tokens_out=50,
                    duration_ms=10,
                    status=AgentCallStatus.OK,
                    prompt_version=EDITOR_PROMPT_VERSION,
                )
            )

    work = cli._agent_work_factory(db_session_factory, run_id, policy, clock)
    fake = FakeLLMProvider()  # no se debe llegar a llamar

    exit_code, status = cli._edit_one_night(
        work=work, provider=fake, config=config, clock=clock, run_id=run_id
    )

    assert exit_code == 6
    assert status is RunStatus.PARTIAL
    assert fake.calls == [], "EDITOR_ALREADY_CALLED deniega antes de llamar al proveedor"

    # `_edit_one_night` solo DECIDE el (exit_code, run_status); cerrar el Run
    # es responsabilidad de `_run_item` (mismo reparto de responsabilidades
    # que el resto de este fichero). Se cierra aquí, con `cli._finish_run`,
    # para comprobar de punta a punta que ese `RunStatus.PARTIAL` persiste
    # sin reventar -- ninguna llamada a `terminal_status_for` de por medio.
    cli._finish_run(db_session_factory, run_id, status, clock.now())

    with db_session_factory() as check_session:
        run_row = check_session.get(RunRow, run_id)
        assert run_row is not None
        assert run_row.status is RunStatus.PARTIAL
        assert run_row.finished_at is not None

        finding_row = check_session.get(FindingRow, finding.id)
        assert finding_row is not None
        assert finding_row.published_at is None
        assert finding_row.confidence is None

        item_row = check_session.get(ItemRow, item.id)
        assert item_row is not None
        assert item_row.status is ItemStatus.READ
