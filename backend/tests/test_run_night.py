"""Tests de `application/use_cases/run_night.py::RunNight` (T44, paso 4).

Empezó (paso 2) como humo mínimo -- tres caminos, para demostrar que el
módulo arranca y que las tres fases se encadenan en el orden que fija
`CLAUDE.md` --; el `tester` amplía aquí la batería completa: el orden de
gasto exacto, el tope de `max_items`, el umbral del Popularizer (que este
módulo NO comprueba dos veces -- vive una única vez dentro de
`PopularizeReading`), el cortacircuitos de fallos consecutivos, cada motivo
de `BudgetDenied` en cada fase, el modelo de degradación monótona campo a
campo, y la ingesta fallida. El corte por `hard_stop` con reloj inyectado
vive en `test_run_night_hard_stop.py`, hermano de este fichero -- el humo
original (test 3, más abajo) ya cubre el camino "el vigía corta una llamada
en vuelo", pero sin ejercitar el reloj ni el cálculo de `deadline_s` que
hace `cli.py`.

Mismo patrón que `test_read_item.py`/`test_edit_night.py` (T41-T43, "la
plantilla"): sin base de datos, `FakeLLMProvider` y un `AgentWorkFactory`
en memoria -- ningún test llama a Claude
(`.claude/skills/testing-without-claude`).

## Dos divergencias, verificadas a mano contra el código y su propio
## docstring antes de escribir el test, no tratadas como bugs de este módulo

- **Un único desenlace `AGENT_ERROR`/`TIMEOUT`/`INVALID_OUTPUT` del Reader,
  sin alcanzar el cortacircuitos, NO degrada la noche.** El docstring de
  `run_night.py` ("El modelo de degradación monótona...") lista
  explícitamente qué sucesos degradan `status`: ingesta fallida,
  `BudgetDenied`, `RATE_LIMITED`, el cortacircuitos de
  `max_consecutive_failures`, y un `EditOutcome` que no sea
  `EDITED`/`NO_CANDIDATES`. Un fallo aislado del Reader que no sea
  `RATE_LIMITED` ni dispare el cortacircuitos no está en esa lista, y
  verificado contra el código (no solo contra el docstring): la noche cierra
  `COMPLETED` si el resto transcurre limpio. `test_agent_error_...` de más
  abajo prueba ese comportamiento real, no el `PARTIAL` que un lector
  apresurado del plan de esta tarea podría esperar de "un ítem falló".
- **`BUDGET_EXHAUSTED` en la fase B degrada a `PARTIAL` aunque el Editor
  llegue a publicar.** Mismo docstring, párrafo "BudgetDenied en la fase A o
  B": "degrada a `terminal_status_for(reason)` (siempre `PARTIAL` para estos
  tres motivos) y la noche sigue hacia la fase siguiente" -- la degradación
  a `PARTIAL` ocurre en el momento de la denegación, antes de que el Editor
  llegue a correr, y `_degrade` es monótono: el éxito posterior del Editor
  nunca la revierte a `COMPLETED`. Verificado contra el código, no solo
  contra el docstring. `test_budget_exhausted_en_fase_b_...` de más abajo
  prueba ese `PARTIAL`, con el Editor efectivamente llamado y publicando --
  la reserva intacta es real, pero no basta para que la noche cierre
  `COMPLETED`.
"""

from __future__ import annotations

import asyncio
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

from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases import run_night as run_night_module
from nocturna.application.use_cases.edit_night import EditNight
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.popularize_reading import PopularizeReading
from nocturna.application.use_cases.read_item import ReadItem
from nocturna.application.use_cases.run_night import RunNight, RunNightResult
from nocturna.domain.entities import AgentCall, AgentCallStatus, Item, ItemStatus, Run, RunStatus
from nocturna.domain.errors import LLMError, LLMRateLimited
from nocturna.domain.llm import AgentRequest, AgentResult, AgentRole

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
        "interest_score": 5,
    }
    defaults.update(overrides)
    return defaults


