"""`SqlAlchemyTensionEvaluationRepository` y consultas de T88 sobre el archivo (PostgreSQL)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from factories import make_item, make_reading
from helpers.exoplanet import make_measurement
from sqlalchemy.exc import IntegrityError
from test_archive_repository import save, snapshot, solution

from nocturna.domain.archive import ArchiveParameterValue, catalog_solution_from_archive
from nocturna.domain.entities import MeasuredParameter, MeasurementUnit
from nocturna.domain.tension import (
    EvaluationStatus,
    PeriodCheck,
    TensionEvaluation,
    TensionResult,
    compare,
    compare_with_limit,
)
from nocturna.infrastructure.db.mappers import tension_evaluation_from_row
from nocturna.infrastructure.db.models import TensionEvaluationRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyTensionEvaluationRepository,
)

MASS = MeasuredParameter.MASS
PERIOD = MeasuredParameter.PERIOD
PLANET = "WASP-12 b"
_T = datetime(2026, 10, 2, 1, 0, tzinfo=UTC)


class World:
    """Ítem, lectura y soluciones del archivo ya persistidos."""

    def __init__(self, session) -> None:
        self.session = session
        self.item = make_item()
        SqlAlchemyItemRepository(session).add_many([self.item])
        self.reading = make_reading(self.item.id)
        SqlAlchemyReadingRepository(session).add(self.reading)

        self.default = solution(PLANET, "Smith 2020", mass=1.47, is_default=True)
        self.prior = solution(
            PLANET, "Jones 2022", mass=1.2, is_default=False, pl_pubdate="2022-05"
        )
        self.upper = replace(
            solution(PLANET, "Lim 2023", pl_pubdate="2023-01"),
            mass=ArchiveParameterValue(value=2.0, err1=None, err2=None, lim=1),
        )
        self.period_sol = replace(
            solution(PLANET, "Per 2024", pl_pubdate="2024-01"),
            period=ArchiveParameterValue(value=1.09, err1=0.001, err2=-0.001, lim=0),
        )
        save(
            SqlAlchemyArchiveRepository(session),
            snapshot(),
            [self.default, self.prior, self.upper, self.period_sol],
        )

    def cat(self, sol, parameter=MASS, *, is_default=False):
        found = catalog_solution_from_archive(sol, parameter, is_default=is_default)
        assert found is not None
        return found


def _evaluation(world: World, status: EvaluationStatus, **kw) -> TensionEvaluation:
    base = {
        "reading_id": world.reading.id,
        "item_id": world.item.id,
        "planet_name": "WASP-12b",
        "parameter": MASS,
        "measurements": (make_measurement(5.0, 0.5, 0.5, planet_name="WASP-12b"),),
        "status": status,
        "evaluated_at": _T,
    }
    base.update(kw)
    return TensionEvaluation(**base)


def _all_states(world: World) -> dict[EvaluationStatus, TensionEvaluation]:
    m = make_measurement(5.0, 0.5, 0.5, planet_name="WASP-12b", unit=MeasurementUnit.M_JUP)
    m_earth = lambda v, e: make_measurement(  # noqa: E731
        v, e, e, planet_name="WASP-12b", unit=MeasurementUnit.M_EARTH
    )
    default = world.cat(world.default, is_default=True)
    prior = world.cat(world.prior)
    upper = world.cat(world.upper)
    result = TensionResult(
        item_id=world.item.id,
        planet_name=PLANET,
        parameter=MASS,
        comparisons=(compare(m, default), compare(m, prior)),
        reading_id=world.reading.id,
    )
    assert result.reference() == default
    near = m_earth(2.5, 1.0)
    far = m_earth(10.0, 1.0)
    return {
        EvaluationStatus.AWAITING_REFERENCE: _evaluation(
            world,
            EvaluationStatus.AWAITING_REFERENCE,
            planet_name="WASP-12b-aw",
            measurements=(m,),
        ),
        EvaluationStatus.EVALUATED: _evaluation(
            world,
            EvaluationStatus.EVALUATED,
            measurements=(m,),
            archive_planet_name=PLANET,
            result=result,
        ),
        EvaluationStatus.CONSISTENT_WITH_LIMIT: _evaluation(
            world,
            EvaluationStatus.CONSISTENT_WITH_LIMIT,
            planet_name="WASP-12b-cl",
            measurements=(near,),
            archive_planet_name=PLANET,
            limit=compare_with_limit([near], upper, threshold_sigma=3.0),
        ),
        EvaluationStatus.INCOMPATIBLE_WITH_LIMIT: _evaluation(
            world,
            EvaluationStatus.INCOMPATIBLE_WITH_LIMIT,
            planet_name="WASP-12b-il",
            measurements=(far,),
            archive_planet_name=PLANET,
            limit=compare_with_limit([far], upper, threshold_sigma=3.0),
        ),
        EvaluationStatus.CLOSED_LOOP: _evaluation(
            world,
            EvaluationStatus.CLOSED_LOOP,
            planet_name="WASP-12b-lp",
            measurements=(m,),
            archive_planet_name=PLANET,
            own_solution_key=world.prior.solution_key,
        ),
    }


@pytest.fixture
def world(db_session) -> World:
    return World(db_session)


@pytest.fixture
def repo(db_session) -> SqlAlchemyTensionEvaluationRepository:
    return SqlAlchemyTensionEvaluationRepository(db_session)


def _reload(db_session, repo):
    db_session.expire_all()
    return repo.all()


def test_round_trip_completo_en_los_cinco_estados(world, repo, db_session):
    states = _all_states(world)
    assert set(states) == set(EvaluationStatus)
    for evaluation in states.values():
        repo.add(evaluation)
    db_session.expunge_all()

    stored = {e.status: e for e in repo.all()}
    assert set(stored) == set(EvaluationStatus)
    for status, original in states.items():
        assert stored[status] == original
        assert stored[status].id == original.id
        assert stored[status].evaluated_at == original.evaluated_at
    evaluated = stored[EvaluationStatus.EVALUATED]
    assert evaluated.result is not None
    assert evaluated.result.reference() == world.cat(world.default, is_default=True)


def test_round_trip_de_periodo_con_period_check(world, repo, db_session):
    paper = make_measurement(
        1.5, 0.01, 0.01, planet_name="WASP-12b", parameter=PERIOD, unit=MeasurementUnit.DAY
    )
    ref = world.cat(world.period_sol, PERIOD)
    evaluation = _evaluation(
        world,
        EvaluationStatus.EVALUATED,
        parameter=PERIOD,
        measurements=(paper,),
        archive_planet_name=PLANET,
        result=TensionResult(
            item_id=world.item.id,
            planet_name=PLANET,
            parameter=PERIOD,
            comparisons=(compare(paper, ref),),
            reading_id=world.reading.id,
        ),
        period_check=PeriodCheck(min_difference_met=True, alias_suspected=False),
    )
    repo.add(evaluation)
    db_session.expunge_all()
    assert repo.all() == [evaluation]


def test_result_sin_reading_id_conserva_none(world, repo, db_session):
    states = _all_states(world)
    evaluated = states[EvaluationStatus.EVALUATED]
    assert evaluated.result is not None
    evaluated = replace(evaluated, result=replace(evaluated.result, reading_id=None))
    repo.add(evaluated)
    db_session.expunge_all()
    assert repo.all() == [evaluated]


def test_columnas_proyectadas_solo_si_evaluated(world, repo, db_session):
    states = _all_states(world)
    for evaluation in states.values():
        repo.add(evaluation)
    db_session.expire_all()
    rows = {
        r.status: r for r in db_session.execute(sa.select(TensionEvaluationRow)).scalars().all()
    }
    evaluated = rows[EvaluationStatus.EVALUATED]
    assert evaluated.reference_solution_key == world.default.solution_key
    result = states[EvaluationStatus.EVALUATED].result
    assert result is not None
    assert evaluated.reference_sigma == result.reference_sigma()
    for status, row in rows.items():
        if status != EvaluationStatus.EVALUATED:
            assert row.reference_solution_key is None and row.reference_sigma is None
    assert rows[EvaluationStatus.CLOSED_LOOP].own_solution_key == world.prior.solution_key
    assert rows[EvaluationStatus.EVALUATED].first_evaluated_at == _T


def test_update_reevalua_conserva_id_y_first_evaluated_at(world, repo, db_session):
    states = _all_states(world)
    waiting = states[EvaluationStatus.AWAITING_REFERENCE]
    repo.add(waiting)
    done = waiting.reevaluate_with(
        replace(
            states[EvaluationStatus.EVALUATED],
            planet_name=waiting.planet_name,
            result=None,
            status=EvaluationStatus.AWAITING_REFERENCE,
        )
    )
    # misma clave, mismo estado: solo cambia evaluated_at
    done = replace(done, evaluated_at=_T + timedelta(days=1))
    repo.update(done)
    db_session.expire_all()
    (stored,) = repo.all()
    assert stored.id == waiting.id
    assert stored.evaluated_at == _T + timedelta(days=1)
    row = db_session.get(TensionEvaluationRow, waiting.id)
    assert row is not None and row.first_evaluated_at == _T


def test_update_a_evaluated_actualiza_proyeccion(world, repo, db_session):
    states = _all_states(world)
    m = states[EvaluationStatus.EVALUATED].measurements
    waiting = _evaluation(
        world, EvaluationStatus.AWAITING_REFERENCE, measurements=m, planet_name="WASP-12b"
    )
    repo.add(waiting)
    evaluated = waiting.reevaluate_with(states[EvaluationStatus.EVALUATED])
    repo.update(evaluated)
    db_session.expire_all()
    (stored,) = repo.all()
    assert stored == evaluated and stored.status == EvaluationStatus.EVALUATED
    row = db_session.get(TensionEvaluationRow, waiting.id)
    assert row is not None and row.reference_solution_key == world.default.solution_key


def test_update_de_id_inexistente_falla(world, repo):
    with pytest.raises(LookupError):
        repo.update(_all_states(world)[EvaluationStatus.EVALUATED])


def test_clave_unica_reading_planeta_parametro(world, repo, db_session):
    first = _evaluation(world, EvaluationStatus.AWAITING_REFERENCE)
    repo.add(first)
    with pytest.raises(IntegrityError, match="uq_tension_evaluation_reading_id_planet_name"):
        repo.add(_evaluation(world, EvaluationStatus.AWAITING_REFERENCE))


def test_misma_clave_con_otro_parametro_o_planeta_es_valida(world, repo):
    repo.add(_evaluation(world, EvaluationStatus.AWAITING_REFERENCE))
    repo.add(_evaluation(world, EvaluationStatus.AWAITING_REFERENCE, planet_name="WASP-12c"))
    repo.add(
        _evaluation(
            world,
            EvaluationStatus.AWAITING_REFERENCE,
            parameter=PERIOD,
            measurements=(
                make_measurement(
                    1.0, 0.1, 0.1, parameter=PERIOD, unit=MeasurementUnit.DAY, planet_name="x"
                ),
            ),
        )
    )
    assert len(repo.all()) == 3


def _raw_insert(session, world: World, **overrides) -> None:
    values = {
        "id": "00000000-0000-0000-0000-0000000000a1",
        "reading_id": world.reading.id,
        "item_id": world.item.id,
        "planet_name": "p",
        "parameter": "mass",
        "status": "awaiting_reference",
        "reference_solution_key": None,
        "own_solution_key": None,
        "now": _T,
    }
    values.update(overrides)
    with session.begin_nested():
        session.execute(
            sa.text(
                "INSERT INTO tension_evaluation (id, reading_id, item_id, planet_name, parameter, "
                "status, reference_solution_key, own_solution_key, detail, first_evaluated_at, "
                "evaluated_at) VALUES (:id, :reading_id, :item_id, :planet_name, :parameter, "
                ":status, :reference_solution_key, :own_solution_key, CAST('{}' AS jsonb), "
                ":now, :now)"
            ),
            values,
        )


def test_raw_insert_valido_es_la_base_de_los_checks_y_fks(world, db_session):
    _raw_insert(db_session, world)


@pytest.mark.parametrize("status", ["bogus", "EVALUATED", ""])
def test_check_de_status(world, db_session, status):
    with pytest.raises(IntegrityError, match="ck_tension_evaluation_tension_evaluation_status"):
        _raw_insert(db_session, world, status=status)


@pytest.mark.parametrize("parameter", ["bogus", "MASS", "msini"])
def test_check_de_parameter(world, db_session, parameter):
    with pytest.raises(IntegrityError, match="ck_tension_evaluation_tension_evaluation_parameter"):
        _raw_insert(db_session, world, parameter=parameter)


@pytest.mark.parametrize(
    ("override", "constraint"),
    [
        (
            {"reading_id": "00000000-0000-0000-0000-00000000ffff"},
            "fk_tension_evaluation_reading_id",
        ),
        ({"item_id": "00000000-0000-0000-0000-00000000ffff"}, "fk_tension_evaluation_item_id"),
        ({"reference_solution_key": "f" * 64}, "fk_tension_evaluation_reference_solution_key"),
        ({"own_solution_key": "f" * 64}, "fk_tension_evaluation_own_solution_key"),
    ],
)
def test_claves_foraneas(world, db_session, override, constraint):
    with pytest.raises(IntegrityError, match=constraint):
        _raw_insert(db_session, world, **override)


def test_schema_version_desconocida_falla_al_leer(world, repo, db_session):
    evaluation = _all_states(world)[EvaluationStatus.EVALUATED]
    repo.add(evaluation)
    db_session.execute(
        sa.text("UPDATE tension_evaluation SET detail = jsonb_set(detail, '{schema_version}', '2')")
    )
    db_session.expire_all()
    with pytest.raises(ValueError, match="schema_version"):
        repo.all()


def test_schema_version_ausente_falla_al_leer(world, repo, db_session):
    repo.add(_all_states(world)[EvaluationStatus.AWAITING_REFERENCE])
    db_session.execute(sa.text("UPDATE tension_evaluation SET detail = detail - 'schema_version'"))
    db_session.expire_all()
    row = db_session.execute(sa.select(TensionEvaluationRow)).scalar_one()
    with pytest.raises(ValueError, match="schema_version"):
        tension_evaluation_from_row(row)


def test_all_vacio(repo):
    assert repo.all() == []


# --- ArchiveRepository: planet_names y active_solutions -------------------------------------


def test_planet_names_excluye_planetas_dados_de_baja(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    a = solution("A b", "Smith 2020")
    b = solution("B b", "Smith 2020")
    c = solution("C b", "Smith 2020")
    save(repo, snapshot(), [a, b, c])
    assert repo.planet_names() == {"A b", "B b", "C b"}

    save(repo, snapshot(offset_days=1), [a, b])
    assert repo.planet_names() == {"A b", "B b"}


def test_planet_names_sigue_si_el_planeta_conserva_alguna_solucion_activa(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    s1 = solution("A b", "Smith 2020")
    s2 = solution("A b", "Jones 2022")
    save(repo, snapshot(), [s1, s2])
    save(repo, snapshot(offset_days=1), [s1])
    assert repo.planet_names() == {"A b"}


def test_planet_names_vacio_sin_soluciones(db_session):
    assert SqlAlchemyArchiveRepository(db_session).planet_names() == frozenset()


def test_active_solutions_excluye_removed_y_usa_is_default_current(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    default = solution("A b", "Smith 2020", is_default=True)
    other = solution("A b", "Jones 2022")
    gone = solution("A b", "Old 2019")
    elsewhere = solution("B b", "Smith 2020", is_default=True)
    save(repo, snapshot(), [default, other, gone, elsewhere])
    save(repo, snapshot(offset_days=1), [default, other, elsewhere])

    found = repo.active_solutions("A b")
    by_key = {sol.solution_key: (sol, flag) for sol, flag in found}
    assert set(by_key) == {default.solution_key, other.solution_key}
    assert by_key[default.solution_key] == (default, True)
    assert by_key[other.solution_key] == (other, False)
    assert repo.active_solutions("nada") == []


def test_active_solutions_usa_is_default_current_no_is_default(db_session):
    """Un incremental que ve la solución sin `default_flag` deja `is_default`
    en la fila, pero el default vigente lo marca `is_default_current`."""
    repo = SqlAlchemyArchiveRepository(db_session)
    old = solution("A b", "Smith 2020", is_default=True)
    new = solution("A b", "Jones 2022")
    save(repo, snapshot(), [old, new])
    flipped_old = replace(old, is_default=False)
    flipped_new = replace(new, is_default=True)
    save(repo, snapshot(offset_days=1), [flipped_old, flipped_new])

    flags = {sol.ref_key: flag for sol, flag in repo.active_solutions("A b")}
    assert flags == {"Smith 2020": False, "Jones 2022": True}


def test_active_solutions_una_baja_deja_de_aparecer_pero_reactivada_vuelve(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    a = solution("A b", "Smith 2020")
    b = solution("A b", "Jones 2022")
    save(repo, snapshot(), [a, b])
    save(repo, snapshot(offset_days=1), [a])
    assert [s.ref_key for s, _ in repo.active_solutions("A b")] == ["Smith 2020"]
    save(repo, snapshot(offset_days=2), [a, b])
    assert {s.ref_key for s, _ in repo.active_solutions("A b")} == {"Smith 2020", "Jones 2022"}
