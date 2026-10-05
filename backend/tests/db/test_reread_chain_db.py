"""T82: la cadena T88/T89 tras releer un ítem con `reader-v3` (PostgreSQL, sin Claude)."""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
import sqlalchemy as sa
from factories import make_item, make_reading, make_run
from fakes.catalog import FakeExoplanetCatalog
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from helpers.exoplanet import make_period_rule

from nocturna import cli
from nocturna.application.budget import BudgetPolicy
from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.application.use_cases.generate_measurement_findings import (
    GenerateMeasurementFindings,
)
from nocturna.application.use_cases.read_item import ReaderPrompt, ReadItem, ReadOutcome
from nocturna.application.use_cases.record_tension_evaluations import RecordTensionEvaluations
from nocturna.domain.entities import FindingType, ItemStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
    SqlAlchemyTensionEvaluationRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

pytestmark = [pytest.mark.db, pytest.mark.anyio]

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
ABSTRACT = "The planet TOI-6981 b has a radius of 2.4 (+0.1/-0.1) R_earth, measured this work."


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


def _count(factory, sql: str):
    with factory() as session:
        return session.execute(sa.text(sql)).scalar_one()


async def test_tras_releer_se_evalua_y_se_genera_una_vez_solo_con_la_lectura_nueva(
    db_session_factory,
):
    factory = db_session_factory
    with unit_of_work(factory) as session:
        run = make_run(budget_tokens=300_000, started_at=NOW)
        SqlAlchemyRunRepository(session).add(run)
        item = make_item(
            external_id="2609.37597",
            status=ItemStatus.DISCARDED,
            abstract=ABSTRACT,
            exoplanet_match=True,
        )
        SqlAlchemyItemRepository(session).add_many([item])
        session.flush()
        previous = make_reading(item.id, prompt_version="reader-v2")
        SqlAlchemyReadingRepository(session).add(previous)
    run_id = run.id

    # T88 no ve la lectura v2 (measurements None): nada que evaluar.
    def _record() -> RecordTensionEvaluations:
        return RecordTensionEvaluations(
            readings=SqlAlchemyReadingRepository(session),
            items=SqlAlchemyItemRepository(session),
            evaluations=SqlAlchemyTensionEvaluationRepository(session),
            compute=ComputeTensions(
                FakeExoplanetCatalog(),
                threshold_sigma=3.0,
                period_rule=make_period_rule(),
                clock=FakeClock(NOW),
            ),
        )

    with unit_of_work(factory) as session:
        report = await _record()(dry_run=False)
    assert report.created == 0

    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.READER,
        json={
            "summary": "Resumen",
            "objects": ["TOI-6981"],
            "claims": ["algo"],
            "interest_score": 4,
            "measurements": [
                {
                    "planet_name": "TOI-6981 b",
                    "parameter": "radius",
                    "value": 2.4,
                    "err_plus": 0.1,
                    "err_minus": 0.1,
                    "unit": "R_earth",
                    "limit": "none",
                    "origin": "this_work",
                    "evidence": "2.4 (+0.1/-0.1) R_earth",
                }
            ],
        },
        tokens_in=1000,
        tokens_out=200,
    )
    clock = FakeClock(NOW)
    work = cli._agent_work_factory(factory, run_id, _policy(), clock)
    read_item = ReadItem(
        work=work,
        provider=fake,
        model="claude-sonnet-test",
        max_turns=3,
        max_attempts=2,
        base=ReaderPrompt("v2", "reader-v2", 6_000),
        measures=ReaderPrompt("v3", "reader-v3", 13_000),
        measures_categories=frozenset({"astro-ph.EP"}),
    )
    result = await read_item.reread_with_measurements(item, previous)
    assert result.outcome is ReadOutcome.READ and result.reading is not None
    new_id = result.reading.id

    with unit_of_work(factory) as session:
        report = await _record()(dry_run=False)
    assert report.created == 1
    assert (
        _count(factory, f"select count(*) from tension_evaluation where reading_id = '{new_id}'")
        == 1
    )
    assert (
        _count(
            factory, f"select count(*) from tension_evaluation where reading_id = '{previous.id}'"
        )
        == 0
    )

    with unit_of_work(factory) as session:
        again = await _record()(dry_run=False)
    assert (again.created, again.reevaluated) == (0, 0)
    assert _count(factory, "select count(*) from tension_evaluation") == 1

    def _generate() -> GenerateMeasurementFindings:
        return GenerateMeasurementFindings(
            work=cli._measurement_findings_work_factory(factory),
            clock=FakeClock(NOW),
            planet_overview_url=lambda name: f"https://archive.test/{name}",
            max_candidates=5,
            max_sigma=2.0,
            window_days=30,
            confirmation_enabled=False,
        )

    first = _generate()(run_id=run_id, dry_run=False)
    assert [f.type for f in first.created] == [FindingType.PRIMERA_MEDIDA]
    second = _generate()(run_id=run_id, dry_run=False)
    assert second.created == ()
    assert _count(factory, "select count(*) from findings where type = 'primera_medida'") == 1
    assert _count(factory, "select count(*) from items where status = 'discarded'") == 1
