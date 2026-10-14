"""T85.e: desenlaces de `_phase_writer` en la pasada del archivo (en memoria, sin Claude)."""

from __future__ import annotations

import asyncio
import logging
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
    make_solution_selector,
    make_solution_writer,
)
from helpers.solution_tension_writer import make_archive_item, make_solution_tension
from helpers.tension_writer import v1298_evaluation

from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.run_night import RunNight
from nocturna.application.use_cases.run_night import _logger as run_night_logger
from nocturna.application.use_cases.write_solution_tensions import SelectSolutionTensions
from nocturna.domain.entities import (
    AgentCall,
    AgentCallStatus,
    Finding,
    FindingType,
    ItemStatus,
    RunStatus,
)
from nocturna.domain.llm import AgentRequest, AgentResult, AgentRole

pytestmark = pytest.mark.anyio

ARCHIVE_JSON = {
    "title": "HD 114082 b: diferencia calculada entre dos soluciones del archivo",
    "level_curious": "c",
    "level_amateur": "a",
    "level_technical": "t",
}
ARXIV_JSON = {**ARCHIVE_JSON, "title": "V1298 Tau b: una discrepancia por revisar"}
LATE = datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)


def _ingest() -> IngestResult:
    return IngestResult(fetched=0, new=0, duplicates=0, skipped=0, truncated=False, items=[])


def _env(*, arxiv: int = 0, archive: int = 1, paper: bool = False, now=WITHIN_WINDOW, **policy):
    defaults = {"writer_reserve_tokens": 40_000, "max_writer_calls_per_night": 5}
    defaults.update(policy)
    arxiv_items = [make_item(status=ItemStatus.READ) for _ in range(arxiv)]
    keys = [chr(ord("c") + i) * 64 for i in range(archive)] or ["c" * 64]
    archive_items = [make_archive_item(external_id=k) for k in keys]
    env = Environment(items=[*arxiv_items, *archive_items], policy=make_policy(**defaults), now=now)
    for item in arxiv_items:
        env.evaluations.add(v1298_evaluation(item.id))
    for i in range(archive):
        env.solution_tensions.add(
            make_solution_tension(archive_items[i].id, new_key=keys[i], sigma=100.0 - i * 10)
        )
    if paper:
        paper_item = make_item(status=ItemStatus.READ)
        env.items.add_many([paper_item])
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
    return env


def _night(env, provider, **kw) -> RunNight:
    return make_run_night(
        env=env,
        provider=provider,
        ingest_result=_ingest(),
        select_solution_tensions=make_solution_selector(env),
        write_solution_tensions=make_solution_writer(env, provider),
        **kw,
    )


def _writes(fake):
    return [c for c in fake.calls if c.role is AgentRole.WRITER]


def _roles(fake):
    return [c.role for c in fake.calls]


def _approve(fake, env) -> None:
    approve_items_in_editor(
        fake,
        finding_ids_by_item=in_memory_finding_ids_by_item(env),
        item_ids=[i.id for i in env.items._items.values()],
    )


def _ok(fake, n: int = 1, *, data=ARCHIVE_JSON, tokens=(700, 300)) -> None:
    for _ in range(n):
        fake.respond(AgentRole.WRITER, json=data, tokens_in=tokens[0], tokens_out=tokens[1])


def _spy_authorize(env) -> list[AgentRole]:
    seen: list[AgentRole] = []
    real = env.guard.authorize

    def _spy(*args, **kwargs):
        seen.append(args[0] if args else kwargs.get("role"))
        return real(*args, **kwargs)

    env.guard.authorize = _spy  # type: ignore[method-assign]
    return seen


class _ClockJumpProvider(FakeLLMProvider):
    """Tras la primera llamada del redactor, la hora pasa de `hard_stop`."""

    def __init__(self, env) -> None:
        super().__init__()
        self._env = env

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        result = await super().run_agent(request)
        if request.role is AgentRole.WRITER:
            self._env.clock.set(LATE)
        return result


