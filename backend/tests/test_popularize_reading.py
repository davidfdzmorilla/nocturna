"""Tests de `application/use_cases/popularize_reading.py::PopularizeReading` (T42).

Mismo patrón que `test_read_item.py` (T41, "tu plantilla"): sin base de
datos, `FakeLLMProvider` (`tests/fakes/llm.py`) y un `AgentWorkFactory` en
memoria (`tests/fakes/work.py`) -- ningún test llama a Claude ni toca
PostgreSQL (`.claude/skills/testing-without-claude`). El equivalente contra
PostgreSQL real vive en `tests/db/test_cli_run_item.py` (el encadenado
Reader -> Popularizer tras `run-item`).

Todos los `Item` de este fichero se construyen ya `READ` (`_make_item`,
`status=ItemStatus.READ` por defecto): `PopularizeReading` se invoca sobre
un `Item` que el Reader ya procesó, y `Item.discard()` solo es una
transición válida desde `READ` (`_ITEM_TRANSITIONS`, `domain/entities.py`)
-- construir el `Item` en `NEW` haría que el camino de umbral bajo o de
JSON inválido agotado reventara con `InvalidTransition` en vez de ejercitar
lo que este módulo prueba.

Decisiones que estos tests fijan explícitamente (ver el docstring de
`popularize_reading.py`):

- `interest_score < min_interest_score` descarta el `Item`
  (`READ -> DISCARDED`) sin llamar al proveedor ni abrir gasto: es el test
  que garantiza que el umbral ahorra dinero de verdad, no solo que existe.
- `INVALID_OUTPUT` (agotados los intentos) también descarta el `Item`: el
  equivalente del "ítem envenenado" de T41 (`ItemStatus.FAILED` para el
  Reader), aquí con `ItemStatus.DISCARDED` porque `Item` no permite volver
  a `FAILED` desde `READ`.
- `RATE_LIMITED`/`TIMEOUT`/`AGENT_ERROR` NO descartan el `Item`: se queda
  `READ`, reintentable otra noche -- misma asimetría que `ReadItem` aplica
  con `NEW`, deliberada en ambos casos.
- Un `Finding` producido por `build` nunca lleva `confidence` ni
  `published_at`: eso es del Editor (T43), no de este caso de uso.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import UUID, uuid4

import pytest
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryFindingRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    counting_work_factory,
    make_work_factory,
)

from nocturna.application.budget import (
    BudgetDenied,
    BudgetGuard,
    BudgetPolicy,
    seconds_until_hard_stop,
)
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.popularize_reading import (
    PopularizeOutcome,
    PopularizeReading,
)
from nocturna.domain.entities import (
    AgentCallStatus,
    FindingType,
    Item,
    ItemStatus,
    Reading,
    Run,
)
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
    """Un `Item` ya leído (`ItemStatus.READ`): ver el docstring del módulo."""
    defaults: dict[str, object] = {
        "source": "arxiv",
        "external_id": f"2501.{uuid4().hex[:5]}",
        "title": "Un título de prueba",
        "abstract": "Un abstract de prueba con contenido suficiente.",
        "categories": ["astro-ph.GA"],
        "published_at": _WITHIN_WINDOW,
        "fetched_at": _WITHIN_WINDOW,
        "status": ItemStatus.READ,
    }
    defaults.update(overrides)
    return Item(**defaults)


def _make_reading(item_id: UUID, **overrides: object) -> Reading:
    defaults: dict[str, object] = {
        "item_id": item_id,
        "summary": "Resumen de prueba para el Popularizer",
        "objects": ("NGC 1234",),
        "claims": ("Una afirmación de prueba",),
        "interest_score": 5,
        "tokens_in": 1000,
        "tokens_out": 300,
        "model": "claude-sonnet-test",
    }
    defaults.update(overrides)
    return Reading(**defaults)


def _valid_popularizer_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "title": "Un titular de prueba",
        "level_curious": "Nivel curioso de prueba",
        "level_amateur": "Nivel aficionado de prueba",
        "level_technical": "Nivel técnico de prueba",
    }
    defaults.update(overrides)
    return defaults


@dataclass
class _Environment:
    work: AgentWorkFactory
    runs: InMemoryRunRepository
    items: InMemoryItemRepository
    readings: InMemoryReadingRepository
    findings: InMemoryFindingRepository
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
    findings = InMemoryFindingRepository()
    agent_calls = InMemoryAgentCallRepository()
    guard = BudgetGuard(
        run_id=resolved_run.id,
        policy=resolved_policy,
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(now),
    )
    work: AgentWorkFactory = make_work_factory(
        guard=guard,
        runs=runs,
        items=items,
        readings=readings,
        findings=findings,
        agent_calls=agent_calls,
    )
    work_calls: list[int] | None = None
    if count_work:
        work, work_calls = counting_work_factory(work)
    return _Environment(
        work=work,
        runs=runs,
        items=items,
        readings=readings,
        findings=findings,
        agent_calls=agent_calls,
        run=resolved_run,
        work_calls=work_calls,
    )


def _make_popularize_reading(
    *,
    work: AgentWorkFactory,
    provider: FakeLLMProvider,
    max_attempts: int = 1,
    model: str = "claude-sonnet-test",
    max_turns: int = 3,
    estimated_tokens: int = 100,
    system_prompt: str = "prompt de sistema del Popularizer",
    prompt_version: str = "popularizer-v1",
    min_interest_score: int = 4,
) -> PopularizeReading:
    return PopularizeReading(
        work=work,
        provider=provider,
        system_prompt=system_prompt,
        prompt_version=prompt_version,
        model=model,
        max_turns=max_turns,
        estimated_tokens=estimated_tokens,
        max_attempts=max_attempts,
        min_interest_score=min_interest_score,
    )


# --- 0. Guarda de estado: Item no READ, antes de cualquier gasto ----------


@pytest.mark.parametrize(
    "status",
    [ItemStatus.NEW, ItemStatus.DISCARDED, ItemStatus.PUBLISHED, ItemStatus.FAILED],
    ids=["new", "discarded", "published", "failed"],
)
async def test_item_no_read_lanza_invalid_transition_sin_gastar_nada(status):
    """Nada llegaba aquí en mal estado hasta T42 (`run-item` siempre corta
    antes, en `ReadItem`), pero T44 seleccionará candidatos directamente de
    base de datos y se los entregará sin pasar por `ReadItem` -- la guarda
    es lo único que evita que `item.discard()` reviente con
    `InvalidTransition` sin traducir, en el caso de `INVALID_OUTPUT`
    **después** de haber gastado (docstring del módulo, "La guarda de
    estado"). Lo que importa no es qué excepción sale, sino que no se gasta
    nada: cero llamadas al proveedor, cero `AgentCall`, ninguna unidad de
    trabajo de gasto abierta."""
    item = _make_item(status=status)
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item, count_work=True)
    fake = FakeLLMProvider()
    popularize = _make_popularize_reading(work=env.work, provider=fake)

    with pytest.raises(InvalidTransition):
        await popularize(item=item, reading=reading)

    assert fake.calls == [], "la guarda va antes que cualquier llamada al proveedor"
    assert env.agent_calls.calls == [], "la guarda va antes que cualquier AgentCall"
    assert env.work_calls == [0], (
        "PopularizeReading no debe abrir ninguna unidad de trabajo de gasto si el Item "
        "no está READ -- la guarda de estado va antes que cualquier authorize()"
    )


async def test_item_no_read_el_target_de_invalid_transition_es_discarded_no_read():
    """El `target` es `DISCARDED`, no `READ`, aunque el estado que le
    correspondería a este agente sea `READ`: el éxito del Popularizer NO
    transiciona el `Item` (se queda `READ`, ver el docstring del módulo,
    "Las transiciones de `Item`"), así que la única transición que este
    caso de uso invoca de verdad es `discard()` (`READ -> DISCARDED`, en
    `SKIPPED_LOW_SCORE` e `INVALID_OUTPUT`). La guarda lanza exactamente lo
    que lanzaría `item.discard()` si se le diera la oportunidad -- si
    alguien la cambia a `READ` por simetría con la guarda de `ReadItem`
    (que sí compara con el estado de entrada), este test debe reventar
    para que se lea por qué se eligió `DISCARDED`."""
    item = _make_item(status=ItemStatus.NEW)
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    popularize = _make_popularize_reading(work=env.work, provider=fake)

    with pytest.raises(InvalidTransition) as excinfo:
        await popularize(item=item, reading=reading)

    assert excinfo.value.origin == ItemStatus.NEW.value
    assert excinfo.value.target == ItemStatus.DISCARDED.value, (
        "el target debe ser DISCARDED, no READ: el único método de transición que "
        "PopularizeReading invoca de verdad es item.discard() (SKIPPED_LOW_SCORE / "
        "INVALID_OUTPUT) -- el éxito deja el Item en READ, sin transicionarlo "
        "(docstring del módulo, 'Las transiciones de Item')"
    )
    assert excinfo.value.entity == "Item"


# --- 1. Camino feliz ---------------------------------------------------


async def test_camino_feliz_json_valido_al_primer_intento():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=1000, tokens_out=400
    )
    popularize = _make_popularize_reading(work=env.work, provider=fake)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.POPULARIZED
    assert result.attempts == 1
    assert result.tokens_spent == 1000 + 400
    finding = result.finding
    assert finding is not None
    assert finding.confidence is None, "publicar es del Editor (T43), no de este caso de uso"
    assert finding.published_at is None
    assert finding.type is FindingType.PAPER_EXPLAINED
    assert finding.item_id == item.id
    assert finding.run_id == env.run.id
    assert item.status is ItemStatus.READ, (
        "un Item con Finding se queda READ a propósito: publicar es transición del Editor"
    )
    assert env.findings.unpublished_for_run(env.run.id) == [finding]
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.OK
    assert call.agent is AgentRole.POPULARIZER
    assert call.prompt_version == "popularizer-v1"
    assert call.total_tokens == 1400


# --- 2. interest_score por debajo del umbral: cero gasto -------------------


async def test_interest_score_por_debajo_del_umbral_descarta_sin_llamar_al_proveedor():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=3)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    popularize = _make_popularize_reading(work=env.work, provider=fake, min_interest_score=4)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.SKIPPED_LOW_SCORE
    assert result.finding is None
    assert result.attempts == 0
    assert result.tokens_spent == 0
    assert fake.calls == [], (
        "el umbral debe evitar CUALQUIER llamada al proveedor -- es lo que ahorra dinero de "
        "verdad, no solo lo que dice el outcome"
    )
    assert env.agent_calls.calls == []
    assert item.status is ItemStatus.DISCARDED


# --- 3. interest_score exactamente igual al umbral: sí divulga -------------


async def test_interest_score_igual_al_umbral_si_divulga():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=4)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=500, tokens_out=200
    )
    popularize = _make_popularize_reading(work=env.work, provider=fake, min_interest_score=4)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.POPULARIZED
    assert result.finding is not None
    assert len(fake.calls) == 1
    assert item.status is ItemStatus.READ


# --- 4. Inválido y luego válido ---------------------------------------------


async def test_json_invalido_y_luego_valido_reintenta_una_vez():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.POPULARIZER, raw="esto no es json", tokens_in=300, tokens_out=30)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=1000, tokens_out=400
    )
    popularize = _make_popularize_reading(work=env.work, provider=fake, max_attempts=2)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.POPULARIZED
    assert result.attempts == 2
    assert result.tokens_spent == 300 + 30 + 1000 + 400
    assert len(env.agent_calls.calls) == 2
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert env.agent_calls.calls[0].total_tokens == 330
    assert env.agent_calls.calls[1].status is AgentCallStatus.OK
    assert len(env.findings.unpublished_for_run(env.run.id)) == 1, (
        "dos intentos, un único Finding: el intento inválido no deja rastro en findings"
    )
    assert item.status is ItemStatus.READ


# --- 5. Inválido en todos los intentos: Item descartado ---------------------


async def test_json_invalido_en_todos_los_intentos_descarta_el_item():
    """Equivalente del "ítem envenenado" de T41 (`ItemStatus.FAILED` para el
    Reader): sin descartar el `Item` aquí, T44 reintentaría este ítem cada
    noche para siempre, gastando `max_calls_per_item` llamadas contra él sin
    parar (docstring de `popularize_reading.py`)."""
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.POPULARIZER, raw="no json, intento 1", tokens_in=200, tokens_out=20)
    fake.respond(AgentRole.POPULARIZER, raw="no json, intento 2", tokens_in=200, tokens_out=20)
    popularize = _make_popularize_reading(work=env.work, provider=fake, max_attempts=2)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.INVALID_OUTPUT
    assert result.finding is None
    assert result.attempts == 2
    assert item.status is ItemStatus.DISCARDED, (
        "fallo terminal: el ítem no vuelve a ofrecerse al Popularizer, igual que "
        "ItemStatus.FAILED hace para el Reader"
    )
    assert len(env.agent_calls.calls) == 2
    assert all(call.status is AgentCallStatus.INVALID_OUTPUT for call in env.agent_calls.calls)
    assert env.findings.unpublished_for_run(env.run.id) == []


# --- 6. Nivel en blanco: regresión del bloqueante de T41, en este caso -----


@pytest.mark.parametrize(
    "overrides",
    [
        {"title": "   "},
        {"level_curious": "   "},
        {"level_amateur": "   "},
        {"level_technical": "   "},
    ],
    ids=[
        "title_en_blanco",
        "level_curious_en_blanco",
        "level_amateur_en_blanco",
        "level_technical_en_blanco",
    ],
)
async def test_nivel_en_blanco_se_contabiliza_como_invalid_output_y_reintenta(overrides):
    """Mismo bloqueante que T41 señaló para `ReaderOutput`/`Reading`
    (`test_read_item.py::test_bloqueante_t41_payload_en_blanco...`), aquí en
    el caso de uso del Popularizer: un campo en blanco que "pasa el
    esquema" a medias no debe perder la llamada ya cobrada entre
    `run_agent` y `record_call`. `PopularizerOutput._not_blank` lo rechaza
    en el parseo, así que cae en la misma rama que cualquier JSON con forma
    inválida: `AgentCall` registrado con `INVALID_OUTPUT` y reintento."""
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.POPULARIZER,
        json=_valid_popularizer_json(**overrides),
        tokens_in=400,
        tokens_out=100,
    )
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=1000, tokens_out=400
    )
    popularize = _make_popularize_reading(work=env.work, provider=fake, max_attempts=2)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.POPULARIZED
    assert result.attempts == 2
    assert len(env.agent_calls.calls) == 2, (
        "el intento con el campo en blanco debe dejar un AgentCall -- si escapara del "
        "try/except de InvalidAgentOutput se perdería (0 AgentCall, 0 tokens contabilizados)"
    )
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert env.agent_calls.calls[0].total_tokens == 500
    assert env.agent_calls.calls[1].status is AgentCallStatus.OK
    assert item.status is ItemStatus.READ


# --- 7. Fallos transitorios: no reintentan, Item se queda READ -------------


async def test_rate_limited_no_reintenta_deja_el_item_en_read_y_la_reading_intacta():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.POPULARIZER,
        error=LLMRateLimited("límite alcanzado", tokens_in=300, tokens_out=10),
    )
    popularize = _make_popularize_reading(work=env.work, provider=fake, max_attempts=3)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.RATE_LIMITED
    assert result.finding is None
    assert result.attempts == 1
    assert len(fake.calls) == 1, "un límite de tasa no se reintenta"
    assert item.status is ItemStatus.READ, (
        "fallo transitorio: reintentable otra noche, NUNCA descartado -- asimetría "
        "deliberada frente a INVALID_OUTPUT, que sí es terminal (docstring del módulo)"
    )
    assert (
        reading.interest_score == 5 and reading.summary == "Resumen de prueba para el Popularizer"
    ), "la Reading que entró no se toca: PopularizeReading nunca la persiste ni la muta"
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 300
    assert call.tokens_out == 10


async def test_timeout_no_reintenta_deja_el_item_en_read_y_la_reading_intacta():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.POPULARIZER,
        error=LLMTimeout("no respondió a tiempo", tokens_in=250, tokens_out=15),
    )
    popularize = _make_popularize_reading(work=env.work, provider=fake, max_attempts=3)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.TIMEOUT
    assert result.finding is None
    assert result.attempts == 1
    assert len(fake.calls) == 1, "un timeout no se reintenta"
    assert item.status is ItemStatus.READ, (
        "fallo transitorio: reintentable otra noche, nunca descartado"
    )
    assert reading.interest_score == 5
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.TIMEOUT
    assert call.tokens_in == 250
    assert call.tokens_out == 15


async def test_agent_error_generico_no_reintenta_deja_el_item_en_read_y_la_reading_intacta():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.POPULARIZER,
        error=LLMError("fallo genérico del agente", tokens_in=150, tokens_out=5),
    )
    popularize = _make_popularize_reading(work=env.work, provider=fake, max_attempts=3)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.AGENT_ERROR
    assert result.finding is None
    assert result.attempts == 1
    assert len(fake.calls) == 1, "un error genérico del agente no se reintenta"
    assert item.status is ItemStatus.READ, (
        "fallo transitorio: reintentable otra noche, nunca descartado"
    )
    assert reading.interest_score == 5
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 150
    assert call.tokens_out == 5


# --- 8. BudgetDenied: se propaga sin capturar -------------------------------


async def test_budget_denied_se_propaga_sin_llamar_al_proveedor_ni_crear_finding():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    run = _make_run(budget_tokens=5_000)
    # editor_reserve_tokens == budget_tokens: presupuesto disponible para el
    # Popularizer es exactamente 0, así que la primera comprobación deniega.
    policy = _policy(editor_reserve_tokens=5_000)
    env = _make_environment(item=item, policy=policy, run=run)
    fake = FakeLLMProvider()
    popularize = _make_popularize_reading(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(BudgetDenied) as excinfo:
        await popularize(item=item, reading=reading)

    assert excinfo.value.role is AgentRole.POPULARIZER, (
        "el rol que llega a BudgetGuard.authorize debe ser el del llamador real "
        "(self._role en AgentRunner), no uno fijo: T43 hace que el Editor ramifique "
        "sobre este rol para su reserva y su tope de una llamada por noche "
        "(CLAUDE.md, 'Control de gasto') -- un rol equivocado se lo saltaría en silencio"
    )
    assert fake.calls == [], "ni una sola llamada al proveedor debe salir de una denegación"
    assert env.agent_calls.calls == []
    assert env.findings.unpublished_for_run(env.run.id) == []
    assert item.status is ItemStatus.READ
    assert reading.interest_score == 5


# --- 9. Presupuesto agotado justo antes del reintento -----------------------


async def test_presupuesto_agotado_antes_del_reintento_no_hay_segunda_llamada():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    run = _make_run(budget_tokens=1_000)
    policy = _policy(editor_reserve_tokens=0)
    env = _make_environment(item=item, policy=policy, run=run)
    fake = FakeLLMProvider()
    # Primer intento: JSON inválido, gasta 950 de los 1000 disponibles.
    fake.respond(AgentRole.POPULARIZER, raw="no json", tokens_in=900, tokens_out=50)
    popularize = _make_popularize_reading(
        work=env.work, provider=fake, max_attempts=2, estimated_tokens=100
    )

    with pytest.raises(BudgetDenied):
        await popularize(item=item, reading=reading)

    assert len(fake.calls) == 1, "el reintento no debe llegar a llamar al proveedor"
    assert len(env.agent_calls.calls) == 1
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert item.status is ItemStatus.READ


# --- 10. El AgentRequest construido -----------------------------------------


async def test_agent_request_usa_timeout_del_guard_no_un_literal_y_los_parametros_del_constructor():
    item = _make_item()
    reading = _make_reading(item.id, interest_score=5)
    policy = _policy(item_timeout_s=180)
    # 10 segundos antes de hard_stop (04:45): item_timeout_s (180s) sería un
    # timeout mucho mayor, así que si el código usara ese literal en vez de
    # `guard.timeout_for_call()` este test lo distinguiría.
    now = datetime(2026, 1, 1, 4, 44, 50, tzinfo=UTC)
    run = _make_run(budget_tokens=policy.nightly_tokens)
    env = _make_environment(item=item, policy=policy, run=run, now=now)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    popularize = _make_popularize_reading(
        work=env.work,
        provider=fake,
        model="modelo-de-prueba-xyz",
        max_turns=7,
        system_prompt="INSTRUCCIONES ESPECIALES DEL POPULARIZER",
        estimated_tokens=50,
    )

    await popularize(item=item, reading=reading)

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
    assert request.system_prompt == "INSTRUCCIONES ESPECIALES DEL POPULARIZER"
    assert request.item_id == item.id
    assert request.role is AgentRole.POPULARIZER


# --- 11. _build_prompt: la Reading viaja envuelta, el system_prompt no -----


def test_build_prompt_envuelve_la_reading_entre_marcas_e_incluye_el_titulo_dentro():
    item = _make_item(title="Un título de prueba T42")
    reading = _make_reading(
        item.id,
        summary="Resumen T42",
        objects=("NGC 1",),
        claims=("Afirmación T42",),
        interest_score=5,
    )

    prompt = PopularizeReading._build_prompt(item, reading)

    assert prompt.startswith("<reading>\n")
    assert prompt.endswith("</reading>")
    open_idx = prompt.index("<reading>")
    close_idx = prompt.index("</reading>")
    title_idx = prompt.index(f"Título: {item.title}")
    assert open_idx < title_idx < close_idx, (
        "el título del ítem -- texto de arXiv, no nuestro -- debe viajar DENTRO del "
        "bloque <reading>, no como instrucción propia fuera de él"
    )
    assert reading.summary in prompt
    assert "NGC 1" in prompt
    assert "Afirmación T42" in prompt


async def test_agent_request_prompt_lleva_titulo_y_reading_y_el_system_prompt_va_aparte():
    """Cierra el mismo hueco que el revisor de T41 encontró por mutación,
    ahora para el Popularizer: mutar `_build_prompt` para que devolviera
    solo el título dejaría pasar la suite si nada comprobara que la
    `Reading` viaja en `AgentRequest.prompt`. Además comprueba que el
    `system_prompt` no se filtra dentro del `prompt` de usuario: son dos
    campos separados de `AgentRequest` (`domain/llm.py`)."""
    item = _make_item(title="Título singular ZQX42 para localizar en el prompt")
    reading = _make_reading(
        item.id,
        summary="Resumen singular XYZZY42 con contenido propio que debe viajar íntegro",
        interest_score=5,
    )
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    popularize = _make_popularize_reading(
        work=env.work,
        provider=fake,
        system_prompt="INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT 42",
    )

    await popularize(item=item, reading=reading)

    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert item.title in request.prompt
    assert reading.summary in request.prompt
    assert "<reading>" in request.prompt
    assert "</reading>" in request.prompt
    assert request.system_prompt == "INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT 42"
    assert "INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT 42" not in request.prompt
