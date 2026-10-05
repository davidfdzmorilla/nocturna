"""T89: `EditNight` y `GenerateMeasurementFindings` contra PostgreSQL real.

`FakeLLMProvider` (ningún test llama a Claude); repositorios y unidad de
trabajo reales. El `Item` del candidato de medidas no cambia de estado; el
`Finding` se persiste con su payload y se publica con `save()`.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime, time

import pytest
import sqlalchemy as sa
from factories import make_finding, make_item, make_reading, make_run
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from helpers.measurement_findings import toi_6981_b

from nocturna import cli
from nocturna.application.budget import BudgetPolicy
from nocturna.application.use_cases.edit_night import EditNight, EditOutcome
from nocturna.application.use_cases.generate_measurement_findings import (
    GenerateMeasurementFindings,
)
from nocturna.domain.entities import FindingType, ItemStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.models import FindingRow, ItemRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
    SqlAlchemyTensionEvaluationRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

pytestmark = [pytest.mark.db, pytest.mark.anyio]

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)


def _policy() -> BudgetPolicy:
    return BudgetPolicy(
        nightly_tokens=300_000,
        editor_reserve_tokens=60_000,
        max_items_per_night=10,
        max_turns_per_agent=3,
        max_editor_calls_per_night=2,
        max_calls_per_item=2,
        item_timeout_s=180,
        editor_timeout_s=300,
        run_timeout_s=16_200,
        window_start=time(0, 0),
        window_hard_stop=time(4, 45),
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _seed(factory):
    """Un ítem READ con su lectura y la evaluación pendiente de TOI-6981 b, y un
    segundo ítem READ con un `paper_explained`; un Run."""
    with unit_of_work(factory) as session:
        run = make_run(budget_tokens=300_000)
        SqlAlchemyRunRepository(session).add(run)
        toi = make_item(external_id="2609.37597", status=ItemStatus.READ)
        paper = make_item(external_id="2609.00001", status=ItemStatus.READ)
        SqlAlchemyItemRepository(session).add_many([toi, paper])
        session.flush()
        evaluation = toi_6981_b(toi.id)
        reading = make_reading(toi.id, measurements=evaluation.measurements)
        SqlAlchemyReadingRepository(session).add(reading)
        session.flush()
        SqlAlchemyTensionEvaluationRepository(session).add(
            dataclasses.replace(evaluation, reading_id=reading.id)
        )
        paper_finding = make_finding(paper.id, run.id, title="Paper explicado")
        SqlAlchemyFindingRepository(session).add(paper_finding)
        session.flush()
        return run.id, toi.id, paper.id, paper_finding.id


def _generator(factory) -> GenerateMeasurementFindings:
    return GenerateMeasurementFindings(
        work=cli._measurement_findings_work_factory(factory),
        clock=FakeClock(NOW),
        planet_overview_url=lambda name: f"https://archive.test/{name}",
        max_candidates=5,
        max_sigma=2.0,
        window_days=30,
        confirmation_enabled=False,
    )


async def test_generar_y_editar_de_extremo_a_extremo_con_la_base_real(db_session_factory):
    run_id, toi_id, paper_id, paper_finding_id = _seed(db_session_factory)
    report = _generator(db_session_factory)(run_id=run_id, dry_run=False)
    assert [f.type for f in report.created] == [FindingType.PRIMERA_MEDIDA]
    (first,) = report.created

    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.EDITOR,
        json={
            "publish": [
                {"candidate_id": str(first.id), "confidence": 0.7, "reason": "Primera medida."}
            ]
        },
        tokens_in=900,
        tokens_out=80,
    )
    clock = FakeClock(NOW)
    work = cli._agent_work_factory(db_session_factory, run_id, _policy(), clock)
    edit_night = EditNight(
        work=work,
        provider=fake,
        clock=clock,
        system_prompt="prompt del Editor",
        prompt_version="editor-v2",
        model="claude-opus-test",
        max_turns=3,
        max_attempts=1,
        base_tokens=1_000,
        tokens_per_candidate=200,
    )

    result = await edit_night(run_id=run_id)

    assert result.outcome is EditOutcome.EDITED and result.candidates == 2
    assert [f.id for f in result.published] == [first.id]
    assert [f.id for f in result.discarded] == [paper_finding_id]
    with db_session_factory() as session:
        row = session.get(FindingRow, first.id)
        assert row.published_at is not None and row.confidence == 0.7
        assert row.first_measurement["paper_planet_name"] == "TOI-6981 b"
        assert session.get(ItemRow, toi_id).status is ItemStatus.READ
        assert session.get(ItemRow, paper_id).status is ItemStatus.DISCARDED
        # Reconstruido por el repositorio, con el payload íntegro.
        reloaded = SqlAlchemyFindingRepository(session).get_published(first.id)
        assert reloaded is not None and reloaded.first_measurement == first.first_measurement
        assert reloaded.tension_evaluation_id == first.tension_evaluation_id


async def test_el_generador_es_idempotente_con_la_base_real(db_session_factory):
    run_id, *_ = _seed(db_session_factory)
    generator = _generator(db_session_factory)

    first = generator(run_id=run_id, dry_run=False)
    second = generator(run_id=run_id, dry_run=False)

    assert len(first.created) == 1
    assert second.created == () and second.already_generated == 1
    with db_session_factory() as session:
        count = session.execute(
            sa.text("select count(*) from findings where type = 'primera_medida'")
        ).scalar_one()
    assert count == 1


async def test_dry_run_del_generador_no_escribe_con_la_base_real(db_session_factory):
    run_id, *_ = _seed(db_session_factory)

    report = _generator(db_session_factory)(run_id=run_id, dry_run=True)

    assert len(report.created) == 1
    with db_session_factory() as session:
        count = session.execute(
            sa.text("select count(*) from findings where type = 'primera_medida'")
        ).scalar_one()
    assert count == 0
