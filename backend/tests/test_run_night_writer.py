"""T76: la fase del redactor de `RunNight` (en memoria, sin Claude)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fakes.llm import FakeLLMProvider
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    approve_items_in_editor,
    in_memory_finding_ids_by_item,
    make_item,
    make_policy,
    make_run_night,
    make_selector,
    valid_reading_json,
)
from helpers.tension_writer import v1298_evaluation

from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.run_night import RunNight
from nocturna.domain.entities import (
    AgentCall,
    AgentCallStatus,
    Finding,
    FindingType,
    Item,
    ItemStatus,
    RunStatus,
)
from nocturna.domain.errors import LLMRateLimited
from nocturna.domain.llm import AgentRequest, AgentResult, AgentRole

pytestmark = pytest.mark.anyio

WRITER_JSON = {
    "title": "V1298 Tau b: una discrepancia por revisar",
    "level_curious": "Dos medidas de la masa no coinciden.",
    "level_amateur": "La masa difiere en unos 3 sigma.",
    "level_technical": "sigma = 3.4 frente a Livingston et al. 2026; no verificado.",
}


def _ingest(items=()) -> IngestResult:
    return IngestResult(
        fetched=len(items),
        new=len(items),
        duplicates=0,
        skipped=0,
        truncated=False,
        items=list(items),
    )


def _policy(**overrides: object):
    defaults: dict[str, object] = {
        "writer_reserve_tokens": 20_000,
        "max_writer_calls_per_night": 5,
    }
    defaults.update(overrides)
    return make_policy(**defaults)


def _env(*, tensions: int = 1, with_paper: bool = True, now: datetime = WITHIN_WINDOW, **policy):
    """Entorno con `tensions` evaluaciones y, si `with_paper`, un `paper_explained`
    pendiente (para que el Editor tenga siempre algo que decidir)."""
    items: list[Item] = [make_item(status=ItemStatus.READ) for _ in range(tensions + 1)]
    env = Environment(items=items, policy=_policy(**policy), now=now)
    for item in items[:tensions]:
        env.evaluations.add(v1298_evaluation(item.id))
    paper_item = items[-1]
    if with_paper:
        env.findings.add(
            Finding(
                item_id=paper_item.id,
                run_id=env.run.id,
                type=FindingType.PAPER_EXPLAINED,
                title="Un paper",
                level_curious="c",
                level_amateur="a",
                level_technical="t",
            )
        )
    return env, items


def _writer_calls(fake: FakeLLMProvider) -> list:
    return [c for c in fake.calls if c.role is AgentRole.WRITER]


def _editor_approves_all(fake: FakeLLMProvider, env: Environment) -> None:
    approve_items_in_editor(
        fake,
        finding_ids_by_item=in_memory_finding_ids_by_item(env),
        item_ids=[i.id for i in env.items._items.values()],
    )


def _night(env: Environment, fake: FakeLLMProvider, **kwargs):
    return make_run_night(env=env, provider=fake, ingest_result=_ingest(), **kwargs)


async def test_sin_tension_no_hay_llamadas_del_redactor() -> None:
    env, _ = _env(tensions=0)
    fake = FakeLLMProvider()
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert _writer_calls(fake) == []
    assert not [c for c in env.agent_calls.calls if c.agent is AgentRole.WRITER]
    assert result.status is RunStatus.COMPLETED


async def test_una_tension_una_llamada_y_el_editor_recibe_los_dos_tipos_en_una_sola_llamada() -> (
    None
):
    env, _ = _env(tensions=1)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert [c.role for c in fake.calls] == [AgentRole.WRITER, AgentRole.EDITOR]
    assert result.status is RunStatus.COMPLETED
    editor_prompt = fake.calls[1].prompt
    assert "type: paper_explained" in editor_prompt
    assert "type: catalog_tension" in editor_prompt
    assert "evidence" not in editor_prompt
    assert result.candidates == 2 and result.findings_published == 2
    assert len([c for c in env.agent_calls.calls if c.agent is AgentRole.EDITOR]) == 1
    (writer_call,) = [c for c in env.agent_calls.calls if c.agent is AgentRole.WRITER]
    assert writer_call.prompt_version == "writer-v1"


async def test_json_invalido_reintenta_queda_fallida_y_la_noche_llega_al_editor() -> None:
    env, _ = _env(tensions=1)
    fake = FakeLLMProvider()
    for _ in range(2):
        fake.respond(AgentRole.WRITER, raw="no es json", tokens_in=100, tokens_out=10)
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert len(_writer_calls(fake)) == 2
    assert [c.status for c in env.agent_calls.calls if c.agent is AgentRole.WRITER] == [
        AgentCallStatus.INVALID_OUTPUT
    ] * 2
    assert AgentRole.EDITOR in [c.role for c in fake.calls]
    assert result.status is RunStatus.COMPLETED
    # La noche siguiente no vuelve a redactarla.
    pending = make_selector(env).pending()
    assert pending.candidates == ()
    assert [reason for _, reason in pending.skipped] == ["writer_failed"]


async def test_presupuesto_del_redactor_agotado_degrada_a_partial_y_el_editor_sigue() -> None:
    env, items = _env(tensions=1)
    env.agent_calls.add(
        AgentCall(
            run_id=env.run.id,
            item_id=None,
            agent=AgentRole.READER,
            model="m",
            tokens_in=60_000,
            tokens_out=30_000,
            duration_ms=1,
            status=AgentCallStatus.OK,
        )
    )
    fake = FakeLLMProvider()
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert _writer_calls(fake) == []
    assert [c.role for c in fake.calls] == [AgentRole.EDITOR]
    assert result.status is RunStatus.PARTIAL
    assert "end=writer_budget_exhausted" in result.notes


@pytest.mark.parametrize("cap", [0, 1])
async def test_tope_de_llamadas_del_redactor_no_degrada_y_el_editor_sigue(cap: int) -> None:
    env, _ = _env(tensions=2, max_writer_calls_per_night=cap)
    fake = FakeLLMProvider()
    for _ in range(cap):
        fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert len(_writer_calls(fake)) == cap
    assert AgentRole.EDITOR in [c.role for c in fake.calls]
    assert result.status is RunStatus.COMPLETED


async def test_fuera_de_ventana_en_el_redactor_cierra_killed_sin_editor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    late = datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)
    env, _ = _env(tensions=1, now=late)
    fake = FakeLLMProvider()
    _editor_approves_all(fake, env)
    editor_phase_entered: list[bool] = []

    async def _spy(self) -> None:
        editor_phase_entered.append(True)

    monkeypatch.setattr(RunNight, "_phase_editor", _spy)

    result = await _night(env, fake)()

    assert fake.calls == []
    assert editor_phase_entered == [], (
        "OUTSIDE_WINDOW en el redactor debe saltar la fase del Editor"
    )
    assert result.status is RunStatus.KILLED


async def test_rate_limited_del_redactor_degrada_y_salta_el_editor() -> None:
    env, _ = _env(tensions=1)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.WRITER, error=LLMRateLimited("límite alcanzado"))
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert [c.role for c in fake.calls] == [AgentRole.WRITER]
    assert result.status is RunStatus.PARTIAL


async def test_con_el_editor_ya_saltado_el_redactor_no_se_llama() -> None:
    new_item = make_item()
    env, _ = _env(tensions=1)
    env.items.add_many([new_item])
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=LLMRateLimited("límite alcanzado"))
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    result = await make_run_night(env=env, provider=fake, ingest_result=_ingest([new_item]))()

    assert _writer_calls(fake) == []
    assert [c.role for c in fake.calls] == [AgentRole.READER]
    assert result.status is RunStatus.PARTIAL


async def test_el_orden_es_reader_popularizer_redactor_editor() -> None:
    new_item = make_item()
    env, _ = _env(tensions=1, with_paper=False)
    env.items.add_many([new_item])
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=valid_reading_json(), tokens_in=1000, tokens_out=200)
    fake.respond(
        AgentRole.POPULARIZER,
        json={
            "title": "t",
            "level_curious": "c",
            "level_amateur": "a",
            "level_technical": "t",
        },
        tokens_in=800,
        tokens_out=150,
    )
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    await make_run_night(env=env, provider=fake, ingest_result=_ingest([new_item]))()

    assert [c.role for c in fake.calls] == [
        AgentRole.READER,
        AgentRole.POPULARIZER,
        AgentRole.WRITER,
        AgentRole.EDITOR,
    ]


class _SlowWriterProvider:
    """Se queda dormido en la llamada del redactor y, al cancelarla el vigía,
    adjunta el gasto ya visto a la `CancelledError` (patrón de
    `AgentSDKProvider`, T41)."""

    def __init__(self, *, tokens_in: int, tokens_out: int) -> None:
        self.calls: list[AgentRequest] = []
        self._usage = (tokens_in, tokens_out)

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            exc.tokens_in, exc.tokens_out = self._usage  # type: ignore[attr-defined]
            raise
        raise AssertionError("el vigía debe cancelar la llamada antes")


async def test_hard_stop_en_la_llamada_del_redactor_cierra_killed_con_gasto_registrado() -> None:
    env, _ = _env(tensions=1)
    provider = _SlowWriterProvider(tokens_in=400, tokens_out=25)

    result = await make_run_night(
        env=env, provider=provider, ingest_result=_ingest(), deadline_s=0.05
    )()

    assert result.status is RunStatus.KILLED
    assert "end=hard_stop" in result.notes
    assert [c.role for c in provider.calls] == [AgentRole.WRITER]
    (call,) = env.agent_calls.calls
    assert call.agent is AgentRole.WRITER
    assert (call.tokens_in, call.tokens_out) == (400, 25)
    assert call.status is AgentCallStatus.ERROR
    assert result.tokens_used == 425
    assert [f for f in env.findings.unpublished_for_run(env.run.id) if f.catalog_tension] == []


async def test_fallo_inesperado_tras_la_llamada_termina_la_fase_y_el_editor_sigue(
    monkeypatch,
) -> None:
    """Una llamada ya cobrada y `ok` cuyo guardado revienta (p. ej. base sin
    migrar) no debe dejar que el siguiente candidato vuelva a gastar."""
    env, _ = _env(tensions=2)
    real_add = env.findings.add

    def _failing_add(finding: Finding) -> None:
        if finding.type is FindingType.CATALOG_TENSION:
            raise RuntimeError("IntegrityError simulado")
        real_add(finding)

    monkeypatch.setattr(env.findings, "add", _failing_add)
    fake = FakeLLMProvider()
    for _ in range(2):
        fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert len(_writer_calls(fake)) == 1
    assert AgentRole.EDITOR in [c.role for c in fake.calls]
    assert result.status is RunStatus.PARTIAL
    assert "end=writer_error" in result.notes


async def test_pending_que_lanza_degrada_a_partial_sin_llamadas_del_redactor() -> None:
    env, _ = _env(tensions=1)

    class _BrokenSelector:
        def pending(self):
            raise RuntimeError("base sin migrar")

    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    result = await _night(env, fake, select_tensions=_BrokenSelector())()

    assert _writer_calls(fake) == []
    assert AgentRole.EDITOR in [c.role for c in fake.calls]
    assert result.status is RunStatus.PARTIAL
    assert "end=writer_error" in result.notes


async def test_run_not_running_en_el_redactor_pasa_por_terminal_status_for() -> None:
    """Con el Run ya cerrado, el guard deniega con `RUN_NOT_RUNNING`: la fase
    degrada a PARTIAL (`terminal_status_for`), no llama al proveedor y la noche
    sigue hacia el Editor, que el guard también deniega (sin llamada)."""
    env, _ = _env(tensions=1)
    env.run.finish(RunStatus.COMPLETED, at=env.run.started_at)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert fake.calls == []
    assert result.status is RunStatus.PARTIAL
    assert "end=run_not_running" in result.notes
