"""T83: `RecordTensionEvaluations` contra PostgreSQL con la versión de revista del propio paper.

Una `awaiting_reference` pasa a `closed_loop` (conservando su `id`) cuando entra
en el archivo la versión de revista del propio paper con los mismos valores y
sin `arxiv_id`; las evaluaciones terminales no se tocan. Sin red ni Claude: el
planeta se resuelve por el índice local y el alias nunca se consulta.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest
from factories import make_item, make_reading
from fakes.clock import FakeClock
from helpers.exoplanet import make_measurement, make_own_solution_rule, make_period_rule
from test_archive_repository import save, snapshot, solution

from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.application.use_cases.record_tension_evaluations import RecordTensionEvaluations
from nocturna.domain.entities import MeasurementUnit
from nocturna.domain.tension import EvaluationStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyTensionEvaluationRepository,
)
from nocturna.infrastructure.exoplanet_archive.catalog import ExoplanetArchiveCatalog

pytestmark = pytest.mark.anyio

PLANET = "HIP 67522 b"
OTHER = "WASP-12 b"
PAPER = "2606.18045"
NOW = datetime(2026, 10, 21, 3, 0, tzinfo=UTC)
JOURNAL = "2026AJ....168..297C"


class _NoAlias:
    """El alias no debe consultarse: los planetas están en el índice local."""

    async def lookup_alias(self, name: str):  # pragma: no cover - fallaría el test
        raise AssertionError(f"alias consultado para {name!r}")


def _barber():
    """Fila por defecto de otro paper, sin masa: el planeta existe pero no hay previa."""
    return solution(
        PLANET,
        "2024AJ....168..000B",
        mass=None,
        is_default=True,
        arxiv_id=None,
        pl_pubdate="2024-09",
        releasedate=date(2024, 9, 10),
    )


def _journal_version(mass=13.8):
    return solution(
        PLANET,
        JOURNAL,
        mass=mass,
        arxiv_id=None,
        pl_pubdate="2026-10",
        releasedate=date(2026, 10, 20),
    )


def _independent(pl_name, ref_key, mass, arxiv_id):
    return solution(
        pl_name,
        ref_key,
        mass=mass,
        arxiv_id=arxiv_id,
        pl_pubdate="2026-05",
        releasedate=date(2026, 9, 1),
    )


class World:
    def __init__(self, session) -> None:
        self.session = session
        self.items = SqlAlchemyItemRepository(session)
        self.readings = SqlAlchemyReadingRepository(session)
        self.evaluations = SqlAlchemyTensionEvaluationRepository(session)
        self.archive = SqlAlchemyArchiveRepository(session)
        self.n_snapshots = 0

    def add_paper(self, external_id, planet, value, published_at):
        item = make_item(external_id=external_id, published_at=published_at)
        self.items.add_many([item])
        session = self.session
        session.flush()
        measurement = make_measurement(
            value, 1.0, 1.0, planet_name=planet, unit=MeasurementUnit.M_EARTH
        )
        reading = make_reading(item.id, measurements=(measurement,))
        self.readings.add(reading)
        session.flush()
        return item, reading

    def snapshot(self, solutions) -> None:
        save(self.archive, snapshot(offset_days=self.n_snapshots), solutions)
        self.n_snapshots += 1
        self.session.flush()

    def use_case(self) -> RecordTensionEvaluations:
        catalog = ExoplanetArchiveCatalog(self.archive, _NoAlias())  # type: ignore[arg-type]
        compute = ComputeTensions(
            catalog,
            threshold_sigma=3.0,
            period_rule=make_period_rule(),
            own_solution_rule=make_own_solution_rule(),
            clock=FakeClock(NOW),
        )
        return RecordTensionEvaluations(
            readings=self.readings, items=self.items, evaluations=self.evaluations, compute=compute
        )

    def stored(self, planet):
        found = [e for e in self.evaluations.all() if e.planet_name == planet]
        assert len(found) == 1
        return found[0]


async def test_awaiting_reference_pasa_a_closed_loop_al_entrar_la_version_de_revista(db_session):
    world = World(db_session)
    world.add_paper(PAPER, PLANET, 13.8, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    world.snapshot([_barber()])

    first = await world.use_case()(dry_run=False)

    assert (first.created, first.reevaluated) == (1, 0)
    waiting = world.stored(PLANET)
    assert waiting.status == EvaluationStatus.AWAITING_REFERENCE

    journal = _journal_version()
    world.snapshot([_barber(), journal])
    second = await world.use_case()(dry_run=False)

    assert (second.created, second.reevaluated, second.unchanged) == (0, 1, 0)
    assert second.by_status == {EvaluationStatus.CLOSED_LOOP: 1}
    closed = world.stored(PLANET)
    assert closed.id == waiting.id
    assert closed.status == EvaluationStatus.CLOSED_LOOP
    assert closed.own_solution_key == journal.solution_key
    assert closed.result is None


async def test_la_version_de_revista_no_deja_la_evaluacion_evaluated_con_sigma_cero(db_session):
    world = World(db_session)
    world.add_paper(PAPER, PLANET, 13.8, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    world.snapshot([_barber(), _journal_version()])

    report = await world.use_case()(dry_run=False)

    assert report.by_status == {EvaluationStatus.CLOSED_LOOP: 1}
    assert EvaluationStatus.EVALUATED not in report.by_status


async def test_dry_run_cuenta_la_reevaluacion_pero_no_escribe(db_session):
    world = World(db_session)
    world.add_paper(PAPER, PLANET, 13.8, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    world.snapshot([_barber()])
    await world.use_case()(dry_run=False)
    world.snapshot([_barber(), _journal_version()])

    report = await world.use_case()(dry_run=True)

    assert report.reevaluated == 1
    assert world.stored(PLANET).status == EvaluationStatus.AWAITING_REFERENCE


async def test_las_terminales_no_se_tocan_cuando_despues_entra_la_version_de_revista(db_session):
    world = World(db_session)
    published = datetime(2026, 6, 20, 8, 0, tzinfo=UTC)
    world.add_paper(PAPER, PLANET, 13.8, published)
    world.add_paper("2606.99999", OTHER, 14.0, published)
    independent = _independent(OTHER, "2026arXiv260500001X", 14.4, "2605.00001")
    world.snapshot([_barber(), independent])
    await world.use_case()(dry_run=False)
    evaluated = world.stored(OTHER)
    assert evaluated.status == EvaluationStatus.EVALUATED

    own_by_value = solution(
        OTHER,
        "2026AJ....169..001X",
        mass=14.0,
        arxiv_id=None,
        pl_pubdate="2026-10",
        releasedate=date(2026, 10, 20),
    )
    world.snapshot([_barber(), independent, own_by_value, _journal_version()])
    report = await world.use_case()(dry_run=False)

    again = world.stored(OTHER)
    assert (again.id, again.status, again.own_solution_key) == (
        evaluated.id,
        evaluated.status,
        None,
    )
    assert again.same_outcome(evaluated)
    assert report.kept_terminal >= 1
    assert world.stored(PLANET).status == EvaluationStatus.CLOSED_LOOP


async def test_una_awaiting_sin_la_version_de_revista_sigue_igual_y_no_cambia_el_id(db_session):
    world = World(db_session)
    world.add_paper(PAPER, PLANET, 13.8, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    world.snapshot([_barber()])
    await world.use_case()(dry_run=False)
    before = world.stored(PLANET)

    report = await world.use_case()(dry_run=False)

    assert (report.reevaluated, report.unchanged) == (0, 1)
    after = world.stored(PLANET)
    assert (after.id, after.status) == (before.id, EvaluationStatus.AWAITING_REFERENCE)