class _SlowProvider:
    def __init__(self, *, usage=(400, 25)) -> None:
        self.calls: list[AgentRequest] = []
        self.started = asyncio.Event()
        self._usage = usage

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        self.started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            exc.tokens_in, exc.tokens_out = self._usage  # type: ignore[attr-defined]
            raise
        raise AssertionError("debe cancelarse antes")


# --- desenlaces del guard en la pasada del archivo --------------------------


async def test_archivo_budget_exhausted_es_partial_writer_budget_exhausted_y_el_editor_sigue() -> (
    None
):
    env = _env(arxiv=0, archive=1, paper=True)
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
    _approve(fake, env)

    result = await _night(env, fake)()

    assert _writes(fake) == [] and AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.PARTIAL and "end=writer_budget_exhausted" in result.notes


async def test_archivo_budget_exhausted_tras_una_llamada_de_arxiv_corta_el_resto() -> None:
    env = _env(arxiv=1, archive=2, paper=True)
    fake = FakeLLMProvider()
    _ok(fake, data=ARXIV_JSON, tokens=(60_000, 29_800))
    _approve(fake, env)

    result = await _night(env, fake)()

    assert len(_writes(fake)) == 1 and "<tension>" in _writes(fake)[0].prompt
    assert AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.PARTIAL and "end=writer_budget_exhausted" in result.notes


async def test_archivo_outside_window_tras_arxiv_es_killed_sin_editor(monkeypatch) -> None:
    env = _env(arxiv=1, archive=1, paper=True)
    fake = _ClockJumpProvider(env)
    _ok(fake, data=ARXIV_JSON)
    _approve(fake, env)
    entered: list[bool] = []

    async def _spy(self) -> None:
        entered.append(True)

    monkeypatch.setattr(RunNight, "_phase_editor", _spy)

    result = await _night(env, fake)()

    assert len(_writes(fake)) == 1 and "<tension>" in _writes(fake)[0].prompt
    assert entered == [] and AgentRole.EDITOR not in _roles(fake)
    assert result.status is RunStatus.KILLED and "outside_window" in result.notes


async def test_archivo_outside_window_solo_archivo_es_killed_sin_llamadas() -> None:
    env = _env(arxiv=0, archive=1, paper=True, now=LATE)
    fake = FakeLLMProvider()
    _approve(fake, env)

    result = await _night(env, fake)()

    assert fake.calls == [] and result.status is RunStatus.KILLED


async def test_archivo_rate_limited_es_partial_y_salta_el_editor() -> None:
    from nocturna.domain.errors import LLMRateLimited

    env = _env(arxiv=0, archive=2, paper=True)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.WRITER, error=LLMRateLimited("límite"))
    _approve(fake, env)

    result = await _night(env, fake)()

    assert _roles(fake) == [AgentRole.WRITER]
    assert result.status is RunStatus.PARTIAL and "end=rate_limited_writer" in result.notes


async def test_archivo_run_not_running_pasa_por_terminal_status_for() -> None:
    env = _env(arxiv=0, archive=1, paper=True)
    env.run.finish(RunStatus.COMPLETED, at=env.run.started_at)
    fake = FakeLLMProvider()
    _ok(fake)
    _approve(fake, env)

    result = await _night(env, fake)()

    assert fake.calls == []
    assert result.status is RunStatus.PARTIAL and "end=run_not_running" in result.notes


