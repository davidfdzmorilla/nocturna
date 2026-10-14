"""`SqlAlchemySolutionTensionRepository`, consultas de T85 sobre el archivo,
`Finding` de tipo `solution_tension` y guarda de gasto de `next_unread` (PostgreSQL)."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from factories import make_finding, make_item, make_run
from sqlalchemy.exc import IntegrityError
from test_archive_repository import save, snapshot, solution

from nocturna.domain.archive import catalog_solution_from_archive
from nocturna.domain.entities import (
    ITEM_SOURCE_EXOPLANET_ARCHIVE,
    FindingType,
    MeasuredParameter,
    ReferenceRole,
    SolutionComparison,
    SolutionTensionData,
    SolutionTensionLabel,
)
from nocturna.domain.solution_tension import SkipReason, SolutionTension
from nocturna.domain.tension import PeriodCheck
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
    SqlAlchemySolutionTensionRepository,
)

MASS = MeasuredParameter.MASS
PERIOD = MeasuredParameter.PERIOD
PLANET = "HD 1 b"
_T = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)


class World:
    def __init__(self, session) -> None:
        self.session = session
        self.old = solution(
            PLANET, "Old 2017", mass=5.0, is_default=False, releasedate=date(2026, 7, 1)
        )
        self.new = solution(
            PLANET, "New 2026", mass=1.0, is_default=True, releasedate=date(2026, 8, 20)
        )
        self.other = solution(
            "HD 2 c", "Else 2026", mass=2.0, is_default=True, releasedate=date(2026, 9, 1)
        )
        self.msini = solution(
            PLANET,
            "Msini 2026",
            mass=3.0,
            is_default=False,
            releasedate=date(2026, 9, 5),
            soltype="Published Candidate",
        )
        self.archive = SqlAlchemyArchiveRepository(session)
        save(self.archive, snapshot(), [self.old, self.new, self.other, self.msini])
        self.repo = SqlAlchemySolutionTensionRepository(session)

    def cat(self, sol, parameter=MASS, *, is_default=False):
        found = catalog_solution_from_archive(sol, parameter, is_default=is_default)
        assert found is not None
        return found

    def tension(self, **overrides) -> SolutionTension:
        fields = {
            "new_solution_key": self.new.solution_key,
            "reference_solution_key": self.old.solution_key,
            "planet_name": PLANET,
            "parameter": MASS,
            "new": self.cat(self.new, is_default=True),
            "reference": self.cat(self.old),
            "reference_role": ReferenceRole.DEFAULT,
            "computed_at": _T,
            "sigma": 8.2,
            "others": (SolutionComparison(self.cat(self.msini), 3.5),),
            "period_check": None,
            "labels": (SolutionTensionLabel.CONTROVERSIAL,),
        }
        fields.update(overrides)
        return SolutionTension(**fields)


def test_roundtrip_incluida_una_excluida(db_session):
    world = World(db_session)
    passing = world.tension()
    skipped = world.tension(
        parameter=PERIOD,
        new=world.cat(world.new, PERIOD, is_default=True),
        reference=world.cat(world.old, PERIOD),
        sigma=1.5,
        others=(),
        period_check=PeriodCheck(min_difference_met=True, alias_suspected=False),
        labels=(),
        skip_reason=SkipReason.BELOW_THRESHOLD,
    )
    world.repo.add(passing)
    world.repo.add(skipped)
    db_session.expire_all()

    assert world.repo.get(passing.id) == passing
    assert world.repo.get(skipped.id) == skipped
    assert {t.id for t in world.repo.all()} == {passing.id, skipped.id}
    assert world.repo.get(passing.id).item_id is None


def test_keys_computed_y_unicidad(db_session):
    world = World(db_session)
    world.repo.add(world.tension())
    assert world.repo.keys_computed() == {(world.new.solution_key, MASS)}
    with pytest.raises(IntegrityError, match="uq_solution_tension_new_solution_key_parameter"):
        world.repo.add(world.tension(sigma=9.0))


def test_set_item(db_session):
    world = World(db_session)
    tension = world.tension()
    world.repo.add(tension)
    item = make_item(source=ITEM_SOURCE_EXOPLANET_ARCHIVE, external_id=world.new.solution_key)
    SqlAlchemyItemRepository(db_session).add_many([item])
    world.repo.set_item(tension.id, item.id)
    db_session.expire_all()
    assert world.repo.get(tension.id).item_id == item.id
    with pytest.raises(LookupError):
        world.repo.set_item(uuid4(), item.id)


def test_confirmed_added_since(db_session):
    world = World(db_session)
    found = world.archive.confirmed_added_since(date(2026, 8, 20))
    # Solo Published Confirmed activas con releasedate >= dia; sin la Candidate ni la vieja.
    assert [(s.solution_key, d) for s, d in found] == [
        (world.new.solution_key, True),
        (world.other.solution_key, True),
    ]
    assert len(world.archive.confirmed_added_since(date(2026, 7, 1))) == 3
    assert world.archive.confirmed_added_since(date(2026, 9, 2)) == []


def test_confirmed_added_since_ignora_las_dadas_de_baja(db_session):
    world = World(db_session)
    save(
        world.archive,
        snapshot(offset_days=7),
        [world.new],
        removal_scope=frozenset({PLANET}),
    )
    keys = [s.solution_key for s, _ in world.archive.confirmed_added_since(date(2026, 7, 1))]
    assert world.new.solution_key in keys
    assert world.old.solution_key not in keys


def test_active_solutions_of(db_session):
    world = World(db_session)
    result = world.archive.active_solutions_of([PLANET, "no existe"])
    assert set(result) == {PLANET}
    assert sorted(s.solution_key for s, _ in result[PLANET]) == sorted(
        [world.old.solution_key, world.new.solution_key, world.msini.solution_key]
    )
    assert dict((s.solution_key, d) for s, d in result[PLANET])[world.new.solution_key] is True
    assert world.archive.active_solutions_of([]) == {}


def _solution_tension_finding(world: World, tension: SolutionTension, item, run):
    data = SolutionTensionData(
        planet_name=PLANET,
        parameter=MASS,
        archive_url="https://exoplanetarchive.ipac.caltech.edu/overview/HD%201%20b",
        threshold_sigma=3.0,
        sigma=8.2,
        new=tension.new,
        reference=tension.reference,
        others=tension.others,
        labels=tension.labels,
        reference_role=tension.reference_role,
    )
    return make_finding(
        item.id,
        run.id,
        type=FindingType.SOLUTION_TENSION,
        solution_tension=data,
        solution_tension_id=tension.id,
    )


def test_finding_solution_tension_roundtrip_y_unicidad(db_session):
    world = World(db_session)
    tension = world.tension()
    world.repo.add(tension)
    item = make_item(source=ITEM_SOURCE_EXOPLANET_ARCHIVE, external_id=world.new.solution_key)
    SqlAlchemyItemRepository(db_session).add_many([item])
    run = make_run()
    SqlAlchemyRunRepository(db_session).add(run)
    db_session.flush()
    findings = SqlAlchemyFindingRepository(db_session)

    finding = _solution_tension_finding(world, tension, item, run)
    findings.add(finding)
    db_session.expire_all()
    [back] = findings.unpublished_for_run(run.id)
    assert back.solution_tension == finding.solution_tension
    assert back.solution_tension_id == tension.id
    assert back.type == FindingType.SOLUTION_TENSION

    with pytest.raises(IntegrityError, match="uq_findings_solution_tension_id_type"):
        findings.add(_solution_tension_finding(world, tension, item, run))
        db_session.flush()


def test_next_unread_ignora_source_distinta_de_arxiv(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    arxiv = make_item(external_id="2601.00001")
    archive = make_item(source=ITEM_SOURCE_EXOPLANET_ARCHIVE, external_id="k" * 64)
    repo.add_many([archive, arxiv])
    assert [i.id for i in repo.next_unread(10)] == [arxiv.id]
    # Con limite 1 el del archivo tampoco ocupa el cupo, aunque llegue antes.
    assert [i.id for i in repo.next_unread(1)] == [arxiv.id]
