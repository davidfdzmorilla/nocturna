"""Ensayo en pequeño del corte de T44: dos formas de que la noche se pare a
mitad de un `ReadItem` con reintento, contra el orquestador real (T41, nota
crítica de `docs/PLAN_TAREAS.md`).

Ambos tests comparten forma: un primer intento real (JSON no parseable, así
que `ReadItem` intenta un segundo intento) y un segundo intento que NUNCA
debe llegar a llamar al proveedor -- lo corta `BudgetGuard.authorize` antes
de construir el `AgentRequest`. Se comprueba con un contador de llamadas a
`agent_sdk_provider.query`, no solo con el número de filas de `AgentCall`.

- `test_presupuesto_agotado_...`: `Run.budget_tokens` justo por encima de lo
  que cuesta el primer intento (con la extracción de tokens corregida,
  `usage` + `model_usage`, ver T40): el segundo intento se deniega por
  `BUDGET_EXHAUSTED`.
- `test_ventana_cruza_hard_stop_...`: el reloj cruza `window.hard_stop`
  ENTRE el primer intento y el segundo (el reloj avanza como efecto
  colateral del propio `query()` falso, justo cuando el intento real
  termina) -- exactamente el mecanismo que T44 debe manejar: una llamada en
  vuelo cuando cae `hard_stop`. Denegado por `OUTSIDE_WINDOW`.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from typing import Any

import pytest
import sqlalchemy as sa
from factories import make_item, make_run
from fakes.clock import FakeClock
from helpers.sdk_doubles import build_fake_query, make_result_message

from nocturna import cli
from nocturna.application.agents.prompt_loader import READER_PROMPT_VERSION, load_prompt
from nocturna.application.budget import (
    BudgetExceeded,
    BudgetPolicy,
    OutsideExecutionWindow,
)
from nocturna.application.use_cases.read_item import ReadItem
from nocturna.domain.entities import AgentCallStatus, ItemStatus
from nocturna.infrastructure.db.models import AgentCallRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_JUST_AFTER_HARD_STOP = datetime(2026, 1, 1, 4, 46, 0, tzinfo=UTC)
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
        "run_timeout_s": 16_200,
        "window_start": time(0, 0),
        "window_hard_stop": time(4, 45),
        "weekly_reset_weekday": 0,
        "weekly_reset_hour": 0,
        "reset_day_multiplier": 1.0,
    }
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


def _persist_run_and_item(db_session_factory, *, budget_tokens: int) -> tuple[Any, Any]:
    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        items = SqlAlchemyItemRepository(session)
        run = make_run(budget_tokens=budget_tokens)
        runs.add(run)
        item = make_item()
        items.add_many([item])
        session.flush()
        return run.id, item.id


def _make_read_item(*, work, provider, estimated_tokens: int) -> ReadItem:
    return ReadItem(
        work=work,
        provider=provider,
        system_prompt=load_prompt("reader"),
        prompt_version=READER_PROMPT_VERSION,
        model=_MODEL,
        max_turns=3,
        estimated_tokens=estimated_tokens,
        max_attempts=2,
    )


def _rows_for_run(db_session_factory, run_id) -> list[AgentCallRow]:
    with db_session_factory() as check_session:
        stmt = sa.select(AgentCallRow).where(AgentCallRow.run_id == run_id)
        return list(check_session.execute(stmt).scalars().all())


# --- 1. Presupuesto agotado justo antes del reintento -----------------------


async def test_presupuesto_agotado_antes_del_reintento_deniega_y_no_hay_segunda_llamada(
    db_session_factory, monkeypatch
) -> None:
    # Gasto real del primer intento (máximo entre `usage` y la suma de
    # `model_usage`, Haiku incluido): 1400/70 = 1470 tokens.
    usage = {
        "input_tokens": 500,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 50,
    }
    model_usage = {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 900,
            "outputTokens": 20,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
        _MODEL: {
            "inputTokens": 500,
            "outputTokens": 50,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
    }
    expected_tokens_in = max(
        usage["input_tokens"], sum(v["inputTokens"] for v in model_usage.values())
    )
    expected_tokens_out = max(
        usage["output_tokens"], sum(v["outputTokens"] for v in model_usage.values())
    )
    assert (expected_tokens_in, expected_tokens_out) == (1400, 70)
    first_attempt_cost = expected_tokens_in + expected_tokens_out

    frame = make_result_message(result="no es json", usage=usage, model_usage=model_usage)

    calls: list[int] = []

    def _fake_query_factory(*, prompt: str, options: object):
        calls.append(1)
        return build_fake_query([frame], sleep_before_s=0.002)(prompt=prompt, options=options)

    monkeypatch.setattr(agent_sdk_provider, "query", _fake_query_factory, raising=True)

    # `budget_tokens` justo por encima de lo que cuesta el primer intento:
    # tras registrarlo, al Reader (sin reserva del Editor) solo le quedan 5
    # tokens, insuficientes para `estimated_tokens=100` del segundo intento.
    policy = _policy(editor_reserve_tokens=0)
    budget_tokens = first_attempt_cost + 5
    run_id, item_id = _persist_run_and_item(db_session_factory, budget_tokens=budget_tokens)
    work = cli._agent_work_factory(db_session_factory, run_id, policy, FakeClock(_WITHIN_WINDOW))
    provider = AgentSDKProvider()

    with work() as w:
        item = w.items.get(item_id)
    assert item is not None

    read_item = _make_read_item(work=work, provider=provider, estimated_tokens=100)

    with pytest.raises(BudgetExceeded):
        await read_item(item)

    assert len(calls) == 1, "el segundo intento no debe llegar a llamar al proveedor"

    rows = _rows_for_run(db_session_factory, run_id)
    assert len(rows) == 1, "solo debe quedar la fila del primer intento"
    assert rows[0].status is AgentCallStatus.INVALID_OUTPUT
    assert (rows[0].tokens_in, rows[0].tokens_out) == (expected_tokens_in, expected_tokens_out)

    with db_session_factory() as check_session:
        persisted_item = SqlAlchemyItemRepository(check_session).get(item_id)
        assert persisted_item is not None
        assert persisted_item.status is ItemStatus.NEW, (
            "una denegación de presupuesto entre intentos no marca el Item como FAILED: "
            "queda NEW para que una noche futura lo reintente"
        )


# --- 2. El reloj cruza `hard_stop` entre el primer intento y el segundo -----


async def test_ventana_cruza_hard_stop_entre_intentos_deniega_y_no_hay_segunda_llamada(
    db_session_factory, monkeypatch
) -> None:
    usage = {
        "input_tokens": 300,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 15,
    }
    model_usage = {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 200,
            "outputTokens": 4,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
        _MODEL: {
            "inputTokens": 300,
            "outputTokens": 15,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
    }
    expected_tokens_in = max(
        usage["input_tokens"], sum(v["inputTokens"] for v in model_usage.values())
    )
    expected_tokens_out = max(
        usage["output_tokens"], sum(v["outputTokens"] for v in model_usage.values())
    )
    frame = make_result_message(result="tampoco es json", usage=usage, model_usage=model_usage)

    clock = FakeClock(_WITHIN_WINDOW)
    calls: list[int] = []

    async def _fake_query(*, prompt: str, options: object):
        calls.append(1)
        inner = build_fake_query([frame], sleep_before_s=0.002)(prompt=prompt, options=options)
        async for message in inner:
            yield message
        # Efecto colateral: el reloj cruza `hard_stop` justo cuando termina
        # la llamada real del primer intento -- la misma secuencia que T44
        # debe manejar cuando una llamada sigue en vuelo al caer las 04:45.
        clock.set(_JUST_AFTER_HARD_STOP)

    monkeypatch.setattr(agent_sdk_provider, "query", _fake_query, raising=True)

    # Presupuesto generoso: la única causa de denegación posible debe ser la
    # ventana horaria, no los tokens.
    policy = _policy()
    run_id, item_id = _persist_run_and_item(db_session_factory, budget_tokens=policy.nightly_tokens)
    work = cli._agent_work_factory(db_session_factory, run_id, policy, clock)
    provider = AgentSDKProvider()

    with work() as w:
        item = w.items.get(item_id)
    assert item is not None

    read_item = _make_read_item(work=work, provider=provider, estimated_tokens=100)

    with pytest.raises(OutsideExecutionWindow):
        await read_item(item)

    assert len(calls) == 1, "el segundo intento no debe llegar a llamar al proveedor"

    rows = _rows_for_run(db_session_factory, run_id)
    assert len(rows) == 1, "solo debe quedar la fila del primer intento"
    assert rows[0].status is AgentCallStatus.INVALID_OUTPUT
    assert (rows[0].tokens_in, rows[0].tokens_out) == (expected_tokens_in, expected_tokens_out)

    with db_session_factory() as check_session:
        persisted_item = SqlAlchemyItemRepository(check_session).get(item_id)
        assert persisted_item is not None
        assert persisted_item.status is ItemStatus.NEW
