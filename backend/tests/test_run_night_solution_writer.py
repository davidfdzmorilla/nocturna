"""T85.e: la pasada del archivo de `_phase_writer` de `RunNight` (en memoria, sin Claude)."""

from __future__ import annotations

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
from nocturna.domain.entities import FindingType, ItemStatus, MeasuredParameter, RunStatus
from nocturna.domain.errors import LLMRateLimited
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio

WRITER_JSON = {
    "title": "HD 114082 b: diferencia calculada entre dos soluciones del archivo",
    "level_curious": "c",
    "level_amateur": "a",
    "level_technical": "t",
}
ARXIV_JSON = {**WRITER_JSON, "title": "V1298 Tau b: una discrepancia por revisar"}


def _ingest() -> IngestResult:
    return IngestResult(fetched=0, new=0, duplicates=0, skipped=0, truncated=False, items=[])


def _env(*, arxiv: int = 0, archive: int = 1, **policy):
    defaults = {"writer_reserve_tokens": 40_000, "max_writer_calls_per_night": 5}
    defaults.update(policy)
    arxiv_items = [make_item(status=ItemStatus.READ) for _ in range(arxiv)]
    archive_item = make_archive_item()
    env = Environment(
        items=[*arxiv_items, archive_item], policy=make_policy(**defaults), now=WITHIN_WINDOW
    )
    for item in arxiv_items:
        env.evaluations.add(v1298_evaluation(item.id))
    if archive >= 1:
        env.solution_tensions.add(make_solution_tension(archive_item.id))
    if archive >= 2:
        env.solution_tensions.add(
            make_solution_tension(archive_item.id, parameter=MeasuredParameter.MASS, sigma=40.0)
        )
    return env


def _night(env, fake, *, enabled: bool = True) -> RunNight:
    return make_run_night(
        env=env,
        provider=fake,
        ingest_result=_ingest(),
        select_solution_tensions=make_solution_selector(env) if enabled else None,
        write_solution_tensions=make_solution_writer(env, fake) if enabled else None,
    )


def _writer_calls(fake):
    return [c for c in fake.calls if c.role is AgentRole.WRITER]


def _editor_approves_all(fake, env) -> None:
    approve_items_in_editor(
        fake,
        finding_ids_by_item=in_memory_finding_ids_by_item(env),
        item_ids=[i.id for i in env.items._items.values()],
    )


async def test_interruptor_apagado_cero_llamadas_y_ni_authorize() -> None:
    env = _env()
    fake = FakeLLMProvider()

    result = await _night(env, fake, enabled=False)()

    assert fake.calls == [] and env.agent_calls.calls == []
    assert result.status is RunStatus.COMPLETED


def test_select_y_write_van_los_dos_o_ninguno() -> None:
    env = _env()
    fake = FakeLLMProvider()
    with pytest.raises(ValueError):
        make_run_night(
            env=env,
            provider=fake,
            ingest_result=_ingest(),
            select_solution_tensions=make_solution_selector(env),
        )


async def test_una_tension_del_archivo_se_redacta_y_el_editor_la_recibe() -> None:
    env = _env()
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert [c.role for c in fake.calls] == [AgentRole.WRITER, AgentRole.EDITOR]
    editor_prompt = fake.calls[1].prompt
    assert "type: solution_tension" in editor_prompt and "planet=HD 114082 b" in editor_prompt
    assert "labels=extreme_sigma" in editor_prompt
    assert result.status is RunStatus.COMPLETED and result.findings_published == 1
    (finding,) = [
        f for f in env.findings._findings.values() if f.type is FindingType.SOLUTION_TENSION
    ]
    assert finding.published_at is not None
    assert env.items.get(finding.item_id).status is ItemStatus.NEW


async def test_dos_parametros_de_la_misma_alta_dan_dos_hallazgos_y_dos_llamadas() -> None:
    env = _env(archive=2)
    fake = FakeLLMProvider()
    for _ in range(2):
        fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    await _night(env, fake)()

    assert len(_writer_calls(fake)) == 2
    solution = [
        f for f in env.findings._findings.values() if f.type is FindingType.SOLUTION_TENSION
    ]
    assert len(solution) == 2 and len({f.item_id for f in solution}) == 1


async def test_arxiv_primero_y_despues_el_archivo() -> None:
    env = _env(arxiv=1)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=ARXIV_JSON, tokens_in=700, tokens_out=300)
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    await _night(env, fake)()

    writes = _writer_calls(fake)
    assert "<tension>" in writes[0].prompt and "<solution_tension>" in writes[1].prompt


async def test_d14_el_tope_de_llamadas_de_la_pasada_arxiv_corta_la_del_archivo() -> None:
    env = _env(arxiv=1, max_writer_calls_per_night=1)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=ARXIV_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert len(_writer_calls(fake)) == 1
    assert "<tension>" in _writer_calls(fake)[0].prompt
    assert result.status is RunStatus.COMPLETED


async def test_d14_rate_limited_en_arxiv_no_llama_al_archivo_ni_al_editor() -> None:
    env = _env(arxiv=1)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.WRITER, error=LLMRateLimited("límite alcanzado"))
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert [c.role for c in fake.calls] == [AgentRole.WRITER]
    assert result.status is RunStatus.PARTIAL


async def test_d14_fallo_al_seleccionar_arxiv_corta_el_archivo(monkeypatch) -> None:
    env = _env(arxiv=1)
    fake = FakeLLMProvider()
    _editor_approves_all(fake, env)
    night = _night(env, fake)

    def _boom():
        raise RuntimeError("base caída")

    monkeypatch.setattr(night._select_tensions, "pending", _boom)

    result = await night()

    assert _writer_calls(fake) == []
    assert result.status is RunStatus.PARTIAL and "writer_error" in result.notes


async def test_fallo_al_seleccionar_el_archivo_es_partial_sin_llamadas_y_arxiv_intacto(
    monkeypatch,
) -> None:
    env = _env(arxiv=1)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=ARXIV_JSON, tokens_in=700, tokens_out=300)
    _editor_approves_all(fake, env)
    night = _night(env, fake)

    def _boom():
        raise RuntimeError("base caída")

    monkeypatch.setattr(night._select_solution_tensions, "pending", _boom)

    result = await night()

    assert len(_writer_calls(fake)) == 1 and "<tension>" in _writer_calls(fake)[0].prompt
    assert AgentRole.EDITOR in [c.role for c in fake.calls]
    assert result.status is RunStatus.PARTIAL and "writer_error" in result.notes


async def test_json_invalido_en_el_archivo_deja_la_fila_fallida_y_la_noche_sigue() -> None:
    env = _env()
    fake = FakeLLMProvider()
    for _ in range(2):
        fake.respond(AgentRole.WRITER, raw="no es json", tokens_in=100, tokens_out=10)
    _editor_approves_all(fake, env)

    result = await _night(env, fake)()

    assert len(_writer_calls(fake)) == 2 and result.status is RunStatus.COMPLETED
    assert [r for _, r in make_solution_selector(env).pending().skipped] == ["writer_failed"]
