"""Siembra en PostgreSQL la evaluación de V1298 Tau b para los humos del redactor (T76).

Solo la usan `tests/manual/test_write_tension_smoke.py`,
`tests/manual/test_edit_night_v3_smoke.py` y el test `-m db` que comprueba
que la siembra es válida sin llamar a Claude.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helpers.archive import load_t71c_measurements, v1298_archive_solutions
from helpers.exoplanet import V1298_MEASURES, make_reading
from helpers.tension_writer import v1298_evaluation
from nocturna.domain.archive import ArchiveSnapshot, SnapshotDiff, SnapshotKind
from nocturna.domain.entities import Item, ItemStatus, Run
from nocturna.domain.tension import TensionEvaluation
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
    SqlAlchemyTensionEvaluationRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

PAPER_ARXIV_ID = "2609.30038"
PAPER_PUBLISHED_AT = datetime(2026, 9, 30, 2, 0, tzinfo=UTC)

# El CSV grabado (`ps_v1298tau.csv`) no trae `pl_pubdate`. La base real, a
# 2026-10-08, dice que la referencia por defecto (Livingston et al. 2026) tiene
# `pl_pubdate = "2026-01"` y ningún `arxiv_id` en el archivo: se fija aquí el
# dato real sin tocar el CSV. Con esa fecha `classify_solution` la da por
# INDEPENDIENTE del paper (publicada nueve meses antes).
REAL_REFERENCE_OVERRIDES = {"arxiv_id": None, "pl_pubdate": "2026-01"}


@dataclass(frozen=True, slots=True)
class SeededTension:
    item: Item
    evaluation: TensionEvaluation
    run_id: UUID


def _seed_archive_snapshot(db_session_factory) -> None:
    """Snapshot local con las soluciones reales de V1298 Tau: las evaluaciones
    guardadas referencian sus claves (FK a `archive_solution`)."""
    solutions = v1298_archive_solutions()
    snapshot = ArchiveSnapshot(
        id=uuid4(),
        taken_at=PAPER_PUBLISHED_AT,
        kind=SnapshotKind.FULL,
        max_releasedate=max(s.releasedate for s in solutions),
        rows_total=len(solutions),
        defaults_total=sum(1 for s in solutions if s.is_default),
        duplicate_rows=0,
        payload_sha256="a" * 64,
        requests=1,
        duration_ms=1,
    )
    diff = SnapshotDiff(added=tuple(s.solution_key for s in solutions))
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyArchiveRepository(session).save_snapshot(snapshot, solutions, diff)


def seed_v1298_tension(db_session_factory, *, run_budget_tokens: int) -> SeededTension:
    """Ítem READ del paper 2609.30038, su `Reading` con medidas, un `Run` y la
    `TensionEvaluation` `evaluated` de la masa de V1298 Tau b."""
    _seed_archive_snapshot(db_session_factory)
    with unit_of_work(db_session_factory) as session:
        item = Item(
            source="arxiv",
            external_id=PAPER_ARXIV_ID,
            title="Masses of the V1298 Tau planets (humo T76)",
            abstract="Abstract no usado por el redactor.",
            categories=["astro-ph.EP"],
            published_at=PAPER_PUBLISHED_AT,
            fetched_at=PAPER_PUBLISHED_AT,
            status=ItemStatus.READ,
        )
        SqlAlchemyItemRepository(session).add_many([item])
        run = Run(started_at=PAPER_PUBLISHED_AT, budget_tokens=run_budget_tokens)
        SqlAlchemyRunRepository(session).add(run)
        session.flush()
        reading = make_reading(item.id, load_t71c_measurements(V1298_MEASURES))
        SqlAlchemyReadingRepository(session).add(reading)
        session.flush()
        evaluation = replace(
            v1298_evaluation(item.id, reference_overrides=REAL_REFERENCE_OVERRIDES),
            reading_id=reading.id,
        )
        SqlAlchemyTensionEvaluationRepository(session).add(evaluation)
        return SeededTension(item=item, evaluation=evaluation, run_id=run.id)