def _valid_popularizer_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "title": "Un titular de prueba",
        "level_curious": "Nivel curioso de prueba",
        "level_amateur": "Nivel aficionado de prueba",
        "level_technical": "Nivel técnico de prueba",
    }
    defaults.update(overrides)
    return defaults


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
    provider: FakeLLMProvider,
    ingest_result: IngestResult,
    max_items: int = 10,
    max_consecutive_failures: int = 5,
    deadline_s: int = 16_200,
) -> RunNight:
    read_item = ReadItem(
        work=env.work,
        provider=provider,
        system_prompt="prompt del Reader",
        prompt_version="reader-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=500,
        max_attempts=2,
    )
    popularize = PopularizeReading(
        work=env.work,
        provider=provider,
        system_prompt="prompt del Popularizer",
        prompt_version="popularizer-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=500,
        max_attempts=2,
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
        max_attempts=2,
        base_tokens=1_000,
        tokens_per_candidate=200,
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
        max_items=max_items,
        max_consecutive_failures=max_consecutive_failures,
        deadline_s=deadline_s,
    )


# --- 1. Camino feliz: ingesta, Reader, Popularizer, Editor, en orden ------


async def test_camino_feliz_encadena_las_tres_fases_en_orden_y_cierra_completed():
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1000, tokens_out=200)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=800, tokens_out=150
    )
    fake.respond(
        AgentRole.EDITOR,
        json={
            "publish": [{"item_id": str(item.id), "confidence": 0.8, "reason": "Motivo de prueba."}]
        },
        tokens_in=1200,
        tokens_out=100,
    )
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    assert isinstance(result, RunNightResult)
    assert result.status is RunStatus.COMPLETED
    assert result.items_fetched == 1
    assert result.items_read == 1
    assert result.items_failed == 0
    assert result.candidates == 1
    assert result.findings_published == 1
    assert result.tokens_used == 1000 + 200 + 800 + 150 + 1200 + 100
    assert [call.role for call in fake.calls] == [
        AgentRole.READER,
        AgentRole.POPULARIZER,
        AgentRole.EDITOR,
    ]
    assert item.status is ItemStatus.PUBLISHED
    assert "end=edited" in result.notes
    # `RunNight` nunca cierra el Run ni toca su status: eso es de `cli.py`.
    assert env.run.status is RunStatus.RUNNING


# --- 2. Sin candidatos: la noche cierra COMPLETED igual, coste cero -------