async def test_archivo_fallo_al_guardar_tras_llamada_ok_corta_sin_mas_llamadas_y_el_editor_sigue(
    monkeypatch,
) -> None:
    env = _env(arxiv=1, archive=2, paper=True)
    real_add = env.findings.add

    def _failing_add(finding: Finding) -> None:
        if finding.type is FindingType.SOLUTION_TENSION:
            raise RuntimeError("IntegrityError simulado")
        real_add(finding)

    monkeypatch.setattr(env.findings, "add", _failing_add)
    fake = FakeLLMProvider()
    _ok(fake, data=ARXIV_JSON)
    _ok(fake, 2)
    _approve(fake, env)

    result = await _night(env, fake)()

    assert len(_writes(fake)) == 2  # 1 arXiv + 1 archivo; la segunda del archivo no sale
    assert AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.PARTIAL and "end=writer_error" in result.notes


# --- cancelación -------------------------------------------------------------


async def test_hard_stop_en_vuelo_en_la_llamada_del_archivo_es_killed_con_gasto() -> None:
    env = _env(arxiv=0, archive=1)
    provider = _SlowProvider()

    result = await make_run_night(
        env=env,
        provider=provider,
        ingest_result=_ingest(),
        deadline_s=0.05,
        select_solution_tensions=make_solution_selector(env),
        write_solution_tensions=make_solution_writer(env, provider),
    )()

    assert result.status is RunStatus.KILLED and "end=hard_stop" in result.notes
    (call,) = env.agent_calls.calls
    assert call.agent is AgentRole.WRITER and (call.tokens_in, call.tokens_out) == (400, 25)
    assert call.status is AgentCallStatus.ERROR and result.tokens_used == 425
    assert [f for f in env.findings._findings.values() if f.solution_tension] == []


async def test_cancelacion_externa_en_la_llamada_del_archivo_se_relanza() -> None:
    env = _env(arxiv=0, archive=1)
    provider = _SlowProvider()
    night = make_run_night(
        env=env,
        provider=provider,
        ingest_result=_ingest(),
        select_solution_tensions=make_solution_selector(env),
        write_solution_tensions=make_solution_writer(env, provider),
    )
    task = asyncio.create_task(night())
    await asyncio.wait_for(provider.started.wait(), timeout=5)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)


# --- pasadas, topes y orden ------------------------------------------------


async def test_editor_ya_saltado_no_lanza_ninguna_pasada_ni_selecciona(monkeypatch) -> None:
    from nocturna.domain.errors import LLMRateLimited

    new_item = make_item()
    env = _env(arxiv=1, archive=1, paper=True)
    env.items.add_many([new_item])
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=LLMRateLimited("límite"))
    _ok(fake)
    _ok(fake, data=ARXIV_JSON)
    _approve(fake, env)
    selected: list[bool] = []
    real = SelectSolutionTensions.pending

    def _spy(self):
        selected.append(True)
        return real(self)

    monkeypatch.setattr(SelectSolutionTensions, "pending", _spy)
    ingest = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[new_item]
    )
    night = make_run_night(
        env=env,
        provider=fake,
        ingest_result=ingest,
        select_solution_tensions=make_solution_selector(env),
        write_solution_tensions=make_solution_writer(env, fake),
    )

    result = await night()

    assert _roles(fake) == [AgentRole.READER] and selected == []
    assert result.status is RunStatus.PARTIAL


def _phase_logs(caplog, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == event or r.msg == event]


async def test_tope_compartido_arxiv_uno_mas_archivo_uno_y_la_tercera_es_call_limit(
    caplog, monkeypatch
) -> None:
    monkeypatch.setattr(run_night_logger, "disabled", False)
    env = _env(arxiv=1, archive=2, paper=True, max_writer_calls_per_night=2)
    fake = FakeLLMProvider()
    _ok(fake, data=ARXIV_JSON)
    _ok(fake, 2)
    _approve(fake, env)

    with caplog.at_level(logging.INFO):
        result = await _night(env, fake)()

    ends = {
        r.phase: r for r in _phase_logs(caplog, "night.phase_end") if r.phase.startswith("writer")
    }
    assert ends["writer"].stop_reason is None
    assert ends["writer_archive"].stop_reason == "call_limit_reached"
    assert ends["writer_archive"].ok == 1 and ends["writer_archive"].processed == 2
    written = {f.solution_tension_id for f in env.findings._findings.values() if f.solution_tension}
    unwritten = [t for t in env.solution_tensions.all() if t.id not in written]
    assert len(written) == 1 and len(unwritten) == 1
    assert unwritten[0].sigma == 90.0  # la de menor σ es la que queda sin redactar
    assert len(_writes(fake)) == 2
    assert "<tension>" in _writes(fake)[0].prompt
    assert "<solution_tension>" in _writes(fake)[1].prompt
    assert "sigma: 100.00" in _writes(fake)[1].prompt  # mayor σ primero
    assert AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.COMPLETED


