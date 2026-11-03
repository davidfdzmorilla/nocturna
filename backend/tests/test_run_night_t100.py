"""T100: `RunNight` de punta a punta (en memoria, `FakeLLMProvider`) con las reglas
nuevas: sin `absent`, sin mismo primer autor, y un retirado no se regenera."""

from __future__ import annotations

import pytest
from fakes.llm import FakeLLMProvider
from helpers.measurement_findings import (
    absent_toi_6981_b,
    chakraborty_b,
    hip67522_b,
    make_arxiv_item,
    present_without_reference,
)
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    approve_items_in_editor,
    in_memory_finding_ids_by_item,
    make_generator,
    make_policy,
    make_run_night,
)
from test_run_night_measurement_findings import _empty_ingest

from nocturna.domain.entities import (
    Finding,
    FindingType,
    ItemStatus,
    RunStatus,
)
from nocturna.domain.llm import AgentRole
from nocturna.domain.measurement_findings import first_measurement_from

pytestmark = pytest.mark.anyio


def _read_item(external_id: str, **kw):
    return make_arxiv_item(external_id, status=ItemStatus.READ, **kw)


async def test_el_editor_solo_ve_la_primera_medida_del_planeta_presente_no_la_del_absent():
    absent_item = _read_item("2609.37597")
    present_item = _read_item("2610.00001")
    env = Environment(items=[absent_item, present_item], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(absent_toi_6981_b(absent_item.id))
    env.evaluations.add(present_without_reference(present_item.id))
    fake = FakeLLMProvider()
    approve_items_in_editor(
        fake,
        finding_ids_by_item=in_memory_finding_ids_by_item(env),
        item_ids=[absent_item.id, present_item.id],
    )
    run_night = make_run_night(env=env, provider=fake, ingest_result=_empty_ingest())

    result = await run_night()

    assert [c.role for c in fake.calls] == [AgentRole.EDITOR]
    prompt = fake.calls[0].prompt
    assert prompt.count("candidate_id:") == 1
    assert "planet=Foo-1 b" in prompt and "TOI-6981" not in prompt
    assert "reference=none (no_comparable_solution)" in prompt
    assert result.candidates == 1 and result.findings_published == 1
    assert result.status is RunStatus.COMPLETED


async def test_una_confirmacion_del_mismo_primer_autor_no_llega_al_editor():
    item = _read_item("2609.35979", authors="A. Smith, E. Knudstrup, B. Doe")
    env = Environment(items=[item], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(
        hip67522_b(item.id, reference=chakraborty_b(reference="Knudstrup et al. 2022"))
    )
    fake = FakeLLMProvider()
    generator = make_generator(env, confirmation_enabled=True)
    run_night = make_run_night(
        env=env, provider=fake, ingest_result=_empty_ingest(), measurement_findings=generator
    )

    result = await run_night()

    assert fake.calls == [], "sin candidatos no hay Editor"
    assert result.candidates == 0 and result.findings_published == 0
    assert env.findings.unpublished_for_run(env.run.id) == []
    # El informe del generador: ni creada ni bloqueada, simplemente no es confirmación.
    report = generator(run_id=env.run.id, dry_run=True)
    assert report.created == () and report.blocked_confirmations == ()
    assert report.skipped_same_team == 1 and report.skipped_unknown_team == 0


async def test_la_misma_confirmacion_con_otro_equipo_si_llega_al_editor():
    item = _read_item("2609.35979", authors="A. Smith, B. Doe")
    env = Environment(items=[item], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(
        hip67522_b(item.id, reference=chakraborty_b(reference="Knudstrup et al. 2022"))
    )
    fake = FakeLLMProvider()
    approve_items_in_editor(
        fake, finding_ids_by_item=in_memory_finding_ids_by_item(env), item_ids=[item.id]
    )
    run_night = make_run_night(
        env=env,
        provider=fake,
        ingest_result=_empty_ingest(),
        measurement_findings=make_generator(env, confirmation_enabled=True),
    )

    result = await run_night()

    assert "type: confirmacion_independiente" in fake.calls[0].prompt
    assert result.findings_published == 1


async def test_un_hallazgo_retirado_no_se_regenera_ni_se_toca_ni_lo_ve_el_editor():
    item = _read_item("2610.00001")
    evaluation = present_without_reference(item.id)
    env = Environment(items=[item], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(evaluation)
    retracted = Finding(
        item_id=item.id,
        run_id=env.run.id,
        type=FindingType.PRIMERA_MEDIDA,
        title="Primera medida retirada",
        level_curious="c",
        level_amateur="a",
        level_technical="t",
        first_measurement=first_measurement_from(evaluation, archive_url="https://a/x"),
        tension_evaluation_id=evaluation.id,
    )
    retracted.publish(confidence=0.8, at=WITHIN_WINDOW)
    retracted.retract(WITHIN_WINDOW, "El paper se retiró en arXiv.")
    env.findings.add(retracted)
    fake = FakeLLMProvider()
    run_night = make_run_night(env=env, provider=fake, ingest_result=_empty_ingest())

    result = await run_night()

    assert fake.calls == [], "no hay candidatos: el Editor no ve el retirado ni su regeneración"
    assert result.candidates == 0 and result.findings_published == 0
    stored = env.findings.get(retracted.id)
    assert stored is retracted
    assert stored.retracted_at == WITHIN_WINDOW
    assert stored.retraction_note == "El paper se retiró en arXiv."
    assert stored.published_at == WITHIN_WINDOW
    assert env.findings.unpublished_for_run(env.run.id) == []
    assert len(env.findings._findings) == 1, "no se creó ningún Finding nuevo"
