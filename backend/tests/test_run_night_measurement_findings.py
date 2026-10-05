"""T89: la fase de findings de medidas de `RunNight` (en memoria, sin Claude)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fakes.llm import FakeLLMProvider
from helpers.measurement_findings import hip67522_b, make_arxiv_item, toi_6981_b
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    approve_items_in_editor,
    in_memory_finding_ids_by_item,
    make_generator,
    make_item,
    make_policy,
    make_run_night,
    valid_reading_json,
)

from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.domain.entities import FindingType, ItemStatus, RunStatus
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio


def _popularizer_json() -> dict:
    return {
        "title": "Un titular de prueba",
        "level_curious": "Nivel curioso de prueba",
        "level_amateur": "Nivel aficionado de prueba",
        "level_technical": "Nivel técnico de prueba",
    }


def _empty_ingest(items=()) -> IngestResult:
    return IngestResult(
        fetched=len(items),
        new=len(items),
        duplicates=0,
        skipped=0,
        truncated=False,
        items=list(items),
    )


class _SpyGenerator:
    """Envuelve el generador real y anota qué agentes se habían llamado cuando
    se invocó (para fijar el orden de las fases)."""

    def __init__(self, inner, fake: FakeLLMProvider) -> None:
        self._inner = inner
        self._fake = fake
        self.roles_before: list[AgentRole] | None = None
        self.calls = 0

    def __call__(self, *, run_id, dry_run):
        self.calls += 1
        self.roles_before = [call.role for call in self._fake.calls]
        return self._inner(run_id=run_id, dry_run=dry_run)


class _FailingGenerator:
    def __call__(self, *, run_id, dry_run):
        raise RuntimeError("fallo inesperado del generador")


def _toi_item(external_id="2609.37597"):
    return make_arxiv_item(external_id, status=ItemStatus.READ)


async def test_orden_reader_popularizer_generacion_editor():
    new_item = make_item()
    toi = _toi_item()
    env = Environment(items=[new_item, toi], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(toi_6981_b(toi.id))
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=valid_reading_json(), tokens_in=1000, tokens_out=200)
    fake.respond(AgentRole.POPULARIZER, json=_popularizer_json(), tokens_in=800, tokens_out=150)
    approve_items_in_editor(
        fake,
        finding_ids_by_item=in_memory_finding_ids_by_item(env),
        item_ids=[new_item.id, toi.id],
    )
    spy = _SpyGenerator(make_generator(env), fake)
    run_night = make_run_night(
        env=env, provider=fake, ingest_result=_empty_ingest([new_item]), measurement_findings=spy
    )

    result = await run_night()

    assert spy.roles_before == [AgentRole.READER, AgentRole.POPULARIZER]
    roles = [call.role for call in fake.calls]
    assert roles == [AgentRole.READER, AgentRole.POPULARIZER, AgentRole.EDITOR]
    assert result.status is RunStatus.COMPLETED
    assert result.candidates == 2 and result.findings_published == 2
    types = {f.type for f in env.findings.unpublished_for_run(env.run.id)}
    assert types == set(), "ambos candidatos quedaron publicados"
    assert env.items.get(new_item.id).status is ItemStatus.PUBLISHED
    assert env.items.get(toi.id).status is ItemStatus.READ


async def test_el_editor_se_llama_aunque_solo_haya_candidatos_de_medidas():
    toi = _toi_item()
    env = Environment(items=[toi], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(toi_6981_b(toi.id))
    fake = FakeLLMProvider()
    approve_items_in_editor(
        fake, finding_ids_by_item=in_memory_finding_ids_by_item(env), item_ids=[toi.id]
    )
    run_night = make_run_night(env=env, provider=fake, ingest_result=_empty_ingest())

    result = await run_night()

    assert [call.role for call in fake.calls] == [AgentRole.EDITOR], (
        "ni Reader ni Popularizer: el texto de T89 es determinista"
    )
    assert result.status is RunStatus.COMPLETED
    assert result.candidates == 1 and result.findings_published == 1
    assert "end=edited" in result.notes
    assert env.items.get(toi.id).status is ItemStatus.READ
    assert len(env.agent_calls.calls) == 1, "un único AgentCall: el del Editor"


async def test_sin_candidatos_ni_evaluaciones_el_editor_no_se_llama():
    env = Environment(items=[], policy=make_policy(), now=WITHIN_WINDOW)
    fake = FakeLLMProvider()
    run_night = make_run_night(env=env, provider=fake, ingest_result=_empty_ingest())

    result = await run_night()

    assert fake.calls == []
    assert "end=no_candidates" in result.notes and result.status is RunStatus.COMPLETED


async def test_confirmacion_bloqueada_por_configuracion_no_llega_al_editor():
    hip_item = make_arxiv_item("2609.35979", status=ItemStatus.READ, exoplanet_match=True)
    env = Environment(items=[hip_item], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(hip67522_b(hip_item.id))
    fake = FakeLLMProvider()
    run_night = make_run_night(env=env, provider=fake, ingest_result=_empty_ingest())

    result = await run_night()

    assert fake.calls == []
    assert result.candidates == 0
    assert env.findings.unpublished_for_run(env.run.id) == []


async def test_un_fallo_del_generador_degrada_a_partial_y_el_editor_sigue():
    item = make_item()
    env = Environment(items=[item], policy=make_policy(), now=WITHIN_WINDOW)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=valid_reading_json(), tokens_in=1000, tokens_out=200)
    fake.respond(AgentRole.POPULARIZER, json=_popularizer_json(), tokens_in=800, tokens_out=150)
    approve_items_in_editor(
        fake, finding_ids_by_item=in_memory_finding_ids_by_item(env), item_ids=[item.id]
    )
    run_night = make_run_night(
        env=env,
        provider=fake,
        ingest_result=_empty_ingest([item]),
        measurement_findings=_FailingGenerator(),
    )

    result = await run_night()

    assert result.status is RunStatus.PARTIAL
    assert "end=measurement_findings_error" in result.notes
    assert result.findings_published == 1, "el paper_explained se publica igualmente"


async def test_pasado_hard_stop_no_se_genera_nada():
    item = make_item()
    env = Environment(
        items=[item], policy=make_policy(), now=datetime(2026, 1, 1, 5, 0, 0, tzinfo=UTC)
    )
    fake = FakeLLMProvider()
    spy = _SpyGenerator(make_generator(env), fake)
    run_night = make_run_night(
        env=env, provider=fake, ingest_result=_empty_ingest([item]), measurement_findings=spy
    )

    result = await run_night()

    assert fake.calls == []
    assert spy.calls == 0, "sin Editor posible no tiene sentido generar candidatos"
    assert result.status is RunStatus.KILLED


async def test_si_el_editor_falla_no_se_publica_nada_y_no_se_regenera():
    toi = _toi_item()
    env = Environment(items=[toi], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(toi_6981_b(toi.id))
    fake = FakeLLMProvider()
    fake.respond(AgentRole.EDITOR, raw="no es json", tokens_in=100, tokens_out=10)
    fake.respond(AgentRole.EDITOR, raw="tampoco", tokens_in=100, tokens_out=10)
    run_night = make_run_night(env=env, provider=fake, ingest_result=_empty_ingest())

    result = await run_night()

    assert result.status is RunStatus.PARTIAL and result.findings_published == 0
    orphans = env.findings.unpublished_for_run(env.run.id)
    assert [f.type for f in orphans] == [FindingType.PRIMERA_MEDIDA]

    # Otra noche: la evaluación ya tiene su Finding (huérfano) y no se regenera.
    again = make_generator(env)(run_id=env.run.id, dry_run=False)
    assert again.created == () and again.already_generated == 1