async def test_sin_candidatos_cierra_completed_sin_llamar_al_proveedor():
    env = _Environment(items=[], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    ingest_result = IngestResult(
        fetched=0, new=0, duplicates=0, skipped=0, truncated=False, items=[]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    assert result.status is RunStatus.COMPLETED
    assert result.items_fetched == 0
    assert result.items_read == 0
    assert result.candidates == 0
    assert result.findings_published == 0
    assert result.tokens_used == 0
    assert fake.calls == []
    assert "end=no_candidates" in result.notes


# --- 3. hard_stop: el vigía cancela la noche en vuelo y cierra KILLED -----


class _SlowLLMProvider:
    """Doble de `LLMProvider` que tarda más que `deadline_s` en responder,
    para ejercitar de verdad el vigía de `RunNight` (no solo el camino de
    `BudgetDenied`, que ya cubre `.../test_read_item.py` et al.). No importa
    `claude_agent_sdk`; solo `asyncio.sleep`, permitido en cualquier test
    (`test_llm_call_sites.py` únicamente vigila imports del SDK real)."""

    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        await asyncio.sleep(10)
        raise AssertionError("no debería completarse: el vigía debe cancelarla antes")


async def test_hard_stop_cancela_la_llamada_en_vuelo_y_cierra_killed():
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW)
    provider = _SlowLLMProvider()
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(
        env=env, provider=provider, ingest_result=ingest_result, deadline_s=0.05
    )

    result = await run_night()

    assert result.status is RunStatus.KILLED
    assert "end=hard_stop" in result.notes
    assert len(provider.calls) == 1, "el vigía debe cortar la única llamada en vuelo"
    # El Run sigue vivo: cerrarlo es responsabilidad de `cli.py` (paso 3).
    assert env.run.status is RunStatus.RUNNING


# --- 2. Orden de gasto: Reader x N, luego Popularizer x M, Editor al final -


async def test_orden_de_gasto_reader_todos_luego_popularizer_candidatos_luego_editor():
    """Tres ítems, dos candidatos: la secuencia de roles en `fake.calls` es
    exactamente `[reader, reader, reader, popularizer, popularizer, editor]`
    -- el corazón de `CLAUDE.md` § "Control de gasto" (primero el Reader
    sobre todos los ítems, después el Popularizer sobre los candidatos, el
    Editor al final). El tercer ítem se lee (`ReadOutcome.READ`, cuenta como
    reader) pero nunca llega a llamar al Popularizer: su `interest_score`
    bajo lo descarta dentro de `PopularizeReading`, antes de cualquier
    `authorize` (test de umbral aparte, más abajo)."""
    item_a, item_b, item_c = _make_item(), _make_item(), _make_item()
    env = _Environment(items=[item_a, item_b, item_c], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=5), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=5), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=2), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=10)
    ingest_result = IngestResult(
        fetched=3, new=3, duplicates=0, skipped=0, truncated=False, items=[item_a, item_b, item_c]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    assert [call.role for call in fake.calls] == [
        AgentRole.READER,
        AgentRole.READER,
        AgentRole.READER,
        AgentRole.POPULARIZER,
        AgentRole.POPULARIZER,
        AgentRole.EDITOR,
    ]
    assert result.items_read == 3
    assert item_c.status is ItemStatus.DISCARDED, (
        "descartado por umbral, sin pasar por el Popularizer"
    )


# --- 3. El Editor se llama exactamente una vez, contado por rol -----------


async def test_el_editor_se_llama_exactamente_una_vez_contando_por_rol():
    item_a, item_b = _make_item(), _make_item()
    env = _Environment(items=[item_a, item_b], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.EDITOR,
        json={"publish": [{"item_id": str(item_a.id), "confidence": 0.6, "reason": "Motivo."}]},
        tokens_in=100,
        tokens_out=10,
    )
    ingest_result = IngestResult(
        fetched=2, new=2, duplicates=0, skipped=0, truncated=False, items=[item_a, item_b]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    editor_calls = [call for call in fake.calls if call.role is AgentRole.EDITOR]
    assert len(editor_calls) == 1
    assert result.status is RunStatus.COMPLETED


# --- 4. max_items: tope exacto de llamadas al Reader, ni una más ----------


async def test_max_items_limita_las_llamadas_al_reader_sin_una_mas():
    items = [_make_item() for _ in range(10)]
    env = _Environment(items=items, policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    for _ in range(3):
        fake.respond(
            AgentRole.READER,
            json=_valid_reading_json(interest_score=1),
            tokens_in=100,
            tokens_out=10,
        )
    ingest_result = IngestResult(
        fetched=10, new=10, duplicates=0, skipped=0, truncated=False, items=items
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result, max_items=3)

    result = await run_night()

    reader_calls = [call for call in fake.calls if call.role is AgentRole.READER]
    assert len(reader_calls) == 3, "next_unread(3) más ninguna llamada extra al agotar el límite"
    assert result.items_read == 3


# --- 5. Umbral de interest_score: sin llamada al Popularizer, DISCARDED ---


async def test_interest_score_bajo_no_llama_al_popularizer_y_descarta_el_item():
    """El umbral vive dentro de `PopularizeReading`, no en `RunNight`
    (docstring del módulo, "El umbral, antes de gastar nada" en
    `popularize_reading.py`): este test verifica que `RunNight` no lo
    reimplementa ni lo comprueba una segunda vez antes de llamar a
    `self._popularize` -- llama siempre, y es el caso de uso quien decide."""
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=2), tokens_in=100, tokens_out=10
    )
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    popularizer_calls = [call for call in fake.calls if call.role is AgentRole.POPULARIZER]
    assert popularizer_calls == [], "interest_score=2 < min_interest_score=4: sin llamada"
    assert item.status is ItemStatus.DISCARDED
    assert result.status is RunStatus.COMPLETED
    assert "end=no_candidates" in result.notes


# --- 6. AGENT_ERROR aislado en el Reader: no impide leer los siguientes ---


async def test_agent_error_aislado_en_el_reader_no_impide_leer_los_siguientes():
    """`item_a` falla con `ReadOutcome.AGENT_ERROR` (un `LLMError` del
    proveedor); `item_b` se lee con normalidad justo después. `items_failed`
    cuenta el fallo, pero el `status` final es `COMPLETED`, no `PARTIAL`:
    un único desenlace fallido del Reader que no es `RATE_LIMITED` y no
    dispara el cortacircuitos no está en la lista de sucesos que degradan
    (ver la nota del docstring de este fichero, verificada contra el código
    antes de escribir este test)."""
    item_a, item_b = _make_item(), _make_item()
    env = _Environment(items=[item_a, item_b], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=LLMError("fallo del agente", tokens_in=100, tokens_out=10))
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=10)
    ingest_result = IngestResult(
        fetched=2, new=2, duplicates=0, skipped=0, truncated=False, items=[item_a, item_b]
    )
    run_night = _make_run_night(
        env=env, provider=fake, ingest_result=ingest_result, max_consecutive_failures=5
    )

    result = await run_night()

    assert result.items_read == 1
    assert result.items_failed == 1
    assert result.status is RunStatus.COMPLETED, (
        "un único AGENT_ERROR, sin cortacircuitos ni RATE_LIMITED, no degrada la noche -- "
        "ver la nota del docstring de este fichero"
    )
    assert "read=1(failed=1)" in result.notes


