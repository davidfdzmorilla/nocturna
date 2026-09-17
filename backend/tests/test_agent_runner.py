"""Tests de `application/agents/runner.py::AgentRunner` (T42, red de seguridad).

Sin base de datos: `FakeLLMProvider` (`tests/fakes/llm.py`) y un
`AgentWorkFactory` en memoria (`tests/fakes/work.py`), ningún test llama a
Claude ni toca PostgreSQL -- ver `.claude/skills/testing-without-claude`.
`AgentRunner` es, tras este refactor, el único sitio del proyecto por donde
sale el dinero: lo que aquí se prueba vale para los tres agentes (Reader,
Popularizer, Editor), no solo para el Reader, porque el runner no sabe qué
es un `Item` ni una `Reading` (ver el docstring de `runner.py`, sección "Qué
NO hace este runner"). `build` se modela con dobles mínimos definidos en
este fichero -- ninguna entidad de dominio real hace falta para probar la
maquinaria de gasto.

El patrón de entorno (`_policy`/`_make_run`/`_make_environment`) y los
helpers de orden/contador de unidades de trabajo replican los de
`tests/test_read_item.py` (T41), adaptados: este runner no persiste ninguna
entidad de negocio (eso es responsabilidad del caso de uso que lo envuelve),
así que el camino feliz abre dos unidades de trabajo, no tres.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import UUID, uuid4

import pytest
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    RunNotFoundByIdRepository,
    counting_work_factory,
    make_work_factory,
    net_counting_work_factory,
)

from nocturna.application.agents import runner as runner_module
from nocturna.application.agents.parsing import InvalidAgentOutput
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.budget import (
    BudgetDenied,
    BudgetGuard,
    BudgetPolicy,
    seconds_until_hard_stop,
)
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.domain.entities import AgentCallStatus, Run
from nocturna.domain.errors import InvariantViolation, LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentResult, AgentRole

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


@dataclass
class _Environment:
    work: AgentWorkFactory
    runs: InMemoryRunRepository
    agent_calls: InMemoryAgentCallRepository
    run: Run
    work_calls: list[int] | None = None


def _make_environment(
    *,
    policy: BudgetPolicy | None = None,
    run: Run | None = None,
    now: datetime = _WITHIN_WINDOW,
    count_work: bool = False,
) -> _Environment:
    resolved_policy = policy or _policy()
    resolved_run = run or _make_run(budget_tokens=resolved_policy.nightly_tokens)
    runs = InMemoryRunRepository(resolved_run)
    # AgentRunner no toca Item ni Reading (docstring de runner.py, "Qué NO
    # hace este runner"): estos dos repositorios en memoria van vacíos, solo
    # están porque `AgentWork` los agrupa junto a `guard`/`runs`/`agent_calls`.
    items = InMemoryItemRepository()
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
        work=work, runs=runs, agent_calls=agent_calls, run=resolved_run, work_calls=work_calls
    )


def _make_runner(
    *,
    work: AgentWorkFactory,
    provider: FakeLLMProvider,
    role: AgentRole = AgentRole.READER,
    max_attempts: int = 1,
    model: str = "claude-sonnet-test",
    max_turns: int = 3,
    estimated_tokens: int = 100,
    system_prompt: str = "prompt de sistema de prueba",
    prompt_version: str = "v1",
) -> AgentRunner:
    return AgentRunner(
        work=work,
        provider=provider,
        role=role,
        model=model,
        system_prompt=system_prompt,
        prompt_version=prompt_version,
        estimated_tokens=estimated_tokens,
        max_turns=max_turns,
        max_attempts=max_attempts,
    )


# --- Dobles mínimos de `build` ---------------------------------------------


@dataclass(frozen=True, slots=True)
class _Built:
    """Entidad mínima que un `build` de prueba construye: no hace falta
    `Reading` ni ninguna otra entidad real para probar la maquinaria de
    gasto del runner (docstring del módulo, "Qué NO hace este runner")."""

    text: str
    run_id: UUID


def _build_ok(result: AgentResult, run_id: UUID) -> _Built:
    return _Built(text=result.output_text, run_id=run_id)


def _build_that_raises(exc: BaseException) -> Callable[[AgentResult, UUID], _Built]:
    """`build` que siempre lanza `exc`, para los escenarios en los que la
    salida del agente nunca es utilizable (`InvalidAgentOutput`,
    `InvariantViolation`) o revienta por un bug de programación."""

    def _build(result: AgentResult, run_id: UUID) -> _Built:
        raise exc

    return _build


# --- 1. Camino OK ------------------------------------------------------


async def test_camino_ok_un_intento_agentcall_ok_value_presente_run_id_presente():
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"foo": "bar"}, tokens_in=1200, tokens_out=300)
    runner = _make_runner(work=env.work, provider=fake)

    result = await runner.run(prompt="prompt de prueba", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.OK
    assert result.attempts == 1
    assert result.tokens_spent == 1200 + 300
    assert result.value is not None
    assert result.value.run_id == env.run.id
    assert result.run_id == env.run.id
    assert len(env.agent_calls.calls) == 1
    assert env.agent_calls.calls[0].status is AgentCallStatus.OK
    assert env.agent_calls.calls[0].total_tokens == 1500


# --- 2. InvalidAgentOutput en build: reintenta hasta agotar intentos -------


async def test_invalid_agent_output_en_build_reintenta_hasta_agotar_intentos():
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=300, tokens_out=30)
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=400, tokens_out=40)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)
    build = _build_that_raises(InvalidAgentOutput("json que no parsea, de prueba"))

    result = await runner.run(prompt="prompt", item_id=None, build=build)

    assert result.outcome is AttemptOutcome.INVALID_OUTPUT
    assert result.value is None
    assert result.attempts == 2
    assert result.run_id == env.run.id
    assert len(fake.calls) == 2
    assert len(env.agent_calls.calls) == 2
    assert all(call.status is AgentCallStatus.INVALID_OUTPUT for call in env.agent_calls.calls)
    assert result.tokens_spent == (300 + 30) + (400 + 40)


# --- 3. InvariantViolation en build: mismo tratamiento (regresión de T41) --


async def test_invariant_violation_en_build_reintenta_igual_que_invalid_agent_output():
    """Regresión explícita del bloqueante que cerró la revisión de T41: una
    llamada ya cobrada cuya entidad revienta con `InvariantViolation` (no
    `InvalidAgentOutput`) debía dejar un `AgentCall` y reintentar igual que
    un JSON con forma inválida. Antes del runner, cada caso de uso tenía que
    acordarse por su cuenta de envolver la construcción de su entidad en el
    mismo `try`; con `build` corriendo dentro de `run()`, esa garantía es del
    runner, una vez, para los tres agentes."""
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=500, tokens_out=50)
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=600, tokens_out=60)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)
    build = _build_that_raises(InvariantViolation("invariante rota, de prueba"))

    result = await runner.run(prompt="prompt", item_id=None, build=build)

    assert result.outcome is AttemptOutcome.INVALID_OUTPUT
    assert result.value is None
    assert result.attempts == 2
    assert result.run_id == env.run.id
    assert len(env.agent_calls.calls) == 2
    assert all(call.status is AgentCallStatus.INVALID_OUTPUT for call in env.agent_calls.calls)


# --- 4. timeout_for_call() a 0: ni una llamada al proveedor -----------------


async def test_timeout_for_call_cero_no_llama_al_proveedor_y_no_registra_agentcall():
    policy = _policy(item_timeout_s=180)
    now = datetime(2026, 1, 1, 4, 44, 59, 700_000, tzinfo=UTC)  # < 1s de hard_stop (04:45)
    run = _make_run(budget_tokens=policy.nightly_tokens)
    env = _make_environment(policy=policy, run=run, now=now)
    assert seconds_until_hard_stop(now, policy.window_start, policy.window_hard_stop) == 0, (
        "el escenario debe garantizar timeout_for_call() == 0, o esta prueba no distinguiría nada"
    )
    fake = FakeLLMProvider()
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)

    result = await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.TIMEOUT
    assert result.value is None
    assert result.attempts == 1
    assert result.tokens_spent == 0
    assert result.run_id == env.run.id
    assert fake.calls == [], "sin tiempo real para responder, no debe llamarse al proveedor"
    assert env.agent_calls.calls == [], "timeout_for_call()==0 se trata como 'no llames': coste 0"


# --- 5. LLMRateLimited / LLMTimeout / LLMError: terminan sin reintentar -----


async def test_llm_rate_limited_termina_sin_reintentar_con_status_y_tokens_correctos():
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.READER, error=LLMRateLimited("límite alcanzado", tokens_in=400, tokens_out=10)
    )
    runner = _make_runner(work=env.work, provider=fake, max_attempts=3)

    result = await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.RATE_LIMITED
    assert result.value is None
    assert result.attempts == 1
    assert result.tokens_spent == 410
    assert result.run_id == env.run.id
    assert len(fake.calls) == 1, "un límite de tasa no se reintenta"
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 400
    assert call.tokens_out == 10


async def test_llm_timeout_termina_sin_reintentar_con_status_y_tokens_correctos():
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.READER, error=LLMTimeout("no respondió a tiempo", tokens_in=300, tokens_out=20)
    )
    runner = _make_runner(work=env.work, provider=fake, max_attempts=3)

    result = await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.TIMEOUT
    assert result.value is None
    assert result.attempts == 1
    assert result.tokens_spent == 320
    assert result.run_id == env.run.id
    assert len(fake.calls) == 1, "un timeout no se reintenta"
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.TIMEOUT
    assert call.tokens_in == 300
    assert call.tokens_out == 20


async def test_llm_error_generico_termina_sin_reintentar_con_status_y_tokens_correctos():
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.fail(
        AgentRole.READER, error=LLMError("fallo genérico del agente", tokens_in=150, tokens_out=5)
    )
    runner = _make_runner(work=env.work, provider=fake, max_attempts=3)

    result = await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.AGENT_ERROR
    assert result.value is None
    assert result.attempts == 1
    assert result.tokens_spent == 155
    assert result.run_id == env.run.id
    assert len(fake.calls) == 1, "un error genérico del agente no se reintenta"
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 150
    assert call.tokens_out == 5


# --- 6. CancelledError con tokens adjuntos: se registra y se relanza intacta


async def test_cancelled_error_con_tokens_adjuntos_registra_el_gasto_y_se_relanza_intacta():
    """El corte de `hard_stop` (T44) depende de que esta excepción llegue
    intacta -- ni traducida a otro tipo, ni sustituida por un fallo de
    contabilidad -- así que se verifica que sea literalmente la MISMA
    instancia la que sale de `AgentRunner.run()`."""
    env = _make_environment()
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop")
    cancelled.tokens_in = 500  # type: ignore[attr-defined]
    cancelled.tokens_out = 20  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert exc_info.value is cancelled, (
        "la excepción debe relanzarse intacta, no envuelta ni sustituida"
    )
    assert len(fake.calls) == 1, "una cancelación no se reintenta"
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 500
    assert call.tokens_out == 20


# --- 7. CancelledError sin tokens: no registra nada, se relanza igual ------


async def test_cancelled_error_sin_tokens_no_registra_nada_y_se_relanza_intacta():
    env = _make_environment()
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop antes de cualquier resultado")
    fake.fail(AgentRole.READER, error=cancelled)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert exc_info.value is cancelled
    assert len(fake.calls) == 1
    assert env.agent_calls.calls == [], "sin tokens adjuntos, nada que contabilizar"


# --- 8. Fallo al contabilizar durante la cancelación: se traga y se relanza
# --- la CancelledError original, no el fallo de contabilidad ---------------


def _failing_at_call_work_factory(factory: AgentWorkFactory, *, fail_at: int) -> AgentWorkFactory:
    """Envuelve `factory` para que la apertura número `fail_at` de una unidad
    de trabajo reviente con `RuntimeError` en vez de ceder un `AgentWork` --
    simula, por ejemplo, un fallo de conexión a la base de datos durante el
    propio corte de `hard_stop`. Mismo patrón que `test_read_item.py`."""
    calls = [0]

    @contextmanager
    def _work():
        calls[0] += 1
        if calls[0] == fail_at:
            raise RuntimeError("fallo simulado al abrir la unidad de trabajo")
        with factory() as w:
            yield w

    return _work


async def test_fallo_al_contabilizar_durante_cancelacion_se_descarta_y_relanza_la_original(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """De esto depende el corte incondicional de las 04:45 (`CLAUDE.md`): si
    la contabilización pudiera sustituir la cancelación por otra excepción,
    T44 dejaría de poder distinguir "corté yo" de "falló algo".

    `monkeypatch.setattr(..., "disabled", False)`: al lanzar la suite
    completa, `tests/db/` corre antes (orden alfabético) y su fixture de
    migraciones invoca `alembic/env.py::fileConfig`, que deshabilita
    cualquier logger ya existente y no declarado en `alembic.ini` --
    incluido `runner_module._logger`. Mismo motivo que en `test_read_item.py`.
    """
    monkeypatch.setattr(runner_module._logger, "disabled", False)
    env = _make_environment()
    failing_work = _failing_at_call_work_factory(env.work, fail_at=2)
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop")
    cancelled.tokens_in = 500  # type: ignore[attr-defined]
    cancelled.tokens_out = 20  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    runner = _make_runner(work=failing_work, provider=fake, max_attempts=2)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert exc_info.value is cancelled, (
        "un fallo al contabilizar durante la cancelación no debe sustituir la "
        "CancelledError original por otra excepción"
    )
    assert env.agent_calls.calls == [], (
        "el RuntimeError simulado impide que record_call llegue a ejecutarse: 0 AgentCall"
    )
    assert any(
        "no se pudo contabilizar" in record.getMessage() and record.levelno == logging.WARNING
        for record in caplog.records
    ), "el fallo debe quedar registrado con logging.warning, no perderse en silencio"


# --- 9. Excepción inesperada de build (TypeError): se registra ERROR y se --
# --- relanza sin reintentar --------------------------------------------------


async def test_excepcion_inesperada_de_build_registra_agentcall_error_y_relanza_sin_reintentar():
    """Reintentar un bug de programación de `build` gastaría otra llamada
    para repetir exactamente el mismo bug, así que aquí NO hay reintento --
    a diferencia de `InvalidAgentOutput`/`InvariantViolation` (tests 2/3).
    La llamada al LLM ya se pagó, así que sí se registra un `AgentCall` con
    `status=ERROR` antes de relanzar la excepción original."""
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=700, tokens_out=60)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=3)
    bug = TypeError("bug de programación en build, de prueba")
    build = _build_that_raises(bug)

    with pytest.raises(TypeError) as exc_info:
        await runner.run(prompt="prompt", item_id=None, build=build)

    assert exc_info.value is bug
    assert len(fake.calls) == 1, "un bug de build no se reintenta"
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 700
    assert call.tokens_out == 60


# --- 10. Igual que 9, con fallo simultáneo de contabilización: se relanza --
# --- la excepción de build, no la de contabilidad ---------------------------


async def test_excepcion_de_build_con_fallo_simultaneo_de_contabilidad_relanza_la_de_build(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(runner_module._logger, "disabled", False)
    env = _make_environment()
    failing_work = _failing_at_call_work_factory(env.work, fail_at=2)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=700, tokens_out=60)
    runner = _make_runner(work=failing_work, provider=fake, max_attempts=3)
    bug = TypeError("bug de programación en build, de prueba")
    build = _build_that_raises(bug)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(TypeError) as exc_info:
            await runner.run(prompt="prompt", item_id=None, build=build)

    assert exc_info.value is bug, (
        "el fallo de contabilidad no debe sustituir la excepción original de build"
    )
    assert env.agent_calls.calls == [], "el RuntimeError simulado impide que record_call se ejecute"
    assert any(
        "no se pudo contabilizar" in record.getMessage() and record.levelno == logging.WARNING
        for record in caplog.records
    ), "el fallo debe quedar registrado con logging.warning, no perderse en silencio"


# --- 11. RunnerResult.run_id informado desde el primer intento autorizado --


async def test_run_id_informado_desde_el_primer_intento_autorizado_aunque_termine_en_timeout():
    """Invariante del docstring de `RunnerResult`: `run_id` viene informado
    desde el primer `authorize()` con éxito, antes incluso de llamar al LLM
    -- aquí verificado con un intento que termina en TIMEOUT. Los tests 2, 3
    y 5 ya comprueban lo mismo para INVALID_OUTPUT/RATE_LIMITED/AGENT_ERROR."""
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=LLMTimeout("no respondió", tokens_in=10, tokens_out=1))
    runner = _make_runner(work=env.work, provider=fake, max_attempts=1)

    result = await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.TIMEOUT
    assert result.run_id == env.run.id


# --- 12. item_id llega tal cual, sin confundirse con "no informado" --------


@pytest.mark.parametrize("item_id", [None, uuid4()], ids=["item_id_none", "item_id_uuid"])
async def test_item_id_llega_intacto_al_agent_request_y_al_agent_call(item_id: UUID | None):
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=100, tokens_out=10)
    runner = _make_runner(work=env.work, provider=fake)

    await runner.run(prompt="prompt", item_id=item_id, build=_build_ok)

    assert fake.calls[0].item_id == item_id
    assert env.agent_calls.calls[0].item_id == item_id


# --- 13. BudgetDenied en authorize: se propaga sin capturar -----------------


async def test_budget_denied_en_authorize_se_propaga_sin_tocar_el_proveedor():
    run = _make_run(budget_tokens=5_000)
    # editor_reserve_tokens == budget_tokens: presupuesto disponible para el
    # Reader es exactamente 0, así que la primera comprobación deniega.
    policy = _policy(editor_reserve_tokens=5_000)
    env = _make_environment(policy=policy, run=run)
    fake = FakeLLMProvider()
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(BudgetDenied):
        await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert fake.calls == [], "ni una sola llamada al proveedor debe salir de una denegación"
    assert env.agent_calls.calls == []


# --- 14. Orden de las unidades de trabajo: authorize primero, record_call --
# --- después y sola -----------------------------------------------------


async def test_camino_ok_abre_exactamente_dos_unidades_de_trabajo():
    """A diferencia de `ReadItem` (tres unidades: authorize, record_call y
    persistencia de la entidad de negocio), `AgentRunner` no persiste nada
    de negocio -- eso es responsabilidad de quien lo envuelve, después de
    que `run()` devuelva (docstring de `runner.py`, "Qué NO hace este
    runner"). El camino feliz de un solo intento abre, por tanto, dos: una
    para `authorize()`, otra -- ya fuera de la espera al LLM -- para
    `record_call()`."""
    env = _make_environment(count_work=True)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=1200, tokens_out=300)
    runner = _make_runner(work=env.work, provider=fake)

    result = await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.OK
    assert env.work_calls == [2], (
        "una unidad de trabajo para authorize() (antes de llamar al proveedor) y otra "
        "separada para record_call() -- fusionarlas baja esta cifra a 1"
    )


class _OrderTrackingAgentCallRepository(InMemoryAgentCallRepository):
    """Deja constancia en `order` de cuándo se escribió el primer
    `AgentCall` -- la unidad de trabajo de `record_call`."""

    def __init__(self, order: list[str]) -> None:
        super().__init__()
        self._order = order

    def add(self, call) -> None:  # noqa: ANN001 - firma igual que la clase base
        self._order.append("record_call")
        super().add(call)


def _make_order_tracking_environment(
    *, policy: BudgetPolicy | None = None, run: Run | None = None
) -> tuple[AgentWorkFactory, Run, list[str]]:
    """Como `_make_environment`, pero instrumenta `guard.authorize` (marca
    "authorize" en `order`) y `agent_calls.add` (marca "record_call"), para
    afirmar sobre la secuencia y no solo sobre el número de unidades
    abiertas -- mismo patrón que `test_read_item.py`, adaptado a las dos
    unidades de trabajo de este runner."""
    order: list[str] = []
    resolved_policy = policy or _policy()
    resolved_run = run or _make_run(budget_tokens=resolved_policy.nightly_tokens)
    runs = InMemoryRunRepository(resolved_run)
    items = InMemoryItemRepository()
    readings = InMemoryReadingRepository()
    agent_calls = _OrderTrackingAgentCallRepository(order)
    guard = BudgetGuard(
        run_id=resolved_run.id,
        policy=resolved_policy,
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )
    original_authorize = guard.authorize

    def _tracked_authorize(role: AgentRole, estimated_tokens: int) -> None:
        order.append("authorize")
        original_authorize(role, estimated_tokens)

    guard.authorize = _tracked_authorize  # type: ignore[method-assign]
    work = make_work_factory(
        guard=guard, runs=runs, items=items, readings=readings, agent_calls=agent_calls
    )
    return work, resolved_run, order


async def test_camino_ok_autoriza_antes_de_registrar_la_llamada():
    work, run, order = _make_order_tracking_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=1200, tokens_out=300)
    runner = _make_runner(work=work, provider=fake)

    result = await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.OK
    assert result.run_id == run.id
    assert order == ["authorize", "record_call"], (
        "authorize() debe ejecutarse antes que record_call(), nunca al revés -- "
        "invertir el orden autorizaría gasto sin haber comprobado el presupuesto"
    )


# --- 15. timeout_s del AgentRequest es exactamente guard.timeout_for_call() -


async def test_agent_request_usa_el_timeout_del_guard_no_un_literal():
    policy = _policy(item_timeout_s=180)
    # 10 segundos antes de hard_stop (04:45): item_timeout_s (180s) sería un
    # timeout mucho mayor, así que si el código usara ese literal en vez de
    # `guard.timeout_for_call()` este test lo distinguiría.
    now = datetime(2026, 1, 1, 4, 44, 50, tzinfo=UTC)
    run = _make_run(budget_tokens=policy.nightly_tokens)
    env = _make_environment(policy=policy, run=run, now=now)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=100, tokens_out=10)
    runner = _make_runner(work=env.work, provider=fake, estimated_tokens=50)

    await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert len(fake.calls) == 1
    request = fake.calls[0]
    expected_timeout = seconds_until_hard_stop(now, policy.window_start, policy.window_hard_stop)
    assert expected_timeout < policy.item_timeout_s, (
        "el escenario del test debe garantizar que el timeout del guard y el literal "
        "item_timeout_s difieren, o esta aserción no distinguiría nada"
    )
    assert request.timeout_s == expected_timeout


# --- 16. El prompt llega tal cual, el system_prompt va aparte --------------


async def test_prompt_llega_tal_cual_y_el_system_prompt_va_aparte():
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=100, tokens_out=10)
    runner = _make_runner(
        work=env.work,
        provider=fake,
        system_prompt="INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT DE PRUEBA",
    )
    prompt = "Contenido de usuario ZQX42 compuesto por el caso de uso"

    await runner.run(prompt=prompt, item_id=None, build=_build_ok)

    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.prompt == prompt
    assert request.system_prompt == "INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT DE PRUEBA"
    assert "INSTRUCCIONES EXCLUSIVAS DEL SYSTEM PROMPT DE PRUEBA" not in request.prompt


# --- 17. Ninguna unidad de trabajo sigue abierta durante la llamada al -----
# --- proveedor: el aserto directo de ADR 0006 § 2 ---------------------------


class _OpenUnitsProbeProvider:
    """Envuelve un `FakeLLMProvider` para registrar, en el instante exacto
    en que `run_agent()` se ejecuta, cuántas unidades de trabajo de
    `AgentWorkFactory` siguen abiertas -- el aserto directo que le falta al
    invariante más caro de ADR 0006 § 2: ninguna conexión de PostgreSQL
    retenida mientras se espera al LLM, hasta `item_timeout_s` (180s por
    defecto). `open_units` viene de `net_counting_work_factory`: a
    diferencia de `counting_work_factory` (aperturas totales, test 14), este
    contador sube al entrar y baja al salir de cada `with self._work()`, así
    que en cada instante refleja cuántas unidades siguen abiertas AHORA."""

    def __init__(self, provider: FakeLLMProvider, open_units: list[int]) -> None:
        self._provider = provider
        self._open_units = open_units
        self.observed_at_call: list[int] = []

    async def run_agent(self, request):  # noqa: ANN001, ANN201 - firma de LLMProvider
        self.observed_at_call.append(self._open_units[0])
        return await self._provider.run_agent(request)


async def test_ninguna_unidad_de_trabajo_sigue_abierta_durante_la_llamada_al_proveedor():
    """Mutación superviviente que este test cierra: mantener abierta la
    unidad de trabajo de `authorize` durante toda la llamada al LLM
    (`__enter__` antes, `__exit__` después de `run_agent`, sin abrir ninguna
    unidad nueva -- o abriendo una segunda pero SOLAPADA con la primera en
    vez de en secuencia) deja pasar los 595 tests existentes: el contador de
    aperturas totales (test 14, `test_camino_ok_abre_exactamente_dos_unidades_de_trabajo`)
    sigue viendo `[2]` porque cuenta aperturas, no cuántas siguen abiertas a
    la vez. Aquí se instrumenta el contador NETO (aperturas menos cierres) y
    se comprueba que vale 0 en el instante exacto en que
    `FakeLLMProvider.run_agent` se ejecuta -- una conexión de PostgreSQL
    retenida ahí bloquea el pool durante toda la espera al modelo."""
    env = _make_environment()
    tracked_work, open_units = net_counting_work_factory(env.work)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=1200, tokens_out=300)
    probe = _OpenUnitsProbeProvider(fake, open_units)
    runner = _make_runner(work=tracked_work, provider=probe)

    result = await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert result.outcome is AttemptOutcome.OK
    assert probe.observed_at_call == [0], (
        "ninguna unidad de trabajo debe seguir abierta cuando se llama a run_agent() "
        "-- una unidad retenida aquí bloquea una conexión del pool de PostgreSQL "
        "durante toda la espera al LLM (ADR 0006 § 2)"
    )


# --- 18. tokens_spent acumula el intento inválido más el fallo terminal ----
# --- que le sigue: la cifra que T44 va a consumir ---------------------------


@pytest.mark.parametrize(
    ("make_error", "expected_outcome", "expected_status"),
    [
        (
            lambda: LLMRateLimited("límite alcanzado", tokens_in=400, tokens_out=10),
            AttemptOutcome.RATE_LIMITED,
            AgentCallStatus.ERROR,
        ),
        (
            lambda: LLMTimeout("no respondió a tiempo", tokens_in=400, tokens_out=10),
            AttemptOutcome.TIMEOUT,
            AgentCallStatus.TIMEOUT,
        ),
        (
            lambda: LLMError("fallo genérico del agente", tokens_in=400, tokens_out=10),
            AttemptOutcome.AGENT_ERROR,
            AgentCallStatus.ERROR,
        ),
    ],
    ids=["rate_limited", "timeout", "agent_error"],
)
async def test_tokens_spent_acumula_intento_invalido_mas_fallo_terminal_que_le_sigue(
    make_error, expected_outcome, expected_status
):
    """Mutación superviviente: cambiar, en la construcción del
    `RunnerResult` de fallo terminal (`_record_terminal_failure`),
    `tokens_spent + call.total_tokens` por `call.total_tokens` a secas deja
    pasar los 595 tests existentes, porque ningún test combinaba "intento 1
    con salida inválida" (que deja `tokens_spent` > 0 al entrar en el
    segundo intento) con "intento 2 termina en RATE_LIMITED/TIMEOUT/
    AGENT_ERROR" (el único camino que pasa por `_record_terminal_failure`
    con un `tokens_spent` acumulado distinto de cero). La base de datos
    quedaría igual de bien en ambos casos (dos `AgentCall` correctos) --
    esto es puramente sobre lo que `RunnerResult.tokens_spent` reporta, la
    cifra que T44 va a consumir para decidir si queda presupuesto para el
    Editor."""
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=300, tokens_out=30)
    fake.fail(AgentRole.READER, error=make_error())
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)
    build = _build_that_raises(InvalidAgentOutput("json que no parsea, de prueba"))

    result = await runner.run(prompt="prompt", item_id=None, build=build)

    assert result.outcome is expected_outcome
    assert result.value is None
    assert result.attempts == 2
    assert len(fake.calls) == 2
    assert len(env.agent_calls.calls) == 2
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert env.agent_calls.calls[1].status is expected_status
    assert result.tokens_spent == (300 + 30) + (400 + 10), (
        "tokens_spent debe sumar el intento inválido (330) MÁS el fallo terminal que "
        "le sigue (410), no solo el último intento"
    )


# --- 19. La asimetría and->or de _record_cancelled_spend, llevada al runner


async def test_cancelled_error_con_solo_tokens_in_registra_el_gasto():
    """Equivalente de
    `test_read_item.py::test_cancelled_error_con_solo_tokens_in_registra_el_gasto`,
    llevado al runner: hoy solo moría por la suite del Reader, dejando
    desnuda la maquinaria de la que depende también Popularizer y Editor.
    La guarda de `_record_cancelled_spend` (`if not tokens_in and not
    tokens_out: return`) solo se salta el registro cuando AMBOS son cero --
    `and`, no `or`. Con `or` en vez de `and`, esta cancelación perdería en
    silencio los 500 tokens de entrada ya cobrados por la suscripción."""
    env = _make_environment()
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop, sin salida todavía")
    cancelled.tokens_in = 500  # type: ignore[attr-defined]
    cancelled.tokens_out = 0  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert exc_info.value is cancelled
    assert len(env.agent_calls.calls) == 1, (
        "tokens_in=500/tokens_out=0 debe contabilizarse: solo el caso 0/0 se descarta"
    )
    call = env.agent_calls.calls[0]
    assert call.tokens_in == 500
    assert call.tokens_out == 0


async def test_cancelled_error_con_solo_tokens_out_registra_el_gasto():
    """Simétrico del anterior: `tokens_in=0` con `tokens_out` no nulo."""
    env = _make_environment()
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop, entrada no adjuntada")
    cancelled.tokens_in = 0  # type: ignore[attr-defined]
    cancelled.tokens_out = 250  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2)

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert exc_info.value is cancelled
    assert len(env.agent_calls.calls) == 1, (
        "tokens_in=0/tokens_out=250 debe contabilizarse: solo el caso 0/0 se descarta"
    )
    call = env.agent_calls.calls[0]
    assert call.tokens_in == 0
    assert call.tokens_out == 250


# --- 20. Presupuesto agotado justo antes del reintento: sin segunda llamada


async def test_presupuesto_agotado_antes_del_reintento_no_hay_segunda_llamada():
    """Equivalente de
    `test_read_item.py::test_presupuesto_agotado_antes_del_reintento_no_hay_segunda_llamada`,
    llevado al runner: hoy solo moría por la suite del Reader. Fija que el
    reintento del bucle de `run()` pasa siempre por `authorize()` de nuevo
    -- saltárselo (llamar directamente a `run_agent` de nuevo sin volver a
    autorizar) dejaría gastar por encima del presupuesto denegado."""
    env = _make_environment(
        policy=_policy(editor_reserve_tokens=0),
        run=_make_run(budget_tokens=1_000),
    )
    fake = FakeLLMProvider()
    # Primer intento: JSON inválido, gasta 950 de los 1000 disponibles.
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=900, tokens_out=50)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=2, estimated_tokens=100)
    build = _build_that_raises(InvalidAgentOutput("json que no parsea, de prueba"))

    with pytest.raises(BudgetDenied):
        await runner.run(prompt="prompt", item_id=None, build=build)

    assert len(fake.calls) == 1, "el reintento no debe llegar a llamar al proveedor"
    assert len(env.agent_calls.calls) == 1
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT


# --- 21. except BaseException (no Exception) alrededor de build ------------


async def test_build_lanza_una_excepcion_que_no_hereda_de_exception_registra_y_relanza_intacta():
    """`except BaseException`, no `except Exception`, alrededor de `build`
    (`run()`, tras el `except (InvalidAgentOutput, InvariantViolation)`):
    entre el `return` de `run_agent` y la ejecución de `build` no hay ningún
    `await`, así que asyncio no puede entregar una `CancelledError` en esa
    ventana -- pero una señal (`KeyboardInterrupt`, `SystemExit`) sí, y el
    corte de `hard_stop` a las 04:45 (T44) es precisamente cuando llegan
    señales. Un test con `TypeError` (subclase de `Exception`, como los
    tests 9/10 de este fichero) no distingue `except BaseException` de
    `except Exception`: ambos la capturan igual. Este test usa `SystemExit`,
    que NO hereda de `Exception`, para que solo `except BaseException`
    pueda estar detrás del comportamiento observado."""
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=700, tokens_out=60)
    runner = _make_runner(work=env.work, provider=fake, max_attempts=3)
    bug = SystemExit("señal recibida durante build, de prueba")
    build = _build_that_raises(bug)

    with pytest.raises(SystemExit) as exc_info:
        await runner.run(prompt="prompt", item_id=None, build=build)

    assert exc_info.value is bug, (
        "debe relanzarse la MISMA instancia, no una copia ni una traducción"
    )
    assert len(fake.calls) == 1, "una señal durante build no se reintenta"
    assert len(env.agent_calls.calls) == 1, (
        "la llamada al LLM ya se pagó antes de que build() reventara: debe quedar un "
        "AgentCall aunque la excepción no herede de Exception"
    )
    call = env.agent_calls.calls[0]
    assert call.status is AgentCallStatus.ERROR
    assert call.tokens_in == 700
    assert call.tokens_out == 60


# --- 22. Los dos logging.warning nuevos del "else" de "current_run is not --
# --- None": RunRepository.get(run_id) devuelve None sin lanzar -------------


def _make_environment_run_not_found_by_id(
    *, policy: BudgetPolicy | None = None, run: Run | None = None
) -> tuple[AgentWorkFactory, InMemoryAgentCallRepository]:
    """Como `_make_environment`, pero el `RunRepository` que ve `AgentWork`
    (y por tanto `AgentRunner`) es `RunNotFoundByIdRepository`: su
    `current()` funciona con normalidad (así que `authorize()` y la lectura
    de `run_id` en `run()` no se ven afectadas), pero su `get(run_id)` -- el
    que usan `_record_cancelled_spend`/`_record_build_failure` para releer
    el Run dentro de su propia unidad de trabajo -- siempre devuelve `None`,
    sin lanzar. El `BudgetGuard` se construye contra un `InMemoryRunRepository`
    normal y separado, así que `authorize()` (que sí necesita encontrar el
    Run) no se ve afectado por esta simulación."""
    resolved_policy = policy or _policy()
    resolved_run = run or _make_run(budget_tokens=resolved_policy.nightly_tokens)
    guard_runs = InMemoryRunRepository(resolved_run)
    work_runs = RunNotFoundByIdRepository(resolved_run)
    items = InMemoryItemRepository()
    readings = InMemoryReadingRepository()
    agent_calls = InMemoryAgentCallRepository()
    guard = BudgetGuard(
        run_id=resolved_run.id,
        policy=resolved_policy,
        runs=guard_runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )
    work = make_work_factory(
        guard=guard, runs=work_runs, items=items, readings=readings, agent_calls=agent_calls
    )
    return work, agent_calls


async def test_run_get_devuelve_none_durante_cancelacion_emite_warning_y_no_registra_nada(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """Cubre el `else` nuevo de `_record_cancelled_spend` (antes de esta
    revisión, `current_run is None` no tenía tratamiento propio: caía
    directo a intentar `w.guard.record_call(call, None)`, que reventaba con
    una excepción distinta y por tanto SÍ pasaba por el `except Exception`
    de fuera, dejando el mismo `logging.warning` pero por el camino
    equivocado). Con `runs.get(run_id)` devolviendo `None` sin lanzar, el
    camino correcto es el `else` explícito: se registra el warning y no se
    llama a `record_call` en absoluto, sin que eso sustituya la
    `CancelledError` original."""
    monkeypatch.setattr(runner_module._logger, "disabled", False)
    work, agent_calls = _make_environment_run_not_found_by_id()
    fake = FakeLLMProvider()
    cancelled = asyncio.CancelledError("corte de hard_stop")
    cancelled.tokens_in = 500  # type: ignore[attr-defined]
    cancelled.tokens_out = 20  # type: ignore[attr-defined]
    fake.fail(AgentRole.READER, error=cancelled)
    runner = _make_runner(work=work, provider=fake, max_attempts=2)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(asyncio.CancelledError) as exc_info:
            await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert exc_info.value is cancelled, (
        "RunRepository.get(run_id) devolviendo None no debe sustituir la CancelledError original"
    )
    assert agent_calls.calls == [], "sin Run encontrado, no hay contra qué registrar el AgentCall"
    assert any(
        "RunRepository.get(run_id) devolvió None" in record.getMessage()
        and "cancelada" in record.getMessage()
        and record.levelno == logging.WARNING
        for record in caplog.records
    ), (
        "el gasto ya cobrado por la suscripción, sin Run encontrado, debe "
        "quedar registrado con warning"
    )


async def test_run_get_devuelve_none_durante_fallo_de_build_emite_warning_y_no_registra_nada(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """Simétrico del anterior, para el `else` nuevo de `_record_build_failure`:
    `build` revienta con una excepción que no es `InvalidAgentOutput` ni
    `InvariantViolation`, y `runs.get(run_id)` devuelve `None` sin lanzar.
    Sin este warning, tokens ya pagados por la suscripción desaparecen sin
    ningún rastro, ni siquiera en el log."""
    monkeypatch.setattr(runner_module._logger, "disabled", False)
    work, agent_calls = _make_environment_run_not_found_by_id()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"x": 1}, tokens_in=700, tokens_out=60)
    runner = _make_runner(work=work, provider=fake, max_attempts=3)
    bug = TypeError("bug de programación en build, de prueba")
    build = _build_that_raises(bug)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(TypeError) as exc_info:
            await runner.run(prompt="prompt", item_id=None, build=build)

    assert exc_info.value is bug, (
        "RunRepository.get(run_id) devolviendo None no debe sustituir la excepción "
        "original de build()"
    )
    assert agent_calls.calls == [], "sin Run encontrado, no hay contra qué registrar el AgentCall"
    assert any(
        "RunRepository.get(run_id) devolvió None" in record.getMessage()
        and "build()" in record.getMessage()
        and record.levelno == logging.WARNING
        for record in caplog.records
    ), (
        "el gasto ya cobrado por la suscripción, sin Run encontrado, debe "
        "quedar registrado con warning"
    )


# --- Bonus: el rol configurado viaja al AgentRequest, para los tres roles --


@pytest.mark.parametrize("role", list(AgentRole))
async def test_role_configurado_viaja_al_agent_request_para_cualquier_rol(role: AgentRole):
    """No es de la lista mínima, pero fija por qué el runner sirve igual
    para Reader, Popularizer y Editor (docstring de `runner.py`): el rol no
    está cableado en ningún punto de `run()`, viaja desde el constructor."""
    env = _make_environment()
    fake = FakeLLMProvider()
    fake.respond(role, json={"x": 1}, tokens_in=100, tokens_out=10)
    runner = _make_runner(work=env.work, provider=fake, role=role)

    await runner.run(prompt="prompt", item_id=None, build=_build_ok)

    assert fake.calls[0].role is role
    assert env.agent_calls.calls[0].agent is role
