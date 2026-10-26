"""T99: `RecordTensionEvaluations` ante planetas cuya resolución falla.

Catálogo falso (`FakeExoplanetCatalog.failing` lanza `PlanetResolutionFailed`,
que es lo que el catálogo real produce tras T99 cuando el alias no responde).
Sin base de datos, sin red, sin Claude.
"""

import json
import logging
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
    make_own_solution_rule,
    make_period_rule,
    make_reading,
    make_solution,
)

from nocturna.application.use_cases import record_tension_evaluations as record_module
from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.application.use_cases.record_tension_evaluations import RecordTensionEvaluations
from nocturna.domain.entities import MeasuredParameter, MeasurementUnit
from nocturna.domain.tension import EvaluationStatus
from nocturna.infrastructure.logging import configure_json_logging

pytestmark = pytest.mark.anyio

A = "TOI-210 b"
B = "V1298 Tau b"
NOW = datetime(2026, 10, 26, 3, 0, tzinfo=UTC)
RESOLUTION_FAILED = "tension.resolution_failed"
SUMMARY = "tension.evaluation_summary"


@pytest.fixture(autouse=True)
def _reactivar_logger(monkeypatch):
    """`alembic/env.py::fileConfig` (cargado por tests/db en la suite completa)
    deshabilita los loggers `nocturna.*` ya creados (deuda en TECHNICAL_DEBT.md);
    sin esto los tests de logging pasan solos y fallan en la suite completa."""
    monkeypatch.setattr(record_module._logger, "disabled", False)


def _compute(catalog) -> ComputeTensions:
    return ComputeTensions(
        catalog,
        threshold_sigma=3.0,
        period_rule=make_period_rule(),
        own_solution_rule=make_own_solution_rule(),
        clock=FakeClock(NOW),
    )


def _catalog(*, failing=(), a_known: bool = False, a_solutions=None) -> FakeExoplanetCatalog:
    aliases = {B: B}
    solutions = {(B, MASS): [make_solution(0.041, 0.017, 0.017, is_default=True)]}
    if a_known:
        aliases[A] = A
        solutions[(A, MASS)] = a_solutions or []
    return FakeExoplanetCatalog(aliases=aliases, solutions=solutions, failing=set(failing))


def _world(*, measurements=None):
    item = make_item("2610.00001")
    reading = make_reading(
        item.id,
        measurements
        or (
            make_measurement(0.52, 0.12, 0.14, planet_name=A),
            make_measurement(0.04, 0.01, 0.01, planet_name=B),
        ),
    )
    readings = InMemoryReadingRepository()
    readings.add(reading)
    return item, reading, InMemoryItemRepository(item), readings


def _use_case(items, readings, evaluations, catalog) -> RecordTensionEvaluations:
    return RecordTensionEvaluations(
        readings=readings, items=items, evaluations=evaluations, compute=_compute(catalog)
    )


async def test_el_planeta_que_falla_no_crea_fila_y_el_resto_se_guarda():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()

    report = await _use_case(items, readings, evaluations, _catalog(failing={A}))(dry_run=False)

    assert report.created == 1
    assert [e.planet_name for e in evaluations.all()] == [B]
    (failure,) = report.failures
    assert (failure.planet_name, failure.parameter) == (A, MASS)
    assert A in failure.reason


async def test_awaiting_previa_del_planeta_que_falla_conserva_su_fila_y_se_recalcula_despues():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()
    # Pasada 1: A resoluble pero sin soluciones -> AWAITING_REFERENCE.
    await _use_case(items, readings, evaluations, _catalog(a_known=True))(dry_run=False)
    (before,) = [e for e in evaluations.all() if e.planet_name == A]
    assert before.status == EvaluationStatus.AWAITING_REFERENCE

    # Pasada 2: A falla; su fila no se toca.
    failed = await _use_case(items, readings, evaluations, _catalog(failing={A}, a_known=True))(
        dry_run=False
    )

    (kept,) = [e for e in evaluations.all() if e.planet_name == A]
    assert kept == before and kept.evaluated_at == before.evaluated_at
    assert [f.planet_name for f in failed.failures] == [A]
    assert failed.reevaluated == 0

    # Pasada 3: el alias vuelve y llega la referencia -> se reevalúa conservando el id.
    arrived = _catalog(a_known=True, a_solutions=[make_solution(0.5, 0.1, 0.1, planet_name=A)])
    again = await _use_case(items, readings, evaluations, arrived)(dry_run=False)

    assert again.failures == () and again.reevaluated == 1
    (final,) = [e for e in evaluations.all() if e.planet_name == A]
    assert final.id == before.id and final.status != EvaluationStatus.AWAITING_REFERENCE