# --- 7. Excepción inesperada en un ítem: aislada, la noche continúa -------


async def test_excepcion_inesperada_en_un_item_queda_aislada_y_la_noche_continua(
    caplog, monkeypatch
):
    """`item_a` dispara un `ValueError` crudo desde el proveedor -- no
    envuelto en `LLMError`/`LLMTimeout`/`LLMRateLimited`, así que ninguno de
    los `except` tipados de `AgentRunner.run` lo captura: escapa hasta el
    `except Exception` de `RunNight._phase_reader`, que lo aísla con
    `logging.exception` (nunca lo deja tumbar la noche) y sigue con
    `item_b`.

    `monkeypatch.setattr(run_night_module._logger, "disabled", False)`: al
    lanzar la suite completa, `tests/db/` corre antes (orden alfabético) y
    su fixture de migraciones invoca `alembic/env.py::fileConfig`, que
    deshabilita cualquier logger ya existente y no declarado en
    `alembic.ini` -- incluido el de `run_night.py`. Mismo motivo, mismo
    parche, que `test_agent_runner.py`/`test_read_item.py`/
    `test_json_repair.py`."""
    monkeypatch.setattr(run_night_module._logger, "disabled", False)
    item_a, item_b = _make_item(), _make_item()
    env = _Environment(items=[item_a, item_b], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=ValueError("bug inesperado, no un LLMError"))
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=10)
    ingest_result = IngestResult(
        fetched=2, new=2, duplicates=0, skipped=0, truncated=False, items=[item_a, item_b]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    with caplog.at_level("ERROR"):
        result = await run_night()

    assert result.items_read == 1, "item_b se lee igual, el fallo de item_a no la tumba"
    assert result.items_failed == 1
    assert any(
        record.levelname == "ERROR" and record.exc_info is not None for record in caplog.records
    ), "logging.exception debe dejar un registro con traceback, no tragarse el fallo en silencio"


# --- 8. Cortacircuitos de fallos consecutivos: aborta fase A, llama al Editor con lo que ya había


async def test_cortacircuitos_aborta_fase_a_pero_llama_al_editor_con_los_candidatos_previos():
    """`item_a` se lee con éxito (candidato); `item_b` e `item_c` fallan
    consecutivamente con `AGENT_ERROR` -- con `max_consecutive_failures=2`,
    la segunda falla dispara el cortacircuitos: la fase A se aborta ahí
    (`item_d` nunca se ofrece al Reader), pero `self._skip_editor` no se
    toca (a diferencia de `RATE_LIMITED`/`OUTSIDE_WINDOW`), así que el
    Editor sí se llama, con el único candidato que ya había."""
    item_a, item_b, item_c, item_d = (
        _make_item(),
        _make_item(),
        _make_item(),
        _make_item(),
    )
    env = _Environment(items=[item_a, item_b, item_c, item_d], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.fail(AgentRole.READER, error=LLMError("fallo 1", tokens_in=50, tokens_out=5))
    fake.fail(AgentRole.READER, error=LLMError("fallo 2", tokens_in=50, tokens_out=5))
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.EDITOR,
        json={"publish": [{"item_id": str(item_a.id), "confidence": 0.7, "reason": "Motivo."}]},
        tokens_in=100,
        tokens_out=10,
    )
    ingest_result = IngestResult(
        fetched=4,
        new=4,
        duplicates=0,
        skipped=0,
        truncated=False,
        items=[item_a, item_b, item_c, item_d],
    )
    run_night = _make_run_night(
        env=env, provider=fake, ingest_result=ingest_result, max_consecutive_failures=2
    )

    result = await run_night()

    reader_calls = [call for call in fake.calls if call.role is AgentRole.READER]
    editor_calls = [call for call in fake.calls if call.role is AgentRole.EDITOR]
    assert len(reader_calls) == 3, "item_d nunca se ofrece al Reader: el cortacircuitos ya cortó"
    assert len(editor_calls) == 1, "el cortacircuitos no toca self._skip_editor: el Editor se llama"
    assert result.status is RunStatus.PARTIAL
    assert "circuit_breaker_reader" in result.notes or "end=circuit_breaker_reader" in result.notes
    assert result.findings_published == 1, "el candidato leído antes del corte sí llega al Editor"


# --- 8b. SKIPPED_LOW_SCORE es neutro: no reinicia el cortacircuitos de B --


async def test_skipped_low_score_no_reinicia_el_cortacircuitos_del_popularizer():
    """Revisión de T44, punto 4: `PopularizeOutcome.SKIPPED_LOW_SCORE` no
    implica ninguna llamada al proveedor (`PopularizeReading` aplica el
    umbral antes de cualquier `authorize`), así que no es evidencia de que
    el proveedor esté sano -- antes de esta revisión, sí contaba como
    "bueno" y hacía `consecutive_bad = 0`, con lo que una racha de fallos
    de verdad intercalada con descartes por umbral bajo nunca disparaba el
    cortacircuitos.

    `item_b` falla de verdad (`AGENT_ERROR`, `consecutive_bad` pasa a 1);
    `item_c` se descarta por `interest_score` bajo -- neutro, sin tocar el
    contador; `item_d` vuelve a fallar de verdad -- con `consecutive_bad`
    sin resetear por el medio, esta es la SEGUNDA falla consecutiva de
    verdad, y con `max_consecutive_failures=2` dispara el cortacircuitos
    ahí mismo: `item_e` nunca llega a ofrecerse al Popularizer. Con el bug
    de antes de esta revisión, `item_c` habría reseteado el contador a 0 y
    el cortacircuitos nunca habría disparado dentro de esta noche."""
    item_b, item_c, item_d, item_e = (
        _make_item(),
        _make_item(),
        _make_item(),
        _make_item(),
    )
    env = _Environment(items=[item_b, item_c, item_d, item_e], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=5), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=2), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=5), tokens_in=100, tokens_out=10
    )
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=5), tokens_in=100, tokens_out=10
    )
    fake.fail(AgentRole.POPULARIZER, error=LLMError("fallo 1", tokens_in=50, tokens_out=5))
    fake.fail(AgentRole.POPULARIZER, error=LLMError("fallo 2", tokens_in=50, tokens_out=5))
    ingest_result = IngestResult(
        fetched=4,
        new=4,
        duplicates=0,
        skipped=0,
        truncated=False,
        items=[item_b, item_c, item_d, item_e],
    )
    run_night = _make_run_night(
        env=env, provider=fake, ingest_result=ingest_result, max_consecutive_failures=2
    )

    result = await run_night()

    reader_calls = [call for call in fake.calls if call.role is AgentRole.READER]
    popularizer_calls = [call for call in fake.calls if call.role is AgentRole.POPULARIZER]
    assert len(reader_calls) == 4, "los cuatro ítems se leen: el cortacircuitos es de la fase B"
    assert len(popularizer_calls) == 2, (
        "item_c nunca llama al proveedor (descartado por umbral) e item_e nunca se "
        "ofrece: el cortacircuitos corta justo tras la segunda falla de verdad (item_d)"
    )
    assert result.status is RunStatus.PARTIAL
    assert (
        "circuit_breaker_popularizer" in result.notes
        or "end=circuit_breaker_popularizer" in result.notes
    )


