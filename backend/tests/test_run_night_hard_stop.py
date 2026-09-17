"""Tests de corte por `hard_stop` de `RunNight` (T44, paso 4), con reloj inyectado.

Hermano de `test_run_night.py`: ese fichero ya tiene un humo de este camino
(`test_hard_stop_cancela_la_llamada_en_vuelo_y_cierra_killed`, con un
`deadline_s` corto y un proveedor lento) -- este fichero completa la
batería propia del vigía (`_watch_hard_stop`/`_build_killed_result`,
docstring de `run_night.py`, "Cancelación por hard_stop"), sin esperar
nunca a las 04:45 de verdad: todos los `deadline_s` son fracciones de
segundo, y `FakeClock` (`tests/fakes/clock.py`) se fija a una hora de
sabor narrativo (04:44:30, dentro de la ventana) que `RunNight` no usa para
calcular el corte -- ese cálculo (`deadline_s, deadline_reason =
cli._deadline_for_run_night(policy, now)`) lo hace `cli.py`, no este
módulo (ver el propio docstring de `run_night.py`, "Por qué deadline_s <=
0 no arranca ningún vigía"), así que aquí se pasa ya resuelto, como en
producción.

Ningún test llama a Claude (`.claude/skills/testing-without-claude`); sin
base de datos.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import textwrap
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryFindingRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    make_work_factory,
)

from nocturna import cli
from nocturna.application.budget import (
    BudgetGuard,
    BudgetPolicy,
    seconds_until_hard_stop,
)
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.edit_night import EditNight
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.popularize_reading import PopularizeReading
from nocturna.application.use_cases.read_item import ReadItem
from nocturna.application.use_cases.run_night import RunNight
from nocturna.domain.entities import AgentCallStatus, Item, Run, RunStatus
from nocturna.domain.llm import AgentRequest, AgentResult

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW_NEAR_HARD_STOP = datetime(2026, 1, 1, 4, 44, 30, tzinfo=UTC)
_PAST_HARD_STOP = datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)


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


def _make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "source": "arxiv",
        "external_id": f"2501.{uuid4().hex[:5]}",
        "title": "Un título de prueba",
        "abstract": "Un abstract de prueba con contenido suficiente para el Reader.",
        "categories": ["astro-ph.GA"],
        "published_at": _WITHIN_WINDOW_NEAR_HARD_STOP,
        "fetched_at": _WITHIN_WINDOW_NEAR_HARD_STOP,
    }
    defaults.update(overrides)
    return Item(**defaults)


class _Environment:
    def __init__(self, *, items: list[Item], policy: BudgetPolicy, now: datetime) -> None:
        self.run = Run(started_at=now, budget_tokens=policy.nightly_tokens)
        self.runs = InMemoryRunRepository(self.run)
        self.items = InMemoryItemRepository(*items)
        self.readings = InMemoryReadingRepository()
        self.findings = InMemoryFindingRepository()
        self.agent_calls = InMemoryAgentCallRepository()
        self.clock = FakeClock(now)
        self.guard = BudgetGuard(
            run_id=self.run.id,
            policy=policy,
            runs=self.runs,
            agent_calls=self.agent_calls,
            clock=self.clock,
        )
        self.work: AgentWorkFactory = make_work_factory(
            guard=self.guard,
            runs=self.runs,
            items=self.items,
            readings=self.readings,
            findings=self.findings,
            agent_calls=self.agent_calls,
        )


def _make_run_night(
    *,
    env: _Environment,
    provider: object,
    ingest_result: IngestResult,
    deadline_s: float,
    deadline_reason: str = "hard_stop",
) -> RunNight:
    read_item = ReadItem(
        work=env.work,
        provider=provider,
        system_prompt="prompt del Reader",
        prompt_version="reader-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=100,
        max_attempts=1,
    )
    popularize = PopularizeReading(
        work=env.work,
        provider=provider,
        system_prompt="prompt del Popularizer",
        prompt_version="popularizer-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=100,
        max_attempts=1,
        min_interest_score=4,
    )
    edit_night = EditNight(
        work=env.work,
        provider=provider,
        clock=env.clock,
        system_prompt="prompt del Editor",
        prompt_version="editor-v1",
        model="claude-opus-test",
        max_turns=3,
        max_attempts=1,
        base_tokens=100,
        tokens_per_candidate=50,
    )

    async def _ingest() -> IngestResult:
        return ingest_result

    return RunNight(
        work=env.work,
        clock=env.clock,
        ingest=_ingest,
        read_item=read_item,
        popularize=popularize,
        edit_night=edit_night,
        run_id=env.run.id,
        max_items=10,
        max_consecutive_failures=5,
        deadline_s=deadline_s,
        deadline_reason=deadline_reason,
    )


class _SlowProviderWithPartialUsage:
    """Doble de `LLMProvider` que tarda más que cualquier `deadline_s` de
    este fichero y, al ser cancelado, adjunta `tokens_in`/`tokens_out` a la
    `CancelledError` antes de relanzarla -- el mismo patrón que
    `AgentSDKProvider.run_agent` documenta para un `ResultMessage` con
    `usage` ya visto antes del corte (T41, `runner.py::
    _record_cancelled_spend`). Sin este adjunto, el test 16 no podría
    demostrar la lección de T41: que el gasto ya incurrido se registra
    igual aunque la llamada nunca complete."""

    def __init__(self, *, tokens_in: int, tokens_out: int, sleep_s: float = 10) -> None:
        self.calls: list[AgentRequest] = []
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self._sleep_s = sleep_s

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        try:
            await asyncio.sleep(self._sleep_s)
        except asyncio.CancelledError as exc:
            exc.tokens_in = self._tokens_in  # type: ignore[attr-defined]
            exc.tokens_out = self._tokens_out  # type: ignore[attr-defined]
            raise
        raise AssertionError("no debería completarse: el vigía debe cancelarla antes")


# --- 16. Reloj cerca de hard_stop, proveedor lento: KILLED, AgentCall con tokens


async def test_reloj_a_04_44_30_con_proveedor_lento_registra_el_agent_call_cancelado():
    """`_WITHIN_WINDOW_NEAR_HARD_STOP` fija el reloj a las 04:44:30 -- puro
    sabor narrativo, como explica el docstring del módulo: `deadline_s` se
    pasa ya resuelto (0.05 s, para no esperar de verdad los 30 s reales que
    ese instante representaría). El vigía cancela la única llamada en
    vuelo; la `CancelledError` que recibe `AgentRunner.run` trae
    `tokens_in`/`tokens_out` adjuntos, así que `_record_cancelled_spend`
    (T41) deja un `AgentCall` real -- la lección de T41: el gasto ya
    incurrido por la suscripción se contabiliza igual, aunque la llamada no
    complete nunca."""
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW_NEAR_HARD_STOP)
    provider = _SlowProviderWithPartialUsage(tokens_in=500, tokens_out=20)
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(
        env=env, provider=provider, ingest_result=ingest_result, deadline_s=0.05
    )

    result = await run_night()

    assert result.status is RunStatus.KILLED
    assert "end=hard_stop" in result.notes
    assert len(provider.calls) == 1, "el vigía corta la única llamada en vuelo"
    assert len(env.agent_calls.calls) == 1, (
        "el gasto ya incurrido por la llamada cancelada debe dejar un AgentCall "
        "(T41: `AgentRunner._record_cancelled_spend`)"
    )
    call = env.agent_calls.calls[0]
    assert call.tokens_in == 500
    assert call.tokens_out == 20
    assert call.status is AgentCallStatus.ERROR
    assert result.tokens_used == 520
    editor_role_calls = [c for c in provider.calls if c.role.value == "editor"]
    assert editor_role_calls == [], "el Editor nunca llega a llamarse: la noche se cortó antes"


# --- 17. Reloj ya pasado hard_stop al arrancar: cero llamadas, KILLED por OUTSIDE_WINDOW


async def test_reloj_ya_pasado_hard_stop_al_arrancar_cero_llamadas_killed_por_outside_window():
    """`deadline_s <= 0` (la ventana ya está cerrada al construir `RunNight`,
    ver el docstring del módulo): no arranca ningún vigía, pero el primer
    `authorize()` de la fase A deniega con `OUTSIDE_WINDOW` antes de
    construir ningún `AgentRequest` -- cero llamadas al proveedor, la misma
    `KILLED` que produciría el vigía, sin necesidad de cancelar nada."""
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_PAST_HARD_STOP)
    fake = FakeLLMProvider()
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result, deadline_s=0)

    result = await run_night()

    assert fake.calls == [], "OUTSIDE_WINDOW deniega antes de llamar al proveedor"
    assert result.status is RunStatus.KILLED
    assert "end=outside_window" in result.notes
    assert env.agent_calls.calls == []


# --- 18. Cancelación externa: se relanza, no se disfraza de KILLED --------


async def test_cancelacion_externa_no_disfrazada_de_killed_se_relanza():
    """Un `deadline_s` grande (100 s: el vigía de esta noche nunca llega a
    dispararse) y una cancelación disparada desde fuera -- un Ctrl-C, el
    proceso padre -- sobre la propia tarea que ejecuta `run_night()`:
    `self._hard_stop_fired` sigue en `False`, así que `__call__` relanza la
    `CancelledError` tal cual, sin construir ningún `RunNightResult(status=
    KILLED, ...)`."""
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW_NEAR_HARD_STOP)
    provider = _SlowProviderWithPartialUsage(tokens_in=500, tokens_out=20, sleep_s=10)
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(
        env=env, provider=provider, ingest_result=ingest_result, deadline_s=100
    )

    outer_task = asyncio.create_task(run_night())
    await asyncio.sleep(0.05)  # deja que la noche entre en la llamada al proveedor
    outer_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await outer_task


# --- 19. deadline_s = min(seconds_until_hard_stop, run_timeout_s), con motivo


def test_deadline_for_run_night_distingue_hard_stop_de_run_timeout():
    """Caracterización de `cli._deadline_for_run_night` (revisión de T44,
    punto 2): antes de esa revisión, la fórmula
    `deadline_s = min(seconds_until_hard_stop(...), policy.run_timeout_s)`
    vivía inline en `_run_night_for_real` y no era importable de forma
    aislada sin construir todo el composition root -- extraerla a una
    función de nivel de módulo es precisamente lo que permite este test.
    Dos ramas: la ventana manda (`"hard_stop"`) cuando queda menos tiempo
    hasta `hard_stop` que `run_timeout_s` (el caso real de una noche que
    arranca tarde), y `run_timeout_s` manda (`"run_timeout"`) cuando la
    ventana entera todavía cabe por delante (el caso real de hoy:
    `run_timeout_s = 16_200` s frente a una ventana de 4h45 = 17_100 s
    completos -- una noche que arranca a las 00:00 la corta el timeout de
    ejecución, no la ventana)."""
    window_start = time(0, 0)
    window_hard_stop = time(4, 45)

    # Rama 1: queda menos ventana que run_timeout_s -> manda la ventana.
    now_tarde = datetime(2026, 1, 1, 4, 30, 0, tzinfo=UTC)  # 15 min = 900 s hasta hard_stop
    policy_tarde = _policy(run_timeout_s=16_200)  # 4h30, mucho más que los 900 s que quedan
    remaining = seconds_until_hard_stop(now_tarde, window_start, window_hard_stop)
    deadline_s, deadline_reason = cli._deadline_for_run_night(policy_tarde, now_tarde)
    assert remaining == 900
    assert deadline_s == 900, "con menos ventana que run_timeout_s, manda la ventana"
    assert deadline_reason == "hard_stop"

    # Rama 2: la ventana entera cabe, pero run_timeout_s es más corto -> manda run_timeout_s.
    now_temprano = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)  # toda la ventana por delante
    policy_temprano = _policy(run_timeout_s=3_600)  # 1h, menos que las 4h45 completas de ventana
    remaining_temprano = seconds_until_hard_stop(now_temprano, window_start, window_hard_stop)
    deadline_s_temprano, deadline_reason_temprano = cli._deadline_for_run_night(
        policy_temprano, now_temprano
    )
    assert remaining_temprano == 4 * 3600 + 45 * 60
    assert deadline_s_temprano == 3_600, "con la ventana entera por delante, manda run_timeout_s"
    assert deadline_reason_temprano == "run_timeout"

    # La configuración real de hoy (config/pipeline.toml): run_timeout_s=16_200 s,
    # window 00:00-04:45 = 17_100 s completos -- exactamente la rama 2, la que
    # motivó la revisión de T44 punto 2 (antes de ella, notes decía
    # end=hard_stop en este caso, no end=run_timeout).
    now_medianoche = datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC)
    policy_real = _policy(run_timeout_s=16_200)
    deadline_s_real, deadline_reason_real = cli._deadline_for_run_night(policy_real, now_medianoche)
    assert deadline_s_real == 16_200
    assert deadline_reason_real == "run_timeout"


async def test_run_night_con_deadline_reason_run_timeout_lo_refleja_en_notes():
    """A nivel de `RunNight`, no solo de `cli.py`: si el vigía dispara con
    `deadline_reason="run_timeout"` (el valor que `_run_night_for_real`
    pasaría cuando manda `limits.run_timeout_s`, ver el test anterior),
    `notes` debe decir `end=run_timeout`, no `end=hard_stop` -- el mismo
    vigía, el mismo `asyncio.CancelledError`, pero el motivo que se fuerza
    en `_build_killed_result` depende del parámetro recibido por
    constructor (revisión de T44, puntos 1 y 2: el motivo se fuerza sin
    condición, y el valor forzado es configurable, no un literal fijo)."""
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW_NEAR_HARD_STOP)
    provider = _SlowProviderWithPartialUsage(tokens_in=500, tokens_out=20)
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(
        env=env,
        provider=provider,
        ingest_result=ingest_result,
        deadline_s=0.05,
        deadline_reason="run_timeout",
    )

    result = await run_night()

    assert result.status is RunStatus.KILLED
    assert "end=run_timeout" in result.notes
    assert "end=hard_stop" not in result.notes


# --- 20. El manejador de cancelación no ejecuta ningún await --------------


def test_build_killed_result_no_contiene_ningun_await_en_su_cuerpo():
    """Test estructural (AST) sobre el propio módulo: `_build_killed_result`
    documenta explícitamente "solo trabajo síncrono... ningún await en ese
    camino" (docstring de `run_night.py`, "Cancelación por hard_stop") --
    una segunda entrega de cancelación mientras se construye el resultado
    de la primera sería el mismo bug que T41 ya pagó una vez
    (`AgentRunner._record_cancelled_spend`). Este test lo congela: si algún
    cambio futuro le añade un `await`, debe fallar aquí, no en producción a
    las 04:45 de una noche real."""
    source = textwrap.dedent(inspect.getsource(RunNight._build_killed_result))
    tree = ast.parse(source)
    function_def = tree.body[0]
    assert isinstance(function_def, ast.AsyncFunctionDef | ast.FunctionDef)

    await_nodes = [node for node in ast.walk(function_def) if isinstance(node, ast.Await)]
    assert await_nodes == [], (
        "_build_killed_result no debe contener ningún 'await': es el manejador de "
        "cancelación (docstring del módulo, 'Cancelación por hard_stop') y debe hacer "
        "solo trabajo síncrono"
    )


def test_build_killed_result_no_es_una_corrutina():
    """Complemento del test anterior, contra el objeto real en vez de contra
    su fuente: si `_build_killed_result` alguna vez se declarara `async def`
    por descuido, ni siquiera haría falta que tuviera un `await` dentro para
    abrir la misma ventana de doble entrega -- toda corrutina es un punto de
    suspensión en potencia en cuanto alguien la `await`ea. Congelar que
    sigue siendo una función síncrona normal es la otra mitad de la garantía
    "ningún await en el camino de KILLED"."""
    assert not inspect.iscoroutinefunction(RunNight._build_killed_result)
