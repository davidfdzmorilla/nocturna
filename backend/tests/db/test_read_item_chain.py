"""Nota crítica de T41 (`docs/PLAN_TAREAS.md`): la cadena completa
`BudgetGuard.authorize` -> `run_agent` -> persistencia de `AgentCall` con un
orquestador real, no `FakeLLMProvider`.

`tests/test_read_item.py` ya prueba `ReadItem` de punta a punta con
`FakeLLMProvider` y repositorios en memoria (T41, paso 7): eso demuestra que
la lógica de `ReadItem` es correcta contra un doble. Lo que ese fichero NO
puede demostrar es que la contabilización de gasto sobrevive al paso por el
proveedor real (`AgentSDKProvider`) y por PostgreSQL de verdad -- en
concreto, que `ReadItem` usa la extracción de tokens corregida (`usage` y
`model_usage`, ver el hallazgo de 2,8x del humo manual de T40 en el
docstring de `infrastructure/llm/agent_sdk_provider.py`) y no una copia
propia que solo mire `usage`.

Lo único doblado aquí es `agent_sdk_provider.query` (`tests/helpers/sdk_doubles.py`
-- ni un subproceso `claude` real ni una API key), sustituido por un
`ResultMessage` real construido con la MISMA forma que T40 volcó de una
llamada de verdad: `usage` en snake_case con solo el modelo pedido (Sonnet),
y `model_usage` en camelCase con una entrada adicional de Haiku -- el gasto
lateral de sesión que el CLI factura pero que `usage` por sí solo no ve. El
resto de la cadena es real: `ReadItem` real, `BudgetGuard` real,
repositorios SQLAlchemy reales, `AgentSDKProvider` real, y el mismo
`AgentWorkFactory` (`cli._agent_work_factory`) que usa `run-item` en
producción -- no una reimplementación de test.

Si `ReadItem` (o el proveedor) solo contabilizara `usage`, el `AgentCall`
persistido registraría 523/6 tokens en vez de los 1452/23 reales: un
subregistro de 2,8x que este test debe ver en rojo (comprobado por
mutación, ver el informe de la tarea).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, time
from typing import Any

import pytest
import sqlalchemy as sa
from factories import make_item, make_run
from fakes.clock import FakeClock
from helpers.sdk_doubles import build_fake_query, make_result_message

from nocturna import cli
from nocturna.application.agents.prompt_loader import READER_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.read_item import ReadItem, ReadOutcome
from nocturna.domain.entities import AgentCallStatus, ItemStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.models import AgentCallRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_MODEL = "claude-sonnet-5"


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 300_000,
        "editor_reserve_tokens": 60_000,
        "max_items_per_night": 10,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 5,
        "item_timeout_s": 180,
        "editor_timeout_s": 300,
        "run_timeout_s": 16_200,
        "window_start": time(0, 0),
        "window_hard_stop": time(4, 45),
        "weekly_reset_weekday": 0,
        "weekly_reset_hour": 0,
        "reset_day_multiplier": 1.0,
    }
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


def _valid_reading_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "summary": "Resumen real de una llamada con orquestador real",
        "objects": ["NGC 1234"],
        "claims": ["Una afirmación de prueba"],
        "interest_score": 4,
    }
    defaults.update(overrides)
    return defaults


def _persist_run_and_item(db_session_factory, *, budget_tokens: int) -> tuple[Any, Any]:
    """Crea un `Run` y un `Item` persistidos de verdad, y devuelve `(run_id, item_id)`."""
    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        items = SqlAlchemyItemRepository(session)
        run = make_run(budget_tokens=budget_tokens)
        runs.add(run)
        item = make_item()
        items.add_many([item])
        session.flush()
        return run.id, item.id


def _make_read_item(
    *,
    work: AgentWorkFactory,
    provider: AgentSDKProvider,
    max_attempts: int,
    estimated_tokens: int = 100,
) -> ReadItem:
    return ReadItem(
        work=work,
        provider=provider,
        system_prompt=load_prompt("reader"),
        prompt_version=READER_PROMPT_VERSION,
        model=_MODEL,
        max_turns=3,
        estimated_tokens=estimated_tokens,
        max_attempts=max_attempts,
    )


def _rows_for_run(db_session_factory, run_id) -> list[AgentCallRow]:
    """Sesión NUEVA, abierta después del commit: la fila debe sobrevivir fuera
    de la unidad de trabajo que la escribió, igual que exige la nota crítica
    de T41."""
    with db_session_factory() as check_session:
        stmt = sa.select(AgentCallRow).where(AgentCallRow.run_id == run_id)
        return list(check_session.execute(stmt).scalars().all())


# --- 1. Camino feliz: la fila refleja el gasto real, no el subregistrado ---


async def test_orquestador_real_persiste_el_gasto_corregido_no_el_subregistrado(
    db_session_factory, monkeypatch
) -> None:
    # Misma forma que el volcado real de T40 (docstring de
    # `agent_sdk_provider.py`): `usage` solo ve el modelo pedido (Sonnet);
    # `model_usage` añade una entrada de Haiku que el CLI gastó por su
    # cuenta y que SÍ se factura.
    usage = {
        "input_tokens": 523,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 6,
    }
    model_usage = {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 929,
            "outputTokens": 17,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
        _MODEL: {
            "inputTokens": 523,
            "outputTokens": 6,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
    }
    # El esperado sale del propio frame que se acaba de construir: máximo
    # componente a componente entre `usage` (solo el modelo pedido) y la
    # SUMA de todas las entradas de `model_usage` (Haiku incluido) -- nunca
    # la suma de ambos (se solaparían). Si `ReadItem` solo contabilizara
    # `usage`, registraría 523/6 en vez de esto.
    expected_tokens_in = max(
        usage["input_tokens"], sum(v["inputTokens"] for v in model_usage.values())
    )
    expected_tokens_out = max(
        usage["output_tokens"], sum(v["outputTokens"] for v in model_usage.values())
    )
    assert (expected_tokens_in, expected_tokens_out) == (1452, 23), (
        "el escenario debe reproducir el subregistro de 2,8x del hallazgo de T40, o esta "
        "prueba no distinguiría un ReadItem que solo mirara 'usage'"
    )

    frame = make_result_message(
        result=json.dumps(_valid_reading_json()),
        usage=usage,
        model_usage=model_usage,
    )
    # `sleep_before_s` garantiza duration_ms > 0 de verdad (monotonic() no
    # avanza de forma fiable entre dos llamadas consecutivas sin él).
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([frame], sleep_before_s=0.005), raising=True
    )

    policy = _policy()
    run_id, item_id = _persist_run_and_item(db_session_factory, budget_tokens=policy.nightly_tokens)
    work = cli._agent_work_factory(db_session_factory, run_id, policy, FakeClock(_WITHIN_WINDOW))
    provider = AgentSDKProvider()

    with work() as w:
        item = w.items.get(item_id)
    assert item is not None

    read_item = _make_read_item(work=work, provider=provider, max_attempts=1)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.reading is not None
    assert result.tokens_spent == expected_tokens_in + expected_tokens_out

    rows = _rows_for_run(db_session_factory, run_id)
    assert len(rows) == 1, "exactamente una fila de AgentCall para esta llamada"
    row = rows[0]
    assert (row.tokens_in, row.tokens_out) == (expected_tokens_in, expected_tokens_out), (
        "la fila debe reflejar el gasto REAL (máximo entre usage y model_usage, Haiku "
        "incluido), no el subregistro de 'usage' a secas"
    )
    assert row.agent is AgentRole.READER
    assert row.item_id == item_id
    assert row.model == _MODEL
    assert row.status is AgentCallStatus.OK
    assert row.duration_ms > 0
    assert row.prompt_version == READER_PROMPT_VERSION

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == expected_tokens_in + expected_tokens_out

        run_row = SqlAlchemyRunRepository(check_session).get(run_id)
        assert run_row.tokens_used == expected_tokens_in + expected_tokens_out

        readings = SqlAlchemyReadingRepository(check_session)
        persisted_reading = readings.get_for_item(item_id)
        assert persisted_reading is not None
        assert persisted_reading.summary == _valid_reading_json()["summary"]

        persisted_item = SqlAlchemyItemRepository(check_session).get(item_id)
        assert persisted_item is not None
        assert persisted_item.status is ItemStatus.READ


# --- 2. Reintento: dos filas, suma acumulada, y el guard ve el primer ------
# --- intento ANTES de autorizar el segundo ----------------------------------


def _sequential_query(frames: list, *, before_call: dict[int, Callable[[], None]]) -> Callable:
    """Doble de `query()` que devuelve un frame distinto en cada llamada
    sucesiva, en el orden de `frames` -- una llamada real por intento del
    Reader, cada una con su propio `ResultMessage`.

    `before_call` ejecuta un `probe()` justo ANTES de emitir ningún mensaje
    de la llamada número `n` (0-indexada): un punto anclado a un evento
    observable desde fuera de `ReadItem` (la llamada real al proveedor),
    no al número de unidades de trabajo que `ReadItem` decida abrir
    internamente. `query()` para el segundo intento solo se invoca DESPUÉS
    de que `authorize()` del segundo intento ya haya tenido éxito, que a su
    vez solo puede pasar DESPUÉS de que `record_call` del primer intento
    haya confirmado -- así que este punto sigue siendo posterior al primer
    `record_call` con independencia de cuántas unidades de trabajo use
    `ReadItem` para conseguirlo (a diferencia de contar llamadas a
    `AgentWorkFactory`, que un cambio de implementación -- p. ej. fusionar
    `authorize` y `record_call` en una sola unidad de trabajo -- podría
    desalinear en silencio).
    """
    index = [0]

    async def _query(*, prompt: str, options: object) -> Any:
        current_index = index[0]
        index[0] += 1
        hook = before_call.get(current_index)
        if hook is not None:
            hook()
        inner = build_fake_query([frames[current_index]], sleep_before_s=0.002)(
            prompt=prompt, options=options
        )
        async for message in inner:
            yield message

    return _query


async def test_reintento_real_persiste_dos_filas_y_el_guard_ve_el_primer_intento_antes_del_segundo(
    db_session_factory, monkeypatch
) -> None:
    # Primer intento: éxito de llamada, pero salida no parseable como JSON.
    usage_1 = {
        "input_tokens": 400,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 20,
    }
    model_usage_1 = {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 300,
            "outputTokens": 5,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
        _MODEL: {
            "inputTokens": 400,
            "outputTokens": 20,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
    }
    expected_1 = (
        max(usage_1["input_tokens"], sum(v["inputTokens"] for v in model_usage_1.values())),
        max(usage_1["output_tokens"], sum(v["outputTokens"] for v in model_usage_1.values())),
    )
    frame_1 = make_result_message(
        result="esto no es json", usage=usage_1, model_usage=model_usage_1
    )

    # Segundo intento: JSON válido.
    usage_2 = {
        "input_tokens": 500,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 30,
    }
    model_usage_2 = {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 100,
            "outputTokens": 2,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
        _MODEL: {
            "inputTokens": 500,
            "outputTokens": 30,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
    }
    expected_2 = (
        max(usage_2["input_tokens"], sum(v["inputTokens"] for v in model_usage_2.values())),
        max(usage_2["output_tokens"], sum(v["outputTokens"] for v in model_usage_2.values())),
    )
    frame_2 = make_result_message(
        result=json.dumps(_valid_reading_json()), usage=usage_2, model_usage=model_usage_2
    )

    policy = _policy()
    run_id, item_id = _persist_run_and_item(db_session_factory, budget_tokens=policy.nightly_tokens)
    work = cli._agent_work_factory(db_session_factory, run_id, policy, FakeClock(_WITHIN_WINDOW))

    probe_ran: list[bool] = []

    def _probe_guard_sees_first_attempt_already() -> None:
        # Se ejecuta justo antes de que salga la llamada REAL de `query()`
        # para el segundo intento -- que solo puede ocurrir después de que
        # `authorize()` del segundo intento ya haya tenido éxito, que a su
        # vez solo puede pasar después de que `record_call` del primer
        # intento haya confirmado (ver docstring de `_sequential_query`).
        # Sesión completamente aparte de las que `ReadItem` usa por dentro:
        # si el gasto del primer intento no fuera visible aquí, el
        # presupuesto no significaría nada.
        probe_ran.append(True)
        with db_session_factory() as probe_session:
            agent_calls = SqlAlchemyAgentCallRepository(probe_session)
            assert agent_calls.tokens_used_for_run(run_id) == sum(expected_1), (
                "el gasto del primer intento debe estar ya persistido y visible antes de "
                "que salga la llamada real que autoriza el segundo"
            )
            probe_guard = BudgetGuard(
                run_id=run_id,
                policy=policy,
                runs=SqlAlchemyRunRepository(probe_session),
                agent_calls=agent_calls,
                clock=FakeClock(_WITHIN_WINDOW),
            )
            decision = probe_guard.check(AgentRole.READER, 50)
            assert decision.allowed
            assert decision.remaining_tokens == (
                policy.nightly_tokens - policy.editor_reserve_tokens - sum(expected_1)
            )

    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        _sequential_query(
            [frame_1, frame_2], before_call={1: _probe_guard_sees_first_attempt_already}
        ),
        raising=True,
    )

    with work() as w:
        item = w.items.get(item_id)
    assert item is not None

    read_item = _make_read_item(work=work, provider=AgentSDKProvider(), max_attempts=2)

    result = await read_item(item)

    assert probe_ran == [True], (
        "la sonda debe haberse ejecutado exactamente una vez, justo antes de la segunda "
        "llamada real -- si no se ejecuta, esta prueba no afirma nada sobre el invariante "
        "que dice comprobar"
    )
    assert result.outcome is ReadOutcome.READ
    assert result.attempts == 2
    assert result.tokens_spent == sum(expected_1) + sum(expected_2)

    rows = _rows_for_run(db_session_factory, run_id)
    assert len(rows) == 2, "un reintento debe dejar dos filas de AgentCall, una por intento"
    rows_by_status = {row.status: row for row in rows}
    assert set(rows_by_status) == {AgentCallStatus.INVALID_OUTPUT, AgentCallStatus.OK}

    invalid_row = rows_by_status[AgentCallStatus.INVALID_OUTPUT]
    assert (invalid_row.tokens_in, invalid_row.tokens_out) == expected_1

    ok_row = rows_by_status[AgentCallStatus.OK]
    assert (ok_row.tokens_in, ok_row.tokens_out) == expected_2

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == sum(expected_1) + sum(expected_2)

        run_row = SqlAlchemyRunRepository(check_session).get(run_id)
        assert run_row.tokens_used == sum(expected_1) + sum(expected_2)

        persisted_item = SqlAlchemyItemRepository(check_session).get(item_id)
        assert persisted_item is not None
        assert persisted_item.status is ItemStatus.READ


# --- 3. ADR 0006 § 2: la llamada al LLM ocurre fuera de toda transacción ---
# --- (la sonda de pool) -----------------------------------------------------


async def test_llamada_al_llm_ocurre_fuera_de_toda_transaccion_pool_checkedout_en_cero(
    db_session_factory, test_engine, monkeypatch
) -> None:
    """ADR 0006 § 2, hoy la única decisión de T41 sin ningún test que la
    sostenga: la llamada al proveedor ocurre **fuera** de toda unidad de
    trabajo, para que ninguna conexión del pool de PostgreSQL quede
    retenida mientras se espera al modelo (hasta `item_timeout_s`
    segundos). Fusionar las unidades de trabajo de `authorize` y
    `record_call` en una sola (que mantuviera la sesión abierta durante la
    llamada) pasa con los demás 106 tests de `tests/test_read_item.py` en
    verde -- ninguno de ellos observa el pool.

    Este test sí: registra `test_engine.pool.checkedout()` -- el número de
    conexiones del pool en uso en ese instante, desde una sesión
    completamente ajena a las que `ReadItem` abre por dentro -- justo antes
    de que el doble de `query()` emita el primer mensaje de la llamada
    real. Con el código actual (autorización en su propia unidad de
    trabajo, cerrada antes de llamar al proveedor) debe dar `0`; una
    fusión que retuviera la sesión durante la llamada daría `1`.
    """
    observed: list[int] = []
    frame = make_result_message(
        result=json.dumps(_valid_reading_json()),
        usage={
            "input_tokens": 200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 15,
        },
        model_usage=None,
    )

    async def _probing_query(*, prompt: str, options: object):
        observed.append(test_engine.pool.checkedout())
        yield frame

    monkeypatch.setattr(agent_sdk_provider, "query", _probing_query, raising=True)

    policy = _policy()
    run_id, item_id = _persist_run_and_item(db_session_factory, budget_tokens=policy.nightly_tokens)
    work = cli._agent_work_factory(db_session_factory, run_id, policy, FakeClock(_WITHIN_WINDOW))

    with work() as w:
        item = w.items.get(item_id)
    assert item is not None

    read_item = _make_read_item(work=work, provider=AgentSDKProvider(), max_attempts=1)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert observed == [0], (
        "el pool debe tener 0 conexiones en uso justo antes de la llamada real al "
        "LLM: si el guardia y el registro de gasto compartieran la misma unidad de "
        "trabajo que la llamada, aquí habría al menos 1 -- ver ADR 0006 § 2"
    )