# --- 9. RATE_LIMITED en fase B: aborta, no llama al Editor, PARTIAL -------


async def test_rate_limited_en_fase_b_aborta_y_no_llama_al_editor():
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.fail(
        AgentRole.POPULARIZER,
        error=LLMRateLimited("límite alcanzado", tokens_in=50, tokens_out=5),
    )
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    editor_calls = [call for call in fake.calls if call.role is AgentRole.EDITOR]
    assert editor_calls == [], "RATE_LIMITED en fase B fija self._skip_editor = True"
    assert result.status is RunStatus.PARTIAL
    assert "end=rate_limited_popularizer" in result.notes


# --- 10. BUDGET_EXHAUSTED en fase B: la reserva del Editor sigue intacta --


async def test_budget_exhausted_en_fase_b_no_toca_la_reserva_y_el_editor_se_llama_igual():
    """La reserva del Editor nunca la tocan Reader/Popularizer
    (`BudgetGuard._available_tokens`, regla 4 de `application/budget.py`):
    aunque la fase B se quede sin presupuesto para un segundo candidato, la
    fase C sigue teniendo su reserva completa y el Editor se llama y
    publica. El `status` final es `PARTIAL`, no `COMPLETED` -- ver la nota
    del docstring de este fichero, verificada contra el código: la
    degradación a `PARTIAL` ocurre en el momento de la denegación de fase B
    y `_degrade` es monótono, así que el éxito posterior del Editor no la
    revierte."""
    item_a, item_b = _make_item(), _make_item()
    # disponible para Reader/Popularizer = 2500 - 1000 = 1500. Dos Reader
    # (110 cada uno, con estimated_tokens=200 más abajo) dejan spent=220;
    # el primer Popularizer (estimated_tokens=1000, autorizado contra los
    # 1500 disponibles) deja spent=220+1000=1220 tras registrarse -- el
    # segundo Popularizer, con la misma estimación, no cabe ya (1220+1000 >
    # 1500): BUDGET_EXHAUSTED, sin tocar la reserva del Editor.
    policy = _policy(nightly_tokens=2_500, editor_reserve_tokens=1_000)
    env = _Environment(items=[item_a, item_b], policy=policy, now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=900, tokens_out=100
    )
    fake.respond(
        AgentRole.EDITOR,
        json={"publish": [{"item_id": str(item_a.id), "confidence": 0.9, "reason": "Motivo."}]},
        tokens_in=100,
        tokens_out=10,
    )
    ingest_result = IngestResult(
        fetched=2, new=2, duplicates=0, skipped=0, truncated=False, items=[item_a, item_b]
    )
    read_item = ReadItem(
        work=env.work,
        provider=fake,
        system_prompt="prompt del Reader",
        prompt_version="reader-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=200,
        max_attempts=1,
    )
    popularize = PopularizeReading(
        work=env.work,
        provider=fake,
        system_prompt="prompt del Popularizer",
        prompt_version="popularizer-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=1_000,
        max_attempts=1,
        min_interest_score=4,
    )
    edit_night = EditNight(
        work=env.work,
        provider=fake,
        clock=env.clock,
        system_prompt="prompt del Editor",
        prompt_version="editor-v1",
        model="claude-opus-test",
        max_turns=3,
        max_attempts=2,
        base_tokens=1_000,
        tokens_per_candidate=200,
    )

    async def _ingest() -> IngestResult:
        return ingest_result

    run_night = RunNight(
        work=env.work,
        clock=env.clock,
        ingest=_ingest,
        read_item=read_item,
        popularize=popularize,
        edit_night=edit_night,
        run_id=env.run.id,
        max_items=10,
        max_consecutive_failures=5,
        deadline_s=16_200,
    )

    result = await run_night()

    editor_calls = [call for call in fake.calls if call.role is AgentRole.EDITOR]
    assert len(editor_calls) == 1, "la reserva del Editor está intacta: se le sigue llamando"
    assert result.findings_published == 1
    assert result.status is RunStatus.PARTIAL, (
        "BUDGET_EXHAUSTED en fase B degrada a PARTIAL en el momento de la denegación; "
        "el éxito posterior del Editor no lo revierte (_degrade es monótono) -- ver la "
        "nota del docstring de este fichero"
    )
    assert "end=budget_exhausted" in result.notes