async def test_archivo_solo_con_tres_candidatos_y_tope_dos_hace_dos_llamadas() -> None:
    env = _env(arxiv=0, archive=3, paper=True, max_writer_calls_per_night=2)
    fake = FakeLLMProvider()
    _ok(fake, 3)
    _approve(fake, env)

    result = await _night(env, fake)()

    assert len(_writes(fake)) == 2 and result.status is RunStatus.COMPLETED
    assert len([f for f in env.findings._findings.values() if f.solution_tension]) == 2


async def test_orden_writer_v1_antes_que_writer_archive_v1_en_agent_calls() -> None:
    env = _env(arxiv=1, archive=1, paper=True)
    fake = FakeLLMProvider()
    _ok(fake, data=ARXIV_JSON)
    _ok(fake)
    _approve(fake, env)

    await _night(env, fake)()

    versions = [c.prompt_version for c in env.agent_calls.calls if c.agent is AgentRole.WRITER]
    assert versions == ["writer-v1", "writer-archive-v1"]


async def test_el_editor_recibe_los_tres_tipos_en_una_sola_llamada() -> None:
    env = _env(arxiv=1, archive=1, paper=True)
    fake = FakeLLMProvider()
    _ok(fake, data=ARXIV_JSON)
    _ok(fake)
    _approve(fake, env)

    result = await _night(env, fake)()

    editors = [c for c in fake.calls if c.role is AgentRole.EDITOR]
    assert len(editors) == 1
    prompt = editors[0].prompt
    assert "type: paper_explained" in prompt
    assert "type: catalog_tension" in prompt
    assert "type: solution_tension" in prompt
    assert result.candidates == 3 and result.findings_published == 3
    assert len([c for c in env.agent_calls.calls if c.agent is AgentRole.EDITOR]) == 1


async def test_interruptor_apagado_cero_authorize_del_redactor_y_cero_pending() -> None:
    env = _env(arxiv=0, archive=2, paper=True)
    seen = _spy_authorize(env)
    fake = FakeLLMProvider()
    _approve(fake, env)

    result = await make_run_night(env=env, provider=fake, ingest_result=_ingest())()

    assert AgentRole.WRITER not in seen and _writes(fake) == []
    assert result.status is RunStatus.COMPLETED


def test_write_sin_select_tambien_es_value_error() -> None:
    env = _env()
    fake = FakeLLMProvider()
    with pytest.raises(ValueError):
        make_run_night(
            env=env,
            provider=fake,
            ingest_result=_ingest(),
            write_solution_tensions=make_solution_writer(env, fake),
        )


async def test_los_night_item_de_cada_pasada_llevan_su_phase(caplog, monkeypatch) -> None:
    monkeypatch.setattr(run_night_logger, "disabled", False)
    env = _env(arxiv=1, archive=2, paper=True)
    fake = FakeLLMProvider()
    _ok(fake, data=ARXIV_JSON)
    _ok(fake, 2)
    _approve(fake, env)

    with caplog.at_level(logging.INFO):
        await _night(env, fake)()

    phases = [
        r.phase
        for r in _phase_logs(caplog, "night.item")
        if getattr(r, "phase", "").startswith("writer")
    ]
    assert phases == ["writer", "writer_archive", "writer_archive"]
