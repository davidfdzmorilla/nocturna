"""Tests de `application/use_cases/read_item.py::ReadItem` (T41, paso 7).

Sin base de datos: `FakeLLMProvider` (`tests/fakes/llm.py`) y un
`AgentWorkFactory` en memoria (`tests/fakes/work.py`), ningún test llama a
Claude ni toca PostgreSQL -- ver `.claude/skills/testing-without-claude`. El
equivalente contra PostgreSQL real que exige la "Nota crítica" de T41 en
`PLAN_TAREAS.md` (la cadena completa `BudgetGuard.authorize` -> `run_agent`
-> persistencia de `AgentCall`, con un proveedor real) es un paso posterior,
fuera del alcance de este fichero.

Dos decisiones del implementador que estos tests fijan explícitamente (ver
el plan del paso 7):

- En `RATE_LIMITED`/`TIMEOUT`/`AGENT_ERROR` el `Item` NO se marca `FAILED`:
  se queda `NEW` para que una noche futura lo reintente. `mark_failed()` es
  solo para JSON que agota los reintentos (`CLAUDE.md`, "Agentes").
- Un `Item` que no está `NEW` lanza `InvalidTransition` en vez de devolver
  un resultado neutro, y no abre ninguna unidad de trabajo de gasto.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    counting_work_factory,
    make_work_factory,
)

from nocturna.application.agents import runner as runner_module
from nocturna.application.agents.reader_output import ReaderOutput
from nocturna.application.budget import (
    BudgetDenied,
    BudgetGuard,
    BudgetPolicy,
    seconds_until_hard_stop,
)
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases import read_item as read_item_module
from nocturna.application.use_cases.read_item import ReadItem, ReadOutcome
from nocturna.domain.entities import AgentCall, AgentCallStatus, Item, ItemStatus, Reading, Run
from nocturna.domain.errors import InvalidTransition, LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 100_000,
        "editor_reserve_tokens": 10_000,
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


def _make_run(**overrides: object) -> Run:
    defaults: dict[str, object] = {"started_at": _WITHIN_WINDOW, "budget_tokens": 100_000}
    defaults.update(overrides)
    return Run(**defaults)


def _make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "source": "arxiv",
        "external_id": f"2501.{uuid4().hex[:5]}",
        "title": "Un título de prueba",
        "abstract": "Un abstract de prueba con contenido suficiente para el Reader.",
        "categories": ["astro-ph.GA"],
        "published_at": _WITHIN_WINDOW,
        "fetched_at": _WITHIN_WINDOW,
    }
    defaults.update(overrides)
    return Item(**defaults)


def _valid_reading_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "summary": "Resumen de prueba",
        "objects": ["NGC 1234"],
        "claims": ["Una afirmación de prueba"],
        "interest_score": 4,
    }
    defaults.update(overrides)
    return defaults


@dataclass
class _Environment:
    work: AgentWorkFactory
    runs: InMemoryRunRepository
    items: InMemoryItemRepository
    readings: InMemoryReadingRepository
    agent_calls: InMemoryAgentCallRepository
    run: Run
    work_calls: list[int] | None = None


def _make_environment(
    *,
    item: Item,
    policy: BudgetPolicy | None = None,
    run: Run | None = None,
    now: datetime = _WITHIN_WINDOW,
    count_work: bool = False,
) -> _Environment:
    resolved_policy = policy or _policy()
    resolved_run = run or _make_run(budget_tokens=resolved_policy.nightly_tokens)
    runs = InMemoryRunRepository(resolved_run)
    items = InMemoryItemRepository(item)
    readings = InMemoryReadingRepository()
    agent_calls = InMemoryAgentCallRepository()
    guard = BudgetGuard(
        run_id=resolved_run.id,
        policy=resolved_policy,
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(now),
    )
    work: AgentWorkFactory = make_work_factory(
        guard=guard, runs=runs, items=items, readings=readings, agent_calls=agent_calls
    )
    work_calls: list[int] | None = None
    if count_work:
        work, work_calls = counting_work_factory(work)
    return _Environment(
        work=work,
        runs=runs,
        items=items,
        readings=readings,
        agent_calls=agent_calls,
        run=resolved_run,
        work_calls=work_calls,
    )


def _make_read_item(
    *,
    work: AgentWorkFactory,
    provider: FakeLLMProvider,
    max_attempts: int = 1,
    model: str = "claude-sonnet-test",
    max_turns: int = 3,
    estimated_tokens: int = 100,
    system_prompt: str = "prompt de sistema del Reader",
    prompt_version: str = "v1",
) -> ReadItem:
    return ReadItem(
        work=work,
        provider=provider,
        system_prompt=system_prompt,
        prompt_version=prompt_version,
        model=model,
        max_turns=max_turns,
        estimated_tokens=estimated_tokens,
        max_attempts=max_attempts,
    )


# --- 1. Camino feliz ---------------------------------------------------


async def test_camino_feliz_json_valido_al_primer_intento():
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.attempts == 1
    assert result.tokens_spent == 1200 + 300
    assert result.reading is not None
    assert result.reading.summary == "Resumen de prueba"
    assert item.status is ItemStatus.READ
    assert env.readings.readings == [result.reading]
    assert len(env.agent_calls.calls) == 1
    assert env.agent_calls.calls[0].status is AgentCallStatus.OK
    assert env.agent_calls.calls[0].total_tokens == 1500


# --- 2. Inválido y luego válido -----------------------------------------


async def test_json_invalido_y_luego_valido_reintenta_una_vez():
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, raw="esto no es json", tokens_in=500, tokens_out=50)
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.attempts == 2
    assert result.tokens_spent == 500 + 50 + 1200 + 300
    assert len(env.agent_calls.calls) == 2
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert env.agent_calls.calls[0].total_tokens == 550
    assert env.agent_calls.calls[1].status is AgentCallStatus.OK
    assert env.agent_calls.calls[1].total_tokens == 1500
    assert item.status is ItemStatus.READ


# --- 3. Inválido en todos los intentos -----------------------------------


async def test_json_invalido_en_todos_los_intentos_marca_item_failed():
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, raw="no json, intento 1", tokens_in=400, tokens_out=40)
    fake.respond(AgentRole.READER, raw="no json, intento 2", tokens_in=400, tokens_out=40)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.INVALID_OUTPUT
    assert result.reading is None
    assert result.attempts == 2
    assert item.status is ItemStatus.FAILED
    assert len(env.agent_calls.calls) == 2
    assert all(call.status is AgentCallStatus.INVALID_OUTPUT for call in env.agent_calls.calls)
    assert env.readings.readings == []


# --- 4. BudgetDenied en authorize: se propaga sin capturar ----------------


async def test_budget_denied_en_authorize_se_propaga_sin_llamar_al_proveedor():
    item = _make_item()
    run = _make_run(budget_tokens=5_000)
    # editor_reserve_tokens == budget_tokens: presupuesto disponible para el
    # Reader es exactamente 0, así que la primera comprobación deniega.
    policy = _policy(editor_reserve_tokens=5_000)
    env = _make_environment(item=item, policy=policy, run=run)
    fake = FakeLLMProvider()
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(BudgetDenied):
        await read_item(item)

    assert fake.calls == [], "ni una sola llamada al proveedor debe salir de una denegación"
    assert env.agent_calls.calls == []
    assert item.status is ItemStatus.NEW


# --- 5. Presupuesto agotado justo antes del reintento ----------------------


async def test_presupuesto_agotado_antes_del_reintento_no_hay_segunda_llamada():
    item = _make_item()
    run = _make_run(budget_tokens=1_000)
    policy = _policy(editor_reserve_tokens=0)
    env = _make_environment(item=item, policy=policy, run=run)
    fake = FakeLLMProvider()
    # Primer intento: JSON inválido, gasta 950 de los 1000 disponibles.
    fake.respond(AgentRole.READER, raw="no json", tokens_in=900, tokens_out=50)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2, estimated_tokens=100)

    with pytest.raises(BudgetDenied):
        await read_item(item)

    assert len(fake.calls) == 1, "el reintento no debe llegar a llamar al proveedor"
    assert len(env.agent_calls.calls) == 1
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert item.status is ItemStatus.NEW


# --- 6. LLMRateLimited: no reintenta, item se queda NEW --------------------


async def test_rate_limited_no_reintenta_y_deja_el_item_en_new():
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.READER, error=LLMRateLimited("límite alcanzado", tokens_in=400, tokens_out=10)
    )
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=3)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.RATE_LIMITED
    assert result.reading is None
    assert result.attempts == 1
    assert result.tokens_spent == 410
    assert len(fake.calls) == 1, "un límite de tasa no se reintenta"
    assert item.status is ItemStatus.NEW, (
        "RATE_LIMITED no marca el Item como FAILED: mark_failed() es solo para JSON "
        "inválido agotado (CLAUDE.md, 'Agentes'); un límite de tasa es transitorio y "
        "debe poder reintentarse una noche futura"
    )
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 400
    assert call.tokens_out == 10


# --- 7. LLMTimeout: no reintenta, item se queda NEW ------------------------


async def test_timeout_no_reintenta_y_deja_el_item_en_new():
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.READER, error=LLMTimeout("no respondió a tiempo", tokens_in=300, tokens_out=20)
    )
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=3)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.TIMEOUT
    assert result.reading is None
    assert result.attempts == 1
    assert result.tokens_spent == 320
    assert len(fake.calls) == 1, "un timeout no se reintenta"
    assert item.status is ItemStatus.NEW, (
        "TIMEOUT no marca el Item como FAILED: se queda NEW para que una noche futura "
        "lo reintente (CLAUDE.md, 'Agentes')"
    )
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.TIMEOUT
    assert call.tokens_in == 300
    assert call.tokens_out == 20


# --- 8. LLMError genérico: no reintenta, item se queda NEW -----------------


async def test_agent_error_generico_no_reintenta_y_deja_el_item_en_new():
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.READER, error=LLMError("fallo genérico del agente", tokens_in=150, tokens_out=5)
    )
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=3)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.AGENT_ERROR
    assert result.reading is None
    assert result.attempts == 1
    assert result.tokens_spent == 155
    assert len(fake.calls) == 1, "un error genérico del agente no se reintenta"
    assert item.status is ItemStatus.NEW, (
        "AGENT_ERROR no marca el Item como FAILED: se queda NEW para que una noche "
        "futura lo reintente (CLAUDE.md, 'Agentes')"
    )
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 150
    assert call.tokens_out == 5


# --- 9. Item no NEW: InvalidTransition, ninguna llamada, ningún gasto ------


async def test_item_no_new_lanza_invalid_transition_sin_llamar_a_nada():
    item = _make_item()
    item.mark_read()  # NEW -> READ: ya no debería procesarse de nuevo
    env = _make_environment(item=item, count_work=True)
    fake = FakeLLMProvider()
    read_item = _make_read_item(work=env.work, provider=fake)

    with pytest.raises(InvalidTransition):
        await read_item(item)

    assert fake.calls == []
    assert env.agent_calls.calls == []
    assert env.work_calls == [0], (
        "ReadItem no debe abrir ninguna unidad de trabajo de gasto si el Item ya no "
        "está NEW -- la guarda de estado va antes que cualquier authorize()"
    )


# --- 10. El AgentRequest construido ----------------------------------------


async def test_agent_request_usa_timeout_del_guard_no_un_literal_y_los_parametros_del_constructor():
    item = _make_item()
    policy = _policy(item_timeout_s=180)
    # 10 segundos antes de hard_stop (04:45): item_timeout_s (180s) sería un
    # timeout mucho mayor, así que si el código usara ese literal en vez de
    # `guard.timeout_for_call()` este test lo distinguiría.
    now = datetime(2026, 1, 1, 4, 44, 50, tzinfo=UTC)
    run = _make_run(budget_tokens=policy.nightly_tokens)
    env = _make_environment(item=item, policy=policy, run=run, now=now)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    read_item = _make_read_item(
        work=env.work,
        provider=fake,
        model="modelo-de-prueba-xyz",
        max_turns=7,
        system_prompt="INSTRUCCIONES ESPECIALES DEL READER",
        estimated_tokens=50,
    )

    await read_item(item)

    assert len(fake.calls) == 1
    request = fake.calls[0]
    expected_timeout = seconds_until_hard_stop(now, policy.window_start, policy.window_hard_stop)
    assert expected_timeout < policy.item_timeout_s, (
        "el escenario del test debe garantizar que el timeout del guard y el literal "
        "item_timeout_s difieren, o esta aserción no distinguiría nada"
    )
    assert request.timeout_s == expected_timeout
    assert request.model == "modelo-de-prueba-xyz"
    assert request.max_turns == 7
    assert request.system_prompt == "INSTRUCCIONES ESPECIALES DEL READER"
    assert request.item_id == item.id
    assert request.role is AgentRole.READER


# --- 11. Bloqueante de T41: los tres payloads exactos del revisor ----------


@pytest.mark.parametrize(
    "overrides",
    [
        {"summary": "   "},
        {"objects": ["   "]},
        {"claims": ["   "]},
    ],
    ids=["summary_en_blanco", "elemento_en_blanco_en_objects", "elemento_en_blanco_en_claims"],
)
async def test_bloqueante_t41_payload_en_blanco_deja_agentcall_y_reintenta(overrides):
    """Los tres payloads exactos que el bloqueante de la revisión de T41 señaló.

    Antes de la corrección, `ReaderOutput` los aceptaba (solo exigía
    `str`/`list[str]`) y `Reading.__post_init__` reventaba con
    `InvariantViolation` FUERA del `try/except InvalidAgentOutput` que
    reintenta: una llamada ya cobrada por la suscripción que no dejaba ni
    `AgentCall` ni reintento (docstring de `reader_output.py`). Ahora
    `ReaderOutput` los rechaza en el propio parseo, así que caen en la misma
    rama que cualquier JSON con forma inválida: `AgentCall` registrado con
    `INVALID_OUTPUT` y reintento.
    """
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(**overrides), tokens_in=500, tokens_out=50
    )
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.attempts == 2
    assert len(env.agent_calls.calls) == 2, (
        "el intento con el payload en blanco debe dejar un AgentCall -- antes del "
        "bloqueante se perdía (0 AgentCall, 0 tokens contabilizados) porque la "
        "InvariantViolation de Reading escapaba fuera del try/except de "
        "InvalidAgentOutput"
    )
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert env.agent_calls.calls[0].total_tokens == 550
    assert env.agent_calls.calls[1].status is AgentCallStatus.OK
    assert item.status is ItemStatus.READ


# --- 12. El cinturón aislado: Reading(...) revienta tras un parseo "válido" -


async def test_cinturon_invariantviolation_de_reading_tras_parseo_valido_se_trata_igual(
    monkeypatch: pytest.MonkeyPatch,
):
    """Cinturón sobre tirantes (docstring de `ReadItem.__call__`, tercer
    `except InvariantViolation` alrededor de `Reading(...)`): si `ReaderOutput`
    y `Reading.__post_init__` volvieran a divergir, una salida que "pase el
    esquema" pero rompa la entidad no debe perder el `AgentCall`.

    Se fuerza parcheando `parse_reader_output` -- el nombre importado dentro
    de `read_item.py`, no el módulo en el que vive -- para que la primera
    llamada devuelva un `ReaderOutput` construido con `model_construct`, que
    **salta sus propios validadores** (`interest_score` fuera de 1-5): "pasa
    el esquema" solo porque nunca se validó, y revienta
    `Reading.__post_init__` con `InterestScoreOutOfRange` (subclase de
    `InvariantViolation`).
    """
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=700, tokens_out=60)
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    bad_output = ReaderOutput.model_construct(
        summary="resumen que sí pasaría el esquema",
        objects=[],
        claims=[],
        interest_score=99,
    )
    real_parse = read_item_module.parse_reader_output
    calls_made = {"count": 0}

    def _fake_parse(text: str):
        calls_made["count"] += 1
        if calls_made["count"] == 1:
            return bad_output
        return real_parse(text)

    monkeypatch.setattr(read_item_module, "parse_reader_output", _fake_parse)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.attempts == 2
    assert len(env.agent_calls.calls) == 2, (
        "el intento cuya Reading revienta con InvariantViolation debe dejar un "
        "AgentCall igual que un JSON inválido -- el bloqueante original con otra forma"
    )
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert env.agent_calls.calls[0].total_tokens == 760
    assert env.agent_calls.calls[1].status is AgentCallStatus.OK
    assert item.status is ItemStatus.READ


# --- 13. Camino feliz: tres unidades de trabajo, contadas de verdad --------


async def test_camino_feliz_abre_tres_unidades_de_trabajo_de_gasto_y_persistencia():
    """Hoy ningún otro test cuenta las unidades de trabajo del camino de
    éxito. El docstring de `ReadItem.__call__` pide `record_call` en una
    unidad separada de `readings.add`/`items.save` (para que un
    `IntegrityError` de esta última, p. ej. `uq_readings_item_id`, no
    deshaga con su rollback un `AgentCall` ya cobrado); la autorización
    (`authorize`) abre una tercera, previa a la llamada al LLM. Fusionar
    `record_call` con `readings.add`/`items.save` baja esta cifra a 2 sin que
    ningún otro test de este fichero lo note. **No** protege, por sí sola,
    contra fusionar `authorize` con `record_call` de forma que la conexión
    quede retenida durante la llamada real al LLM -- ambas siguen siendo
    dos `with self._work() as w:` distintos aunque se ejecuten sin cerrar la
    sesión entre medias, así que la cuenta no baja: eso es exactamente lo que
    demuestra que este contador, por sí solo, no basta para sostener ADR 0006
    § 2, y por qué existe además la sonda de pool contra PostgreSQL real
    (`tests/db/test_read_item_chain.py`)."""
    item = _make_item()
    env = _make_environment(item=item, count_work=True)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert env.work_calls == [3], (
        "camino feliz de un solo intento: una unidad de trabajo para authorize() "
        "(antes de llamar al proveedor), otra separada para record_call() (el "
        "AgentCall), y una tercera para Reading/Item -- fusionar cualquier par "
        "baja esta cifra"
    )


# --- 14. CancelledError con tokens adjuntos: se contabiliza y se relanza intacta


async def test_cancelled_error_con_tokens_adjuntos_registra_el_gasto_y_se_relanza_intacta():
    """`AgentSDKProvider.run_agent` puede adjuntar `tokens_in`/`tokens_out` a
    la `CancelledError` que relanza si ya había visto un `ResultMessage` con
    `usage` antes de la cancelación. El corte de `hard_stop` (T44) depende de
    que esta excepción llegue intacta -- ni traducida a otro tipo, ni
    sustituida por un fallo de contabilidad -- así que se verifica que sea
    literalmente la MISMA instancia la que sale de `ReadItem.__call__`.
    """
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop")
    cancelled.tokens_in = 500  # type: ignore[attr-defined]
    cancelled.tokens_out = 20  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await read_item(item)

    assert exc_info.value is cancelled, (
        "la excepción debe relanzarse intacta, no envuelta ni sustituida"
    )
    assert len(fake.calls) == 1, "una cancelación no se reintenta"
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 500
    assert call.tokens_out == 20
    assert item.status is ItemStatus.NEW


async def test_cancelled_error_sin_tokens_no_registra_nada_y_se_relanza_intacta():
    """Sin `tokens_in`/`tokens_out` adjuntos (cancelación antes de cualquier
    `ResultMessage`), no hay nada que contabilizar: cero `AgentCall`, y la
    excepción sigue relanzándose intacta."""
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop antes de cualquier resultado")
    fake.fail(AgentRole.READER, error=cancelled)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await read_item(item)

    assert exc_info.value is cancelled
    assert len(fake.calls) == 1
    assert env.agent_calls.calls == [], "sin tokens adjuntos, nada que contabilizar"
    assert item.status is ItemStatus.NEW


# --- 15. timeout_for_call() == 0: ni una llamada al proveedor --------------


async def test_timeout_for_call_cero_no_llama_al_proveedor_y_da_timeout():
    """A menos de un segundo de `hard_stop`, `BudgetGuard.timeout_for_call()`
    trunca a 0 (regla 6 de `budget.py`). `ReadItem` trata eso como "no
    llames": TIMEOUT terminal, sin llamar al proveedor y sin `AgentCall` --
    lanzar la llamada real solo para que muriera de inmediato facturaría un
    subproceso sin ninguna posibilidad real de respuesta.
    """
    item = _make_item()
    policy = _policy(item_timeout_s=180)
    now = datetime(2026, 1, 1, 4, 44, 59, 700_000, tzinfo=UTC)  # < 1s de hard_stop (04:45)
    run = _make_run(budget_tokens=policy.nightly_tokens)
    env = _make_environment(item=item, policy=policy, run=run, now=now)
    assert seconds_until_hard_stop(now, policy.window_start, policy.window_hard_stop) == 0, (
        "el escenario debe garantizar timeout_for_call() == 0, o esta prueba no distinguiría nada"
    )
    fake = FakeLLMProvider()
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.TIMEOUT
    assert result.reading is None
    assert result.attempts == 1
    assert result.tokens_spent == 0
    assert fake.calls == [], "sin tiempo real para responder, no debe llamarse al proveedor"
    assert env.agent_calls.calls == [], "timeout_for_call()==0 se trata como 'no llames': coste 0"
    assert item.status is ItemStatus.NEW


# --- 16. _build_prompt: el abstract viaja envuelto, el system_prompt no ----


def test_build_prompt_envuelve_el_abstract_entre_marcas_y_no_incluye_el_system_prompt():
    item = _make_item(title="Un título de prueba T41", abstract="Un abstract de prueba T41")

    prompt = ReadItem._build_prompt(item)

    assert (
        prompt
        == "Título: Un título de prueba T41\n\n<abstract>\nUn abstract de prueba T41\n</abstract>"
    )


async def test_agent_request_prompt_lleva_titulo_y_abstract_y_el_system_prompt_va_aparte():
    """Cierra el hueco que el revisor encontró por mutación: mutar
    `_build_prompt` para que devolviera solo el título (el abstract nunca se
    envía) dejaba pasar los 547 tests de entonces porque nada comprobaba que
    el contenido del ítem viaja en `AgentRequest.prompt`. Además comprueba
    que el `system_prompt` no se filtra dentro del `prompt` de usuario: son
    dos campos separados de `AgentRequest` (`domain/llm.py`)."""
    item = _make_item(
        title="Título singular ZQX41 para localizar en el prompt",
        abstract="Abstract singular XYZZY41 con contenido propio que debe viajar íntegro",
    )
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    read_item = _make_read_item(
        work=env.work, provider=fake, system_prompt="INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT 41"
    )

    await read_item(item)

    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert item.title in request.prompt
    assert f"<abstract>\n{item.abstract}\n</abstract>" in request.prompt
    assert request.system_prompt == "INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT 41"
    assert "INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT 41" not in request.prompt


# --- 17. El orden de las dos unidades de trabajo del camino feliz ----------


class _OrderTrackingAgentCallRepository(InMemoryAgentCallRepository):
    """Igual que `InMemoryAgentCallRepository`, pero deja constancia en
    `order` de cuándo se escribió el primer `AgentCall` -- la unidad de
    trabajo de `record_call`."""

    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self._order = order

    def add(self, call: AgentCall) -> None:
        self._order.append("record_call")
        super().add(call)


class _OrderTrackingReadingRepository(InMemoryReadingRepository):
    """Igual que `InMemoryReadingRepository`, pero deja constancia en
    `order` de cuándo se escribió el primer `Reading` -- la unidad de
    trabajo de persistencia (`readings.add` antes que `items.save` dentro
    de esa misma unidad, así que basta con instrumentar `add`)."""

    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self._order = order

    def add(self, reading: Reading) -> None:
        self._order.append("persist")
        super().add(reading)


def _make_order_tracking_environment(
    *, item: Item, policy: BudgetPolicy | None = None, run: Run | None = None
) -> tuple[AgentWorkFactory, list[str]]:
    """Como `_make_environment`, pero con repositorios que registran en
    `order` la primera escritura de cada unidad de trabajo, para afirmar
    sobre la secuencia y no solo sobre el número de unidades abiertas."""
    order: list[str] = []
    resolved_policy = policy or _policy()
    resolved_run = run or _make_run(budget_tokens=resolved_policy.nightly_tokens)
    runs = InMemoryRunRepository(resolved_run)
    items = InMemoryItemRepository(item)
    readings = _OrderTrackingReadingRepository(order)
    agent_calls = _OrderTrackingAgentCallRepository(order)
    guard = BudgetGuard(
        run_id=resolved_run.id,
        policy=resolved_policy,
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )
    work = make_work_factory(
        guard=guard, runs=runs, items=items, readings=readings, agent_calls=agent_calls
    )
    return work, order


async def test_camino_feliz_registra_el_agentcall_antes_que_reading_e_item():
    """El docstring de `ReadItem.__call__` pide `record_call` en una unidad
    de trabajo separada de `readings.add`/`items.save`, y en ESE orden: si
    se invirtiera, un `IntegrityError` en `readings.add` (p. ej.
    `uq_readings_item_id` ante dos `run-item` concurrentes sobre el mismo
    ítem) impediría que `record_call` llegara siquiera a ejecutarse -- la
    misma pérdida de dinero del bloqueante ya corregido de T41, esta vez
    por "nunca se ejecutó" en vez de por rollback. El contador de unidades
    de trabajo (test 13) no distingue esto: cuenta cuántas veces se abrió
    `work()`, no en qué orden se escribió cada repositorio.
    """
    item = _make_item()
    work, order = _make_order_tracking_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=work, provider=fake)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert order == ["record_call", "persist"], (
        "la contabilidad (AgentCall) debe escribirse antes que la persistencia "
        "(Reading/Item), nunca al revés"
    )


# --- 18. La asimetría de _record_cancelled_spend: un solo lado en cero -----


async def test_cancelled_error_con_solo_tokens_in_registra_el_gasto():
    """`tokens_out=0` no es "sin tokens adjuntos": la guarda de
    `_record_cancelled_spend` solo se salta el registro cuando AMBOS
    (`tokens_in` y `tokens_out`) son cero -- `and`, no `or`. Este caso es
    físicamente plausible: el `usage` visto antes de que el modelo hubiera
    emitido ningún token de salida. Con `or` en vez de `and`, esta
    cancelación perdería en silencio los 500 tokens de entrada ya
    cobrados por la suscripción.
    """
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop, sin salida todavía")
    cancelled.tokens_in = 500  # type: ignore[attr-defined]
    cancelled.tokens_out = 0  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await read_item(item)

    assert exc_info.value is cancelled
    assert len(env.agent_calls.calls) == 1, (
        "tokens_in=500/tokens_out=0 debe contabilizarse: solo el caso 0/0 se descarta"
    )
    call = env.agent_calls.calls[0]
    assert call.tokens_in == 500
    assert call.tokens_out == 0
    assert item.status is ItemStatus.NEW


async def test_cancelled_error_con_solo_tokens_out_registra_el_gasto():
    """Simétrico del anterior: `tokens_in=0` con `tokens_out` no nulo. Menos
    plausible físicamente (no suele haber salida sin entrada contabilizada),
    pero barato de fijar y distingue la misma mutación `and`/`or`.
    """
    item = _make_item()
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop, entrada no adjuntada")
    cancelled.tokens_in = 0  # type: ignore[attr-defined]
    cancelled.tokens_out = 250  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await read_item(item)

    assert exc_info.value is cancelled
    assert len(env.agent_calls.calls) == 1, (
        "tokens_in=0/tokens_out=250 debe contabilizarse: solo el caso 0/0 se descarta"
    )
    call = env.agent_calls.calls[0]
    assert call.tokens_in == 0
    assert call.tokens_out == 250
    assert item.status is ItemStatus.NEW


# --- 19. Fallo al contabilizar durante la cancelación no sustituye la ------
# --- CancelledError original ------------------------------------------------


def _failing_at_call_work_factory(factory: AgentWorkFactory, *, fail_at: int) -> AgentWorkFactory:
    """Envuelve `factory` para que la apertura número `fail_at` de una
    unidad de trabajo reviente con `RuntimeError` en vez de ceder un
    `AgentWork` -- simula, por ejemplo, un fallo de conexión a la base de
    datos durante el propio corte de `hard_stop`."""
    calls = [0]

    @contextmanager
    def _work():
        calls[0] += 1
        if calls[0] == fail_at:
            raise RuntimeError("fallo simulado al abrir la unidad de trabajo")
        with factory() as w:
            yield w

    return _work


async def test_fallo_al_contabilizar_durante_cancelacion_se_descarta_y_relanza_intacta(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """El revisor de T41 verificó esta rama a mano con una fábrica que
    revienta en la segunda apertura de unidad de trabajo -- la primera es
    `authorize()`, la segunda es la de `_record_cancelled_spend` -- y
    comprobó que sale la MISMA instancia de `CancelledError`, que el
    `RuntimeError` se traga con `logging.warning`, y que no queda ningún
    `AgentCall`. Es la rama de la que depende el corte de las 04:45 (T44):
    si la contabilización pudiera sustituir la cancelación por otra
    excepción, T44 dejaría de poder distinguir "corté yo" de "falló algo".

    `monkeypatch.setattr(..., "disabled", False)`: al lanzar la suite
    completa, `tests/db/` corre antes (orden alfabético) y su fixture de
    migraciones invoca `alembic/env.py::fileConfig`, que por defecto
    deshabilita cualquier logger ya existente y no declarado en
    `alembic.ini`. Sin reactivarlo aquí, este test pasaría solo o en el
    fichero, pero fallaría en la suite completa por un artefacto de orden de
    ejecución ajeno al código bajo test (alembic corre como proceso propio
    fuera de tests, así que esto no reproduce nada real de producción).

    Tras T42, el `logging.warning` que este test observa ya no vive en
    `read_item.py`: `ReadItem` delega toda la maquinaria de gasto -- reintento,
    `authorize`, contabilización -- en `AgentRunner`
    (`application/agents/runner.py`), incluido `_record_cancelled_spend`, que
    tiene su propio logger (`nocturna.application.agents.runner`), el que de
    verdad emite el mensaje que comprueba este test -- `read_item.py` ya no
    define ningún `_logger` propio (código muerto tras la migración).
    """
    monkeypatch.setattr(runner_module._logger, "disabled", False)
    item = _make_item()
    env = _make_environment(item=item)
    failing_work = _failing_at_call_work_factory(env.work, fail_at=2)
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop")
    cancelled.tokens_in = 500  # type: ignore[attr-defined]
    cancelled.tokens_out = 20  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    read_item = _make_read_item(work=failing_work, provider=fake, max_attempts=2)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await read_item(item)

    assert exc_info.value is cancelled, (
        "un fallo al contabilizar durante la cancelación no debe sustituir la "
        "CancelledError original por otra excepción"
    )
    assert env.agent_calls.calls == [], (
        "el RuntimeError simulado impide que record_call llegue a ejecutarse: 0 "
        "AgentCall, igual que verificó el revisor a mano"
    )
    assert any(
        "no se pudo contabilizar" in record.getMessage() and record.levelno == logging.WARNING
        for record in caplog.records
    ), "el fallo debe quedar registrado con logging.warning, no perderse en silencio"
    assert item.status is ItemStatus.NEW
