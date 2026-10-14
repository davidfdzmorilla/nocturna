"""Siembra en PostgreSQL la tensión de masa de HD 202206 c para los humos de T85.e.

Cao et al. 2026 (982,09 ± 44,5 M_earth, por defecto, entrada 2026-08-20) frente a
Benedict & Harrison 2017 (5689 +922/-572 M_earth): 8,2 σ. La siembra pasa por
`ComputeSolutionTensions` real (sin LLM), así que deja el `Item` del archivo y las
filas `solution_tension` (masa y periodo) tal como las dejaría una noche de verdad.

Solo la usan `tests/manual/test_write_solution_tension_smoke.py`,
`tests/manual/test_edit_night_v4_smoke.py` y el test `-m db` que comprueba, sin
llamar a Claude, que la siembra deja una tensión elegible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from fakes.clock import FakeClock

from nocturna import cli
from nocturna.domain.archive import (
    ArchiveParameterValue,
    ArchiveSnapshot,
    ArchiveSolution,
    SnapshotDiff,
    SnapshotKind,
)
from nocturna.domain.entities import Item, MeasuredParameter, Run
from nocturna.domain.solution_tension import SolutionTension
from nocturna.infrastructure.config import PipelineConfig
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
    SqlAlchemySolutionTensionRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

SEED_NOW = datetime(2026, 10, 14, 2, 0, tzinfo=UTC)


def _solution(**kw) -> ArchiveSolution:
    base = {
        "pl_name": "HD 202206 c",
        "hostname": "HD 202206",
        "pl_refname": "x",
        "ref_key": "2026A&A...711A.229C",
        "ref_text": "Cao et al. 2026",
        "arxiv_id": None,
        "soltype": "Published Confirmed",
        "releasedate": date(2026, 8, 20),
        "pl_pubdate": "2026-07",
        "is_default": True,
        "mass": ArchiveParameterValue(982.09, 44.50, -44.50, 0),
        "radius": ArchiveParameterValue(),
        "period": ArchiveParameterValue(1299.22, 3.21, -2.76, 0),
        "pl_bmassprov": "Mass",
        "st_rad": ArchiveParameterValue(),
        "st_mass": ArchiveParameterValue(),
        "discoverymethod": "Radial Velocity",
        "ttv_flag": False,
        "pl_controv_flag": False,
    }
    base.update(kw)
    return ArchiveSolution(**base)


def hd_202206_c_solutions() -> list[ArchiveSolution]:
    return [
        _solution(),
        _solution(
            ref_key="2017AJ....153..258B",
            ref_text="Benedict & Harrison 2017",
            releasedate=date(2017, 2, 1),
            pl_pubdate="2017-03",
            is_default=False,
            mass=ArchiveParameterValue(5689.0, 922.0, -572.0, 0),
            period=ArchiveParameterValue(1260.0, 11.0, -11.0, 0),
        ),
    ]


@dataclass(frozen=True, slots=True)
class SeededSolutionTension:
    item: Item
    mass: SolutionTension
    run_id: UUID | None


def seed_hd202206c_solution_tension(
    db_session_factory, config: PipelineConfig, *, run_budget_tokens: int | None
) -> SeededSolutionTension:
    """Snapshot local con las dos soluciones, `ComputeSolutionTensions` real y un `Run`.

    `run_budget_tokens=None` no abre `Run` (`run_id` queda `None`): para componer la siembra
    con otra que ya abrió el suyo (`uq_runs_status_running` solo admite un `Run` activo).
    """
    solutions = hd_202206_c_solutions()
    snapshot = ArchiveSnapshot(
        id=uuid4(),
        taken_at=SEED_NOW,
        kind=SnapshotKind.FULL,
        max_releasedate=max(s.releasedate for s in solutions),
        rows_total=len(solutions),
        defaults_total=sum(1 for s in solutions if s.is_default),
        duplicate_rows=0,
        payload_sha256="b" * 64,
        requests=1,
        duration_ms=1,
    )
    diff = SnapshotDiff(added=tuple(s.solution_key for s in solutions))
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyArchiveRepository(session).save_snapshot(snapshot, solutions, diff)
    with unit_of_work(db_session_factory) as session:
        cli.compute_solution_tensions_from_config(config, session, FakeClock(SEED_NOW))(
            dry_run=False
        )
    with unit_of_work(db_session_factory) as session:
        run_id = None
        if run_budget_tokens is not None:
            run = Run(started_at=SEED_NOW, budget_tokens=run_budget_tokens)
            SqlAlchemyRunRepository(session).add(run)
            run_id = run.id
        tensions = SqlAlchemySolutionTensionRepository(session).all()
        (mass,) = [
            t
            for t in tensions
            if t.parameter is MeasuredParameter.MASS and t.planet_name == "HD 202206 c"
        ]
        assert mass.item_id is not None
        item = SqlAlchemyItemRepository(session).get(mass.item_id)
        assert item is not None
        return SeededSolutionTension(item=item, mass=mass, run_id=run_id)
