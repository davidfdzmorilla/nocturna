"""Helpers compartidos de los tests de `RunNight` (en memoria, sin Claude)."""

from __future__ import annotations

import itertools
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time
from uuid import UUID

from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.tension_evaluations import InMemoryTensionEvaluationRepository
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryFindingRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    make_measurement_findings_work_factory,
    make_work_factory,
)

from helpers.exoplanet import make_own_solution_rule
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.edit_night import EditNight
from nocturna.application.use_cases.generate_measurement_findings import (
    GenerateMeasurementFindings,
)
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.popularize_reading import PopularizeReading
from nocturna.application.use_cases.read_item import ReaderPrompt, ReadItem
from nocturna.application.use_cases.run_night import RunNight
from nocturna.domain.entities import Item, Run
from nocturna.domain.llm import AgentRole

WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)


def make_policy(**overrides: object) -> BudgetPolicy:
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


ITEM_SEQ = itertools.count()


def make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "source": "arxiv",
        # Creciente: `next_unread` desempata por `external_id` (como el
        # repositorio real), así que el orden de creación debe ser el de lectura.
        "external_id": f"2501.{next(ITEM_SEQ):06d}",
        "title": "Un título de prueba",
        "abstract": "Un abstract de prueba con contenido suficiente para el Reader.",
        "categories": ["astro-ph.GA"],
        "published_at": WITHIN_WINDOW,
        "fetched_at": WITHIN_WINDOW,
    }
    defaults.update(overrides)
    return Item(**defaults)


def valid_reading_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "summary": "Resumen de prueba",
        "objects": ["NGC 1234"],
        "claims": ["Una afirmación de prueba"],
        "interest_score": 5,
    }
    defaults.update(overrides)
    return defaults


class Environment:
    def __init__(self, *, items: list[Item], policy: BudgetPolicy, now: datetime) -> None:
        self.run = Run(started_at=now, budget_tokens=policy.nightly_tokens)
        self.runs = InMemoryRunRepository(self.run)
        self.items = InMemoryItemRepository(*items)
        self.readings = InMemoryReadingRepository()
        self.findings = InMemoryFindingRepository()
        self.agent_calls = InMemoryAgentCallRepository()
        self.evaluations = InMemoryTensionEvaluationRepository()
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


def make_generator(
    env: Environment,
    *,
    max_candidates: int = 5,
    max_sigma: float = 2.0,
    window_days: int = 30,
    confirmation_enabled: bool = False,
) -> GenerateMeasurementFindings:
    """`GenerateMeasurementFindings` sobre las evaluaciones en memoria de `env`."""
    return GenerateMeasurementFindings(
        work=make_measurement_findings_work_factory(
            items=env.items, findings=env.findings, evaluations=env.evaluations
        ),
        clock=env.clock,
        planet_overview_url=lambda name: f"https://archive.example/overview/{name}",
        max_candidates=max_candidates,
        max_sigma=max_sigma,
        window_days=window_days,
        confirmation_enabled=confirmation_enabled,
        own_solution_rule=make_own_solution_rule(),
    )


def make_run_night(
    *,
    env: Environment,
    provider: FakeLLMProvider,
    ingest_result: IngestResult,
    max_items: int = 10,
    max_consecutive_failures: int = 5,
    deadline_s: int = 16_200,
    measurement_findings: GenerateMeasurementFindings | None = None,
) -> RunNight:
    read_item = ReadItem(
        work=env.work,
        provider=provider,
        model="claude-sonnet-test",
        max_turns=3,
        max_attempts=2,
        base=ReaderPrompt(
            system_prompt="prompt del Reader", prompt_version="reader-v1", estimated_tokens=500
        ),
        measures=ReaderPrompt(
            system_prompt="prompt del Reader v3 (no usado en este test)",
            prompt_version="reader-v1-v3",
            estimated_tokens=500,
        ),
        measures_categories=frozenset(),
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
        measurement_findings=measurement_findings or make_generator(env),
        run_id=env.run.id,
        max_items=max_items,
        max_consecutive_failures=max_consecutive_failures,
        deadline_s=deadline_s,
    )


def approve_items_in_editor(
    fake: FakeLLMProvider,
    *,
    finding_ids_by_item: Callable[[], dict[UUID, list[UUID]]],
    item_ids: Sequence[UUID],
    confidence: float = 0.8,
    reason: str = "Motivo de prueba.",
    tokens_in: int = 1000,
    tokens_out: int = 100,
) -> None:
    """Programa una respuesta del Editor que aprueba los `Finding` de `item_ids`.

    Desde T89 el Editor decide por `candidate_id = finding.id`, que solo
    existe cuando el Popularizer ya persistió el `Finding`: la respuesta se
    construye en el momento de la llamada, con `finding_ids_by_item()`.
    """

    def _build(_request: object) -> dict:
        mapping = finding_ids_by_item()
        return {
            "publish": [
                {"candidate_id": str(finding_id), "confidence": confidence, "reason": reason}
                for item_id in item_ids
                for finding_id in mapping.get(item_id, [])
            ]
        }

    fake.respond_with(AgentRole.EDITOR, build=_build, tokens_in=tokens_in, tokens_out=tokens_out)


def in_memory_finding_ids_by_item(env: Environment) -> Callable[[], dict[UUID, list[UUID]]]:
    def _mapping() -> dict[UUID, list[UUID]]:
        result: dict[UUID, list[UUID]] = {}
        for finding in env.findings.unpublished_for_run(env.run.id):
            result.setdefault(finding.item_id, []).append(finding.id)
        return result

    return _mapping


def db_finding_ids_by_item(session_factory: object) -> Callable[[], dict[UUID, list[UUID]]]:
    """Como `in_memory_finding_ids_by_item`, contra la base real: los
    `Finding` sin publicar, agrupados por `item_id`."""
    from sqlalchemy import select

    from nocturna.infrastructure.db.models import FindingRow

    def _mapping() -> dict[UUID, list[UUID]]:
        result: dict[UUID, list[UUID]] = {}
        with session_factory() as session:  # type: ignore[operator]
            rows = session.execute(
                select(FindingRow.item_id, FindingRow.id).where(FindingRow.published_at.is_(None))
            ).all()
        for item_id, finding_id in rows:
            result.setdefault(item_id, []).append(finding_id)
        return result

    return _mapping
