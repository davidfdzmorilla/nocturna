"""T95: en una noche, la tensión del archivo que la vía arXiv ya cuenta no se redacta."""

from __future__ import annotations

import pytest
from fakes.llm import FakeLLMProvider
from helpers.cross_path_dedup import (
    HD65216_ARXIV_ID,
    archive_tension,
)
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
from helpers.solution_tension_writer import make_archive_item
from helpers.tension_writer import v1298_evaluation

from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.domain.entities import FindingType, ItemStatus, MeasuredParameter
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio

WRITER_JSON = {"title": "t", "level_curious": "c", "level_amateur": "a", "level_technical": "t"}


async def test_misma_noche_una_sola_llamada_writer_y_un_candidato_para_el_editor() -> None:
    arxiv_item = make_item(external_id=HD65216_ARXIV_ID, status=ItemStatus.READ)
    archive_item = make_archive_item(planet="HD 65216 c")
    env = Environment(
        items=[arxiv_item, archive_item],
        policy=make_policy(writer_reserve_tokens=40_000, max_writer_calls_per_night=5),
        now=WITHIN_WINDOW,
    )
    # Evaluación elegible de la vía arXiv con los datos de V1298 Tau b (masa),
    # colgada del ítem cuyo external_id es el del caso HD 65216 (HD65216_ARXIV_ID);
    # la alta del archivo cita ese mismo paper, planeta y parámetro.
    evaluation = v1298_evaluation(arxiv_item.id)
    env.evaluations.add(evaluation)
    env.solution_tensions.add(
        archive_tension(
            archive_item.id,
            planet=evaluation.archive_planet_name,
            parameter=evaluation.parameter,
        )
    )
    # el `arxiv_id` de la alta es el `external_id` del Item arXiv
    assert evaluation.parameter is MeasuredParameter.MASS

    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    fake.respond(AgentRole.WRITER, json=WRITER_JSON, tokens_in=700, tokens_out=300)
    approve_items_in_editor(
        fake,
        finding_ids_by_item=in_memory_finding_ids_by_item(env),
        item_ids=[arxiv_item.id, archive_item.id],
    )
    night = make_run_night(
        env=env,
        provider=fake,
        ingest_result=IngestResult(
            fetched=0, new=0, duplicates=0, skipped=0, truncated=False, items=[]
        ),
        select_solution_tensions=make_solution_selector(env),
        write_solution_tensions=make_solution_writer(env, fake),
    )

    await night()

    writes = [c for c in fake.calls if c.role is AgentRole.WRITER]
    assert len(writes) == 1
    assert "<tension>" in writes[0].prompt and "<solution_tension>" not in writes[0].prompt
    types = [f.type for f in env.findings._findings.values()]
    assert types == [FindingType.CATALOG_TENSION]
    editor_prompt = next(c.prompt for c in fake.calls if c.role is AgentRole.EDITOR)
    assert "type: solution_tension" not in editor_prompt