# --- 11. El Editor denegado por BUDGET_EXHAUSTED no publica nada ----------


async def test_editor_denegado_por_budget_exhausted_no_publica_nada():
    """Reader y Popularizer caben en el presupuesto disponible (spent=220
    tras ambos) y producen un candidato; el Editor, con
    `base_tokens=1_000, tokens_per_candidate=200` (estimación 1200 para 1
    candidato), no cabe en `nightly_tokens=1_000` -- que es el presupuesto
    COMPLETO que ve el Editor, sin resta de reserva (`_available_tokens`,
    regla 4 de `application/budget.py`): `BUDGET_EXHAUSTED` en la fase C, no
    en la A ni la B."""
    item = _make_item()
    policy = _policy(nightly_tokens=1_000, editor_reserve_tokens=100)
    env = _Environment(items=[item], policy=policy, now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(
        env=env,
        provider=fake,
        ingest_result=ingest_result,
    )

    result = await run_night()

    editor_calls = [call for call in fake.calls if call.role is AgentRole.EDITOR]
    assert editor_calls == [], "el Editor deniega antes de llamar al proveedor"
    assert result.candidates == 0, "BudgetDenied ocurre antes de que EditNight informe candidatos"
    assert result.findings_published == 0
    assert result.status is RunStatus.PARTIAL
    unpublished = env.findings.unpublished_for_run(env.run.id)
    assert len(unpublished) == 1 and unpublished[0].published_at is None, (
        "el candidato (el Finding que ya construyó el Popularizer) sigue ahí, sin "
        "publicar: el Editor nunca llegó a decidir nada sobre él"
    )


# --- 12. EDITOR_ALREADY_CALLED con candidatos: PARTIAL sin terminal_status_for


async def test_editor_already_called_con_candidatos_cierra_partial_sin_terminal_status_for():
    """Con `max_editor_calls_per_night=2` ya agotadas por dos `AgentCall`
    previas de esta misma noche, la fase C deniega con
    `EDITOR_ALREADY_CALLED`. `RunNight._phase_editor` trata este motivo
    aparte, sin pasar por `terminal_status_for` (que lanza `ValueError` a
    propósito para él, ver su docstring): si este test llamara a esa
    función con este motivo, la propia función reventaría -- este test pasa
    en verde precisamente porque `RunNight` no la invoca en esta rama."""
    item = _make_item()
    policy = _policy(max_editor_calls_per_night=2)
    env = _Environment(items=[item], policy=policy, now=_WITHIN_WINDOW)
    for _ in range(2):
        env.agent_calls.add(
            AgentCall(
                run_id=env.run.id,
                item_id=None,
                agent=AgentRole.EDITOR,
                model="claude-opus-test",
                tokens_in=10,
                tokens_out=5,
                duration_ms=1,
                status=AgentCallStatus.OK,
                prompt_version="editor-v1",
            )
        )
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    editor_calls = [call for call in fake.calls if call.role is AgentRole.EDITOR]
    assert editor_calls == [], "las dos llamadas ya agotadas: ninguna más al proveedor"
    assert result.status is RunStatus.PARTIAL
    assert "end=editor_already_called" in result.notes


# --- 13. Editor con JSON inválido agotados los intentos: PARTIAL, nada publicado


async def test_editor_con_json_invalido_agotados_los_intentos_no_publica_nada():
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(AgentRole.EDITOR, raw="esto no es json", tokens_in=100, tokens_out=10)
    fake.respond(AgentRole.EDITOR, raw="tampoco esto", tokens_in=100, tokens_out=10)
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    editor_calls = [call for call in fake.calls if call.role is AgentRole.EDITOR]
    assert len(editor_calls) == 2, "un único reintento del Editor, agotado en la segunda llamada"
    assert result.findings_published == 0
    assert result.status is RunStatus.PARTIAL
    assert "end=editor_invalid_output" in result.notes


# --- 14. Ingesta fallida: la noche sigue con los NEW existentes -----------


async def test_ingesta_fallida_continua_con_los_new_existentes_y_no_supera_partial():
    """`self._ingest()` lanza `RuntimeError` (arXiv caído, un tipo que este
    módulo ni siquiera importa -- ver "except Exception, no un tipo
    concreto" en su docstring): la noche degrada a `PARTIAL` con motivo
    `ingest_error`, pero sigue leyendo lo que ya hubiera en base
    (`ItemRepository.next_unread` no depende de que la ingesta de esta
    noche haya añadido nada)."""
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=10)

    async def _failing_ingest() -> IngestResult:
        raise RuntimeError("arXiv caído")

    read_item = ReadItem(
        work=env.work,
        provider=fake,
        system_prompt="prompt del Reader",
        prompt_version="reader-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=500,
        max_attempts=2,
    )
    popularize = PopularizeReading(
        work=env.work,
        provider=fake,
        system_prompt="prompt del Popularizer",
        prompt_version="popularizer-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=500,
        max_attempts=2,
        min_interest_score=4,
    )
    edit_night = EditNight(
        work=env.work,
        provider=fake,
        clock=env.clock,
        system_prompt="prompt del Editor",
        prompt_version="editor-v1",
        model="claude-opus-test",
        max_turns=3,
        max_attempts=2,
        base_tokens=1_000,
        tokens_per_candidate=200,
    )
    run_night = RunNight(
        work=env.work,
        clock=env.clock,
        ingest=_failing_ingest,
        read_item=read_item,
        popularize=popularize,
        edit_night=edit_night,
        run_id=env.run.id,
        max_items=10,
        max_consecutive_failures=5,
        deadline_s=16_200,
    )

    result = await run_night()

    assert result.items_fetched == 0, "la ingesta falló: nada nuevo llegó a Item.new"
    assert result.items_read == 1, "el ítem ya existente (NEW) se lee igual"
    assert result.status is RunStatus.PARTIAL
    assert "ingest=error:RuntimeError" in result.notes
    assert "end=ingest_error" in result.notes


