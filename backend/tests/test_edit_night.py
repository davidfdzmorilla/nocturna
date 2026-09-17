"""Tests de `application/use_cases/edit_night.py::EditNight` (T43).

Empezó como humo mínimo (camino feliz + `NO_CANDIDATES`); el `tester` amplía
aquí la batería completa: guarda de estado por candidato, `item_id`
desconocido/duplicado, `publish` vacío, fallos transitorios del proveedor,
`BudgetDenied` (presupuesto insuficiente y `EDITOR_ALREADY_CALLED`), la
estimación de coste real pasada a `BudgetGuard.authorize`, y la forma exacta
del `AgentRequest`/`AgentCall` construidos. Mismo patrón que
`test_popularize_reading.py` (T42, "tu plantilla"): sin base de datos,
`FakeLLMProvider` (`tests/fakes/llm.py`) y un `AgentWorkFactory` en memoria
(`tests/fakes/work.py`) -- ningún test llama a Claude
(`.claude/skills/testing-without-claude`). El equivalente contra PostgreSQL
real vive en `tests/db/test_edit_night_db.py`.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import replace
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
    counting_work_factory,
    make_work_factory,
)

from nocturna.application.budget import BudgetDenied, BudgetGuard, BudgetPolicy, DenyReason
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.edit_night import EditNight, EditOutcome, estimate_editor_tokens
from nocturna.domain.entities import (
    AgentCall,
    AgentCallStatus,
    Finding,
    FindingType,
    Item,
    ItemStatus,
    Run,
)
from nocturna.domain.errors import LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 100_000,
        "editor_reserve_tokens": 20_000,
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
    """Un `Item` ya leído y divulgado (`ItemStatus.READ`): el estado que le
    corresponde a un candidato del Editor."""
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


def _make_finding(*, item_id, run_id, **overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "item_id": item_id,
        "run_id": run_id,
        "type": FindingType.PAPER_EXPLAINED,
        "title": "Un titular de prueba",
        "level_curious": "Nivel curioso de prueba",
        "level_amateur": "Nivel aficionado de prueba",
        "level_technical": "Nivel técnico de prueba",
    }
    defaults.update(overrides)
    return Finding(**defaults)


def _make_environment(
    *,
    items: list[Item],
    findings: list[Finding],
    policy: BudgetPolicy | None = None,
    run: Run | None = None,
    now: datetime = _WITHIN_WINDOW,
) -> tuple[AgentWorkFactory, Run, InMemoryAgentCallRepository]:
    resolved_policy = policy or _policy()
    resolved_run = run or _make_run(budget_tokens=resolved_policy.nightly_tokens)
    runs = InMemoryRunRepository(resolved_run)
    items_repo = InMemoryItemRepository(*items)
    readings = InMemoryReadingRepository()
    findings_repo = InMemoryFindingRepository()
    for finding in findings:
        findings_repo.add(finding)
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
        items=items_repo,
        readings=readings,
        findings=findings_repo,
        agent_calls=agent_calls,
    )
    return work, resolved_run, agent_calls


def _make_edit_night(
    *,
    work: AgentWorkFactory,
    provider: FakeLLMProvider,
    clock: FakeClock,
    max_attempts: int = 1,
    system_prompt: str = "prompt de sistema del Editor",
    prompt_version: str = "editor-v1",
    model: str = "claude-opus-test",
) -> EditNight:
    return EditNight(
        work=work,
        provider=provider,
        clock=clock,
        system_prompt=system_prompt,
        prompt_version=prompt_version,
        model=model,
        max_turns=3,
        max_attempts=max_attempts,
        base_tokens=1_000,
        tokens_per_candidate=200,
    )


# --- 1. Camino feliz: publica los aprobados, descarta el resto ------------


async def test_camino_feliz_publica_los_aprobados_y_descarta_el_resto():
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    item_b = _make_item()
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    finding_b = _make_finding(item_id=item_b.id, run_id=run.id)
    work, run, _ = _make_environment(
        items=[item_a, item_b], findings=[finding_a, finding_b], run=run
    )
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.EDITOR,
        json={
            "publish": [
                {"item_id": str(item_a.id), "confidence": 0.9, "reason": "Motivo de prueba."}
            ]
        },
        tokens_in=2000,
        tokens_out=300,
    )
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock)

    result = await edit_night(run_id=run.id)

    assert result.outcome is EditOutcome.EDITED
    assert result.candidates == 2
    assert [f.item_id for f in result.published] == [item_a.id]
    assert [f.item_id for f in result.discarded] == [item_b.id]
    assert result.reasons == {item_a.id: "Motivo de prueba."}
    assert result.unknown_item_ids == ()
    assert result.tokens_spent == 2300
    assert item_a.status is ItemStatus.PUBLISHED
    assert item_b.status is ItemStatus.DISCARDED
    assert finding_a.is_published
    assert finding_a.confidence == 0.9
    assert not finding_b.is_published
    assert len(fake.calls) == 1, "el Editor se llama una única vez por noche"


# --- 2. Sin candidatos: NO_CANDIDATES, cero gasto, cero llamadas ----------


async def test_sin_candidatos_no_llama_al_proveedor():
    run = _make_run(budget_tokens=100_000)
    work, run, _ = _make_environment(items=[], findings=[], run=run)
    fake = FakeLLMProvider()
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock)

    result = await edit_night(run_id=run.id)

    assert result.outcome is EditOutcome.NO_CANDIDATES
    assert result.candidates == 0
    assert result.published == []
    assert result.discarded == []
    assert result.reasons == {}
    assert result.tokens_spent == 0
    assert result.attempts == 0
    assert fake.calls == [], "sin candidatos no se autoriza ni se llama a nada"


# --- 2b. Sin candidatos: ninguna unidad de trabajo de gasto se abre --------


async def test_sin_candidatos_no_abre_ninguna_unidad_de_trabajo_de_gasto():
    """La única unidad de trabajo que `EditNight.__call__` abre en este
    camino es la lectura de candidatos (sin LLM, docstring del módulo,
    "Las dos unidades de trabajo del camino con llamada"): `NO_CANDIDATES`
    se decide antes de construir ningún `AgentRunner`, así que ninguna
    unidad de trabajo de `authorize`/`record_call` (gasto) llega a abrirse."""
    run = _make_run(budget_tokens=100_000)
    work, run, _ = _make_environment(items=[], findings=[], run=run)
    counting_work, work_calls = counting_work_factory(work)
    fake = FakeLLMProvider()
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=counting_work, provider=fake, clock=clock)

    result = await edit_night(run_id=run.id)

    assert result.outcome is EditOutcome.NO_CANDIDATES
    assert result.tokens_spent == 0
    assert fake.calls == []
    assert work_calls == [1], (
        "una única apertura: la lectura de candidatos. Ninguna unidad de trabajo de "
        "gasto (authorize/record_call) se abre sin candidatos que ofrecer al Editor"
    )


# --- 3. publish: [] es una respuesta válida: descarta todo, sin fallo -----


async def test_publish_vacio_no_publica_nada_pero_no_es_un_fallo():
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    item_b = _make_item()
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    finding_b = _make_finding(item_id=item_b.id, run_id=run.id)
    work, run, _ = _make_environment(
        items=[item_a, item_b], findings=[finding_a, finding_b], run=run
    )
    fake = FakeLLMProvider()
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=1500, tokens_out=200)
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock)

    result = await edit_night(run_id=run.id)

    assert result.outcome is EditOutcome.EDITED
    assert result.published == []
    assert {f.item_id for f in result.discarded} == {item_a.id, item_b.id}
    assert item_a.status is ItemStatus.DISCARDED
    assert item_b.status is ItemStatus.DISCARDED
    assert not finding_a.is_published
    assert not finding_b.is_published


# --- 4. item_id desconocido: se ignora, cuenta, una sola llamada ----------


async def test_item_id_desconocido_se_ignora_una_sola_llamada_y_el_resto_se_aplica():
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    item_b = _make_item()
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    finding_b = _make_finding(item_id=item_b.id, run_id=run.id)
    work, run, _ = _make_environment(
        items=[item_a, item_b], findings=[finding_a, finding_b], run=run
    )
    unknown_id = uuid4()
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.EDITOR,
        json={
            "publish": [
                {"item_id": str(item_a.id), "confidence": 0.7, "reason": "Motivo válido."},
                {"item_id": str(unknown_id), "confidence": 0.5, "reason": "Alucinación."},
            ]
        },
        tokens_in=1000,
        tokens_out=200,
    )
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock)

    result = await edit_night(run_id=run.id)

    assert result.outcome is EditOutcome.EDITED
    assert result.unknown_item_ids == (str(unknown_id),)
    assert [f.item_id for f in result.published] == [item_a.id]
    assert [f.item_id for f in result.discarded] == [item_b.id]
    assert len(fake.calls) == 1, "un item_id desconocido no dispara ningún reintento"


# --- 5. item_id duplicado: se publica una vez, sin InvalidTransition ------


async def test_item_id_duplicado_se_publica_una_vez_sin_invalid_transition():
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    work, run, _ = _make_environment(items=[item_a], findings=[finding_a], run=run)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.EDITOR,
        json={
            "publish": [
                {"item_id": str(item_a.id), "confidence": 0.6, "reason": "Primera aparición."},
                {"item_id": str(item_a.id), "confidence": 0.9, "reason": "Segunda aparición."},
            ]
        },
        tokens_in=1000,
        tokens_out=200,
    )
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock)

    # No debe lanzar InvalidTransition (un segundo Finding.publish() sobre el
    # mismo Finding lo haría, ver docstring del módulo, "Validación de la
    # respuesta del Editor").
    result = await edit_night(run_id=run.id)

    assert result.outcome is EditOutcome.EDITED
    assert [f.item_id for f in result.published] == [item_a.id]
    assert result.reasons[item_a.id] == "Primera aparición."
    assert finding_a.confidence == 0.6, "gana la primera aparición, no la última"


# --- 6. Salida inválida agotados los intentos: cero publicados, cero transicionados


@pytest.mark.parametrize(
    "make_invalid_payload",
    [
        lambda: {"raw": "esto no es json en absoluto"},
        lambda: {
            "json": {"publish": [{"item_id": str(uuid4()), "confidence": 1.7, "reason": "Motivo."}]}
        },
        lambda: {
            "json": {"publish": [{"item_id": str(uuid4()), "confidence": 0.5, "reason": "   "}]}
        },
    ],
    ids=["json_no_parseable", "confidence_fuera_de_rango", "reason_en_blanco"],
)
async def test_salida_invalida_agotados_los_intentos_no_publica_ni_transiciona_nada(
    make_invalid_payload,
):
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    work, run, _ = _make_environment(items=[item_a], findings=[finding_a], run=run)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.EDITOR, tokens_in=500, tokens_out=50, **make_invalid_payload())
    fake.respond(AgentRole.EDITOR, tokens_in=500, tokens_out=50, **make_invalid_payload())
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock, max_attempts=2)

    result = await edit_night(run_id=run.id)

    assert result.outcome is EditOutcome.INVALID_OUTPUT
    assert len(fake.calls) == 2, "un único reintento, agotado en la segunda llamada"
    assert result.published == []
    assert result.discarded == []
    assert item_a.status is ItemStatus.READ, "sin salida utilizable, el Item no se toca"
    assert not finding_a.is_published


# --- 7. Presupuesto insuficiente: la excepción sale de EditNight sin gasto -


async def test_presupuesto_insuficiente_deniega_y_no_publica_nada():
    run = _make_run(budget_tokens=100)  # muy por debajo de la estimación del único candidato
    item_a = _make_item()
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    work, run, agent_calls = _make_environment(items=[item_a], findings=[finding_a], run=run)
    fake = FakeLLMProvider()
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock)

    with pytest.raises(BudgetDenied) as excinfo:
        await edit_night(run_id=run.id)

    assert excinfo.value.reason is DenyReason.BUDGET_EXHAUSTED
    assert excinfo.value.role is AgentRole.EDITOR
    assert fake.calls == [], "la denegación va antes de cualquier llamada al proveedor"
    assert agent_calls.calls == []
    assert finding_a.published_at is None
    assert item_a.status is ItemStatus.READ


# --- 8. EDITOR_ALREADY_CALLED: dos AgentCall previas, denegación sin gasto -


async def test_editor_ya_llamado_deniega_sin_llamar_al_proveedor_ni_publicar():
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    policy = _policy(max_editor_calls_per_night=2)
    work, run, agent_calls = _make_environment(
        items=[item_a], findings=[finding_a], run=run, policy=policy
    )
    for _ in range(2):
        agent_calls.add(
            AgentCall(
                run_id=run.id,
                item_id=None,
                agent=AgentRole.EDITOR,
                model="claude-opus-test",
                tokens_in=100,
                tokens_out=50,
                duration_ms=10,
                status=AgentCallStatus.OK,
                prompt_version="editor-v1",
            )
        )
    fake = FakeLLMProvider()
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock)

    with pytest.raises(BudgetDenied) as excinfo:
        await edit_night(run_id=run.id)

    assert excinfo.value.reason is DenyReason.EDITOR_ALREADY_CALLED
    assert fake.calls == []
    assert finding_a.published_at is None
    assert item_a.status is ItemStatus.READ


# --- 9. Candidato con Item no READ: se excluye antes de la llamada --------


async def test_candidato_con_item_no_read_se_excluye_antes_de_la_llamada():
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    item_b = _make_item(status=ItemStatus.NEW)
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id, title="Candidato válido")
    finding_b = _make_finding(
        item_id=item_b.id, run_id=run.id, title="Candidato excluido, nunca en el prompt"
    )
    work, run, _ = _make_environment(
        items=[item_a, item_b], findings=[finding_a, finding_b], run=run
    )
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.EDITOR,
        json={"publish": [{"item_id": str(item_a.id), "confidence": 0.8, "reason": "Motivo."}]},
        tokens_in=1000,
        tokens_out=200,
    )
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock)

    result = await edit_night(run_id=run.id)

    assert result.outcome is EditOutcome.EDITED
    assert result.candidates == 1
    assert [f.item_id for f in result.published] == [item_a.id]
    assert result.discarded == [], "item_b nunca fue candidato: no aparece descartado"
    assert item_b.status is ItemStatus.NEW, "un Item que no estaba READ no se toca"
    assert not finding_b.is_published

    assert len(fake.calls) == 1
    prompt = fake.calls[0].prompt
    assert str(item_b.id) not in prompt
    assert "Candidato excluido, nunca en el prompt" not in prompt


# --- 10. estimate_editor_tokens: fórmula pura ------------------------------


def test_estimate_editor_tokens_formula_pura():
    assert (
        estimate_editor_tokens(candidates=3, base_tokens=1_000, tokens_per_candidate=200) == 1_600
    )
    assert estimate_editor_tokens(candidates=0, base_tokens=500, tokens_per_candidate=100) == 500


# --- 10b. La estimación real (con la n de candidatos filtrados) llega a authorize


class _AuthorizeSpy:
    """Envoltorio de `BudgetGuard` que registra los `estimated_tokens` pasados a `authorize`.

    Delega cualquier otro atributo/método al guard real vía `__getattr__`
    (`timeout_for_call`, `record_call`, ...): solo `authorize` se intercepta.
    """

    def __init__(self, guard: BudgetGuard, calls: list[int]) -> None:
        self._guard = guard
        self._calls = calls

    def __getattr__(self, name: str):
        return getattr(self._guard, name)

    def authorize(self, role: AgentRole, estimated_tokens: int) -> None:
        self._calls.append(estimated_tokens)
        self._guard.authorize(role, estimated_tokens)


async def test_estimacion_real_se_pasa_a_budget_guard_authorize_con_la_n_de_candidatos_leidos():
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    item_b = _make_item()
    item_c = _make_item(status=ItemStatus.NEW)  # excluido: no está READ
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    finding_b = _make_finding(item_id=item_b.id, run_id=run.id)
    finding_c = _make_finding(item_id=item_c.id, run_id=run.id)
    work, run, _ = _make_environment(
        items=[item_a, item_b, item_c],
        findings=[finding_a, finding_b, finding_c],
        run=run,
    )
    authorize_calls: list[int] = []

    @contextmanager
    def spying_work():
        with work() as w:
            yield replace(w, guard=_AuthorizeSpy(w.guard, authorize_calls))

    fake = FakeLLMProvider()
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=10, tokens_out=5)
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=spying_work, provider=fake, clock=clock)

    result = await edit_night(run_id=run.id)

    assert result.candidates == 2, "item_c queda excluido por no estar READ"
    expected = estimate_editor_tokens(candidates=2, base_tokens=1_000, tokens_per_candidate=200)
    assert authorize_calls == [expected], (
        "authorize debe recibir base_tokens + n * tokens_per_candidate con la n REAL de "
        "candidatos ya filtrados, no el número de Finding sin publicar sin filtrar"
    )


# --- 11. Contrato del AgentRequest/AgentCall -------------------------------


async def test_agent_request_contrato_role_model_item_id_system_prompt_y_sin_niveles_completos():
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    finding_a = _make_finding(
        item_id=item_a.id,
        run_id=run.id,
        title="Titular único ZQX99 para localizar en el prompt",
        level_curious="Nivel curioso único ZQX99",
        level_amateur="NIVEL AMATEUR SECRETO QUE NO DEBE VIAJAR EN EL PROMPT",
        level_technical="NIVEL TECNICO SECRETO QUE NO DEBE VIAJAR EN EL PROMPT",
    )
    work, run, agent_calls = _make_environment(items=[item_a], findings=[finding_a], run=run)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=20)
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(
        work=work,
        provider=fake,
        clock=clock,
        system_prompt="SYSTEM PROMPT EXCLUSIVO DEL EDITOR",
        model="claude-opus-test-contrato",
    )

    await edit_night(run_id=run.id)

    assert len(fake.calls) == 1
    request = fake.calls[0]
    assert request.role is AgentRole.EDITOR
    assert request.model == "claude-opus-test-contrato"
    assert request.item_id is None
    assert request.system_prompt == "SYSTEM PROMPT EXCLUSIVO DEL EDITOR"
    assert "SYSTEM PROMPT EXCLUSIVO DEL EDITOR" not in request.prompt, (
        "el system_prompt viaja aparte, no dentro del prompt de usuario"
    )
    assert request.prompt.startswith("<candidates>")
    assert request.prompt.endswith("</candidates>")
    assert str(item_a.id) in request.prompt
    assert "Titular único ZQX99 para localizar en el prompt" in request.prompt
    assert "Nivel curioso único ZQX99" in request.prompt
    assert "NIVEL AMATEUR SECRETO QUE NO DEBE VIAJAR EN EL PROMPT" not in request.prompt
    assert "NIVEL TECNICO SECRETO QUE NO DEBE VIAJAR EN EL PROMPT" not in request.prompt

    assert len(agent_calls.calls) == 1
    call = agent_calls.calls[0]
    assert call.item_id is None
    assert call.agent is AgentRole.EDITOR
    assert call.prompt_version == "editor-v1"


# --- 12. Fallos transitorios: outcome correspondiente, AgentCall con los tokens del fallo


@pytest.mark.parametrize(
    "error,expected_outcome,expected_call_status",
    [
        (
            LLMTimeout("no respondió a tiempo", tokens_in=800, tokens_out=50),
            EditOutcome.TIMEOUT,
            AgentCallStatus.TIMEOUT,
        ),
        (
            LLMRateLimited("límite alcanzado", tokens_in=800, tokens_out=50),
            EditOutcome.RATE_LIMITED,
            AgentCallStatus.ERROR,
        ),
        (
            LLMError("fallo genérico del agente", tokens_in=800, tokens_out=50),
            EditOutcome.AGENT_ERROR,
            AgentCallStatus.ERROR,
        ),
    ],
    ids=["timeout", "rate_limited", "agent_error"],
)
async def test_fallo_transitorio_del_proveedor_no_reintenta_ni_publica_nada(
    error, expected_outcome, expected_call_status
):
    run = _make_run(budget_tokens=100_000)
    item_a = _make_item()
    finding_a = _make_finding(item_id=item_a.id, run_id=run.id)
    work, run, agent_calls = _make_environment(items=[item_a], findings=[finding_a], run=run)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.EDITOR, error=error)
    clock = FakeClock(_WITHIN_WINDOW)
    edit_night = _make_edit_night(work=work, provider=fake, clock=clock, max_attempts=3)

    result = await edit_night(run_id=run.id)

    assert result.outcome is expected_outcome
    assert len(fake.calls) == 1, "un fallo transitorio no se reintenta"
    assert result.published == []
    assert result.discarded == []
    assert item_a.status is ItemStatus.READ
    assert not finding_a.is_published

    assert len(agent_calls.calls) == 1
    call = agent_calls.calls[0]
    assert call.status is expected_call_status
    assert call.tokens_in == 800
    assert call.tokens_out == 50
    assert call.agent is AgentRole.EDITOR
