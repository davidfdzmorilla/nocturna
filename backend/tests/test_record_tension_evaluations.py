"""`RecordTensionEvaluations` (T88) con repositorios y catálogo en memoria."""

from datetime import UTC, datetime

import pytest
from fakes.catalog import FakeExoplanetCatalog
from fakes.clock import FakeClock
from fakes.tension_evaluations import InMemoryTensionEvaluationRepository
from fakes.work import InMemoryItemRepository, InMemoryReadingRepository
from helpers.exoplanet import (
    MASS,
    make_item,
    make_measurement,
    make_period_rule,
    make_reading,
    make_solution,
)

from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.application.use_cases.record_tension_evaluations import RecordTensionEvaluations
from nocturna.domain.tension import EvaluationStatus

pytestmark = pytest.mark.anyio

V1298 = "V1298 Tau b"
NOW = datetime(2026, 10, 2, 3, 0, tzinfo=UTC)


def _compute(catalog) -> ComputeTensions:
    return ComputeTensions(
        catalog, threshold_sigma=3.0, period_rule=make_period_rule(), clock=FakeClock(NOW)
    )


def _catalog(*, known: bool, solutions=None) -> FakeExoplanetCatalog:
    return FakeExoplanetCatalog(
        aliases={V1298: V1298} if known else {},
        solutions={(V1298, MASS): solutions or []},
    )


def _world():
    item = make_item("2601.00001")
    reading = make_reading(item.id, (make_measurement(0.52, 0.12, 0.14),))
    readings = InMemoryReadingRepository()
    readings.add(reading)
    # Una lectura sin extracción de medidas (`None`) no se considera.
    other = make_item("2601.00002")
    readings.add(make_reading(other.id, None))
    return item, reading, InMemoryItemRepository(item, other), readings


def _use_case(items, readings, evaluations, catalog) -> RecordTensionEvaluations:
    return RecordTensionEvaluations(
        readings=readings, items=items, evaluations=evaluations, compute=_compute(catalog)
    )


async def test_crea_las_que_faltan_y_cuenta_por_estado():
    _, reading, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()

    report = await _use_case(items, readings, evaluations, _catalog(known=False))(dry_run=False)

    assert (report.created, report.reevaluated, report.unchanged, report.kept_terminal) == (
        1,
        0,
        0,
        0,
    )
    assert report.by_status == {EvaluationStatus.AWAITING_REFERENCE: 1}
    (stored,) = evaluations.all()
    assert stored.reading_id == reading.id and stored.archive_planet_name is None


async def test_dry_run_calcula_y_cuenta_pero_no_escribe():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()

    report = await _use_case(items, readings, evaluations, _catalog(known=False))(dry_run=True)

    assert report.created == 1
    assert evaluations.all() == []


async def test_una_evaluacion_que_espera_sin_novedades_queda_sin_cambios():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()
    await _use_case(items, readings, evaluations, _catalog(known=False))(dry_run=False)
    before = evaluations.all()[0]

    report = await _use_case(items, readings, evaluations, _catalog(known=False))(dry_run=False)

    assert (report.created, report.reevaluated, report.unchanged) == (0, 0, 1)
    assert evaluations.all() == [before]


async def test_reevalua_cuando_llega_la_referencia_y_conserva_el_id():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()
    await _use_case(items, readings, evaluations, _catalog(known=False))(dry_run=False)
    original_id = evaluations.all()[0].id
    arrived = _catalog(known=True, solutions=[make_solution(0.041, 0.017, 0.017, is_default=True)])

    report = await _use_case(items, readings, evaluations, arrived)(dry_run=False)

    assert (report.created, report.reevaluated, report.unchanged) == (0, 1, 0)
    assert report.by_status == {EvaluationStatus.EVALUATED: 1}
    (stored,) = evaluations.all()
    assert stored.id == original_id
    assert stored.status == EvaluationStatus.EVALUATED


async def test_dry_run_de_una_reevaluacion_no_actualiza():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()
    await _use_case(items, readings, evaluations, _catalog(known=False))(dry_run=False)
    arrived = _catalog(known=True, solutions=[make_solution(0.041, 0.017, 0.017, is_default=True)])

    report = await _use_case(items, readings, evaluations, arrived)(dry_run=True)

    assert report.reevaluated == 1
    assert evaluations.all()[0].status == EvaluationStatus.AWAITING_REFERENCE


async def test_las_terminales_no_se_tocan_aunque_cambie_el_catalogo():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()
    first = _catalog(known=True, solutions=[make_solution(0.041, 0.017, 0.017, is_default=True)])
    await _use_case(items, readings, evaluations, first)(dry_run=False)
    before = evaluations.all()[0]
    changed = _catalog(known=True, solutions=[make_solution(0.5, 0.1, 0.1, is_default=True)])

    report = await _use_case(items, readings, evaluations, changed)(dry_run=False)

    assert (report.created, report.reevaluated, report.unchanged, report.kept_terminal) == (
        0,
        0,
        0,
        1,
    )
    assert report.by_status == {EvaluationStatus.EVALUATED: 1}
    assert evaluations.all() == [before]


async def test_sin_lecturas_con_medidas_no_consulta_el_catalogo():
    catalog = _catalog(known=True)
    use_case = _use_case(
        InMemoryItemRepository(),
        InMemoryReadingRepository(),
        InMemoryTensionEvaluationRepository(),
        catalog,
    )

    report = await use_case(dry_run=False)

    assert (report.created, report.reevaluated, report.unchanged, report.kept_terminal) == (
        0,
        0,
        0,
        0,
    )
    assert catalog.total_calls == 0


async def test_las_omisiones_del_calculo_llegan_al_informe():
    item = make_item("2601.00003")
    readings = InMemoryReadingRepository()
    readings.add(make_reading(item.id, (make_measurement(0.5, None, None),)))

    report = await _use_case(
        InMemoryItemRepository(item),
        readings,
        InMemoryTensionEvaluationRepository(),
        _catalog(known=True),
    )(dry_run=False)

    assert report.created == 0 and len(report.skipped) == 1


async def test_lectura_con_todas_terminales_no_consulta_el_catalogo():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()
    solution = make_solution(0.04, 0.01, 0.01, is_default=True)
    first = await _use_case(
        items, readings, evaluations, _catalog(known=True, solutions=[solution])
    )(dry_run=False)
    assert first.by_status == {EvaluationStatus.EVALUATED: 1}

    catalog = _catalog(known=True, solutions=[solution])
    report = await _use_case(items, readings, evaluations, catalog)(dry_run=False)

    assert catalog.total_calls == 0
    assert (report.created, report.reevaluated, report.unchanged, report.kept_terminal) == (
        0,
        0,
        0,
        1,
    )
    assert report.by_status == {EvaluationStatus.EVALUATED: 1}
    assert len(report.evaluations) == 1


async def test_lectura_con_alguna_awaiting_si_consulta_el_catalogo():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()
    await _use_case(items, readings, evaluations, _catalog(known=False))(dry_run=False)

    catalog = _catalog(known=False)
    report = await _use_case(items, readings, evaluations, catalog)(dry_run=False)

    assert catalog.resolve_calls == [V1298]
    assert report.unchanged == 1 and report.kept_terminal == 0