async def test_el_log_de_ingesta_fallida_incluye_error_detail(caplog, monkeypatch):
    """`night.ingest` (nivel ERROR, vía `_logger.exception`) lleva el campo
    `error_detail` con `str(exc)` -- T60.b: hoy (con `error=type(exc).__name__`
    solamente) un 406 de arXiv solo deja "ArxivUnavailable" en el log
    estructurado, sin el mensaje legible con el código, los intentos y el
    motivo de corte que compone `infrastructure/arxiv/client.py::_get`; ese
    texto solo vivía en el traceback, no consultable como campo. Mismo
    parche de logger deshabilitado que
    `test_excepcion_inesperada_en_un_item_queda_aislada_y_la_noche_continua`."""
    monkeypatch.setattr(run_night_module._logger, "disabled", False)
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=100, tokens_out=10)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=100, tokens_out=10
    )
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=10)

    failure_message = "la API de arXiv respondió 406 (rechazo de la petición), tras 4 intentos"

    async def _failing_ingest() -> IngestResult:
        raise RuntimeError(failure_message)

    read_item = ReadItem(
        work=env.work,
        provider=fake,
        system_prompt="prompt del Reader",
        prompt_version="reader-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=500,
        max_attempts=2,
    )
    popularize = PopularizeReading(
        work=env.work,
        provider=fake,
        system_prompt="prompt del Popularizer",
        prompt_version="popularizer-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=500,
        max_attempts=2,
        min_interest_score=4,
    )
    edit_night = EditNight(
        work=env.work,
        provider=fake,
        clock=env.clock,
        system_prompt="prompt del Editor",
        prompt_version="editor-v1",
        model="claude-opus-test",
        max_turns=3,
        max_attempts=2,
        base_tokens=1_000,
        tokens_per_candidate=200,
    )
    run_night = RunNight(
        work=env.work,
        clock=env.clock,
        ingest=_failing_ingest,
        read_item=read_item,
        popularize=popularize,
        edit_night=edit_night,
        run_id=env.run.id,
        max_items=10,
        max_consecutive_failures=5,
        deadline_s=16_200,
    )

    with caplog.at_level("ERROR"):
        await run_night()

    ingest_records = [
        record for record in caplog.records if record.__dict__.get("event") == "night.ingest"
    ]
    assert len(ingest_records) == 1
    assert ingest_records[0].__dict__["error_detail"] == failure_message


# --- 15. NO_CANDIDATES tras descartar todo: COMPLETED, coste cero del Editor


async def test_no_candidates_tras_descartar_todo_cierra_completed_con_coste_cero():
    """A diferencia del humo `test_sin_candidatos_...` (sin ítems en
    absoluto), aquí hay un ítem real que el Reader lee, pero el Popularizer
    lo descarta por umbral: cero candidatos llegan a la fase C igual, y el
    Editor nunca llama al proveedor (`EditOutcome.NO_CANDIDATES`, coste
    cero, docstring de `edit_night.py`)."""
    item = _make_item()
    env = _Environment(items=[item], policy=_policy(), now=_WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.READER, json=_valid_reading_json(interest_score=1), tokens_in=100, tokens_out=10
    )
    ingest_result = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
    )
    run_night = _make_run_night(env=env, provider=fake, ingest_result=ingest_result)

    result = await run_night()

    editor_calls = [call for call in fake.calls if call.role is AgentRole.EDITOR]
    assert editor_calls == [], "sin candidatos, el Editor no llama al proveedor"
    assert result.status is RunStatus.COMPLETED
    assert result.findings_published == 0
    assert "end=no_candidates" in result.notes