async def test_una_lectura_con_grupo_fallido_vuelve_a_entrar_la_noche_siguiente():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()
    first = await _use_case(items, readings, evaluations, _catalog(failing={A}))(dry_run=False)
    assert [e.planet_name for e in evaluations.all()] == [B]

    second_catalog = _catalog(a_known=True)
    second = await _use_case(items, readings, evaluations, second_catalog)(dry_run=False)

    assert len(first.failures) == 1 and second.failures == ()
    assert second.created == 1
    assert {e.planet_name for e in evaluations.all()} == {A, B}
    assert A in second_catalog.resolve_calls


async def test_dry_run_con_fallo_informa_y_no_escribe():
    _, _, items, readings = _world()
    evaluations = InMemoryTensionEvaluationRepository()

    report = await _use_case(items, readings, evaluations, _catalog(failing={A}))(dry_run=True)

    assert report.created == 1 and len(report.failures) == 1
    assert evaluations.all() == []


async def test_un_warning_por_planeta_distinto_con_planeta_parametros_y_motivo(caplog):
    item = make_item("2610.00002")
    measurements = (
        make_measurement(0.52, 0.12, 0.14, planet_name=A),
        make_measurement(
            1.1,
            0.1,
            0.1,
            planet_name=A,
            parameter=MeasuredParameter.RADIUS,
            unit=MeasurementUnit.R_JUP,
        ),
        make_measurement(0.04, 0.01, 0.01, planet_name=B),
    )
    readings = InMemoryReadingRepository()
    readings.add(make_reading(item.id, measurements))
    # Segunda lectura con el mismo planeta fallido: sigue siendo un solo evento.
    other = make_item("2610.00003")
    readings.add(make_reading(other.id, (make_measurement(0.6, 0.1, 0.1, planet_name=A),)))
    use_case = _use_case(
        InMemoryItemRepository(item, other),
        readings,
        InMemoryTensionEvaluationRepository(),
        _catalog(failing={A}),
    )

    with caplog.at_level(logging.INFO):
        report = await use_case(dry_run=False)

    assert len(report.failures) == 3  # 2 grupos de la primera lectura + 1 de la segunda
    warnings = [r for r in caplog.records if r.getMessage() == RESOLUTION_FAILED]
    assert len(warnings) == 1
    (record,) = warnings
    assert record.levelno == logging.WARNING
    assert record.__dict__["event"] == RESOLUTION_FAILED
    assert record.__dict__["planet_name"] == A
    assert "mass" in str(record.__dict__["parameters"])
    assert "radius" in str(record.__dict__["parameters"])
    assert A in str(record.__dict__["reason"])


async def test_un_resumen_info_con_los_contadores(caplog):
    _, _, items, readings = _world()
    use_case = _use_case(
        items, readings, InMemoryTensionEvaluationRepository(), _catalog(failing={A})
    )

    with caplog.at_level(logging.INFO):
        await use_case(dry_run=False)

    (summary,) = [r for r in caplog.records if r.getMessage() == SUMMARY]
    assert summary.levelno == logging.INFO
    extra = summary.__dict__
    assert extra["event"] == SUMMARY
    assert extra["evaluations_created"] == 1
    assert extra["reevaluated"] == 0
    assert extra["unchanged"] == 0
    assert extra["kept_terminal"] == 0
    assert extra["failed_groups"] == 1
    assert extra["failed_planets"] == 1


async def test_sin_fallos_no_hay_warning_y_el_resumen_lleva_ceros(caplog):
    _, _, items, readings = _world()
    use_case = _use_case(
        items, readings, InMemoryTensionEvaluationRepository(), _catalog(a_known=True)
    )

    with caplog.at_level(logging.INFO):
        await use_case(dry_run=False)

    assert [r for r in caplog.records if r.getMessage() == RESOLUTION_FAILED] == []
    (summary,) = [r for r in caplog.records if r.getMessage() == SUMMARY]
    assert summary.__dict__["failed_groups"] == 0 and summary.__dict__["failed_planets"] == 0


async def test_con_el_logging_json_real_a_info_no_hay_keyerror_y_salen_los_dos_eventos(capsys):
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    try:
        configure_json_logging(logging.INFO)
        _, _, items, readings = _world()
        use_case = _use_case(
            items, readings, InMemoryTensionEvaluationRepository(), _catalog(failing={A})
        )

        await use_case(dry_run=False)  # un atributo reservado en extra= lanzaría KeyError
    finally:
        root.handlers.clear()
        root.handlers.extend(handlers)
        root.setLevel(level)

    lines = [json.loads(x) for x in capsys.readouterr().err.splitlines() if x.strip()]
    by_event = {p["event"]: p for p in lines}
    assert by_event[RESOLUTION_FAILED]["level"] == "WARNING"
    assert by_event[RESOLUTION_FAILED]["planet_name"] == A
    assert by_event[SUMMARY]["failed_planets"] == 1
