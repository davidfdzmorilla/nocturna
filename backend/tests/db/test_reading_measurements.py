"""Tests de `ReadingRow.measurements` (T71.c) contra PostgreSQL real.

Tres cosas que un test puro de mappers (ver `tests/test_mappers.py`, si
existe) no puede comprobar porque no toca SQL de verdad:

1. `Reading.measurements = None` se guarda como `measurements IS NULL` en la
   columna, no como el literal JSON `null` dentro de una fila no-NULL. Es
   justo lo que `JSONB(none_as_null=True)` en `models.py` existe para
   garantizar; sin ese flag, este test fallaría.
2. `Reading.measurements = ()` se guarda como un array JSON vacío (`[]`),
   fila NOT NULL, distinguible de (1) por `jsonb_array_length`.
3. Un roundtrip completo -- por el repositorio, pasando por PostgreSQL de
   verdad -- conserva floats exactos y los cuatro enums de `Measurement`
   (`MeasuredParameter`, `MeasurementUnit`, `MeasurementLimit`,
   `MeasurementOrigin`), incluida una medida sin errores informados
   (`err_plus`/`err_minus = None`).
"""

from __future__ import annotations

import sqlalchemy as sa
from factories import make_item, make_reading

from nocturna.domain.entities import (
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
)
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
)


def test_measurements_none_se_guarda_como_sql_null(db_session):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    item = make_item()
    items.add_many([item])
    db_session.flush()
    reading = make_reading(item_id=item.id, measurements=None)

    readings.add(reading)
    db_session.flush()

    is_null = db_session.execute(
        sa.text("SELECT measurements IS NULL FROM readings WHERE id = :id"),
        {"id": str(reading.id)},
    ).scalar_one()
    assert is_null is True


def test_measurements_tupla_vacia_se_guarda_como_array_json_vacio_no_null(db_session):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    item = make_item()
    items.add_many([item])
    db_session.flush()
    reading = make_reading(item_id=item.id, measurements=())

    readings.add(reading)
    db_session.flush()

    row = db_session.execute(
        sa.text(
            "SELECT measurements IS NULL AS is_null, "
            "jsonb_array_length(measurements) AS array_length "
            "FROM readings WHERE id = :id"
        ),
        {"id": str(reading.id)},
    ).one()
    assert row.is_null is False
    assert row.array_length == 0


def test_measurements_roundtrip_conserva_floats_exactos_y_los_cuatro_enums(db_session):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    item = make_item()
    items.add_many([item])
    db_session.flush()

    # Cubre los tres `MeasuredParameter`, tres de las cinco `MeasurementUnit`
    # (una por parámetro usado), los tres `MeasurementLimit` y los dos
    # `MeasurementOrigin` entre las tres medidas, más dos sin errores
    # informados (`err_plus`/`err_minus = None`, el caso de una cota).
    measurement_with_errors = Measurement(
        planet_name="Kepler-1 b",
        parameter=MeasuredParameter.MASS,
        value=1.3,
        err_plus=0.67247479,
        err_minus=0.00000028,
        unit=MeasurementUnit.M_JUP,
        limit=MeasurementLimit.NONE,
        origin=MeasurementOrigin.THIS_WORK,
        evidence="a mass of 1.3 M_jup",
    )
    measurement_upper_limit_no_errors = Measurement(
        planet_name="Kepler-1 c",
        parameter=MeasuredParameter.RADIUS,
        value=2.5,
        err_plus=None,
        err_minus=None,
        unit=MeasurementUnit.R_EARTH,
        limit=MeasurementLimit.UPPER,
        origin=MeasurementOrigin.LITERATURE,
        evidence="a radius upper limit of 2.5 R_earth",
    )
    measurement_lower_limit_this_work = Measurement(
        planet_name="Kepler-1 d",
        parameter=MeasuredParameter.PERIOD,
        value=10.0,
        err_plus=None,
        err_minus=None,
        unit=MeasurementUnit.DAY,
        limit=MeasurementLimit.LOWER,
        origin=MeasurementOrigin.THIS_WORK,
        evidence="a period lower limit of 10 days",
    )
    measurements = (
        measurement_with_errors,
        measurement_upper_limit_no_errors,
        measurement_lower_limit_this_work,
    )
    reading = make_reading(item_id=item.id, measurements=measurements)

    readings.add(reading)
    db_session.flush()
    db_session.expunge_all()
    rehydrated = readings.get_for_item(item.id)

    assert rehydrated is not None
    assert rehydrated.measurements == measurements
    # Igualdad de `float` es exacta a propósito, no `pytest.approx`: el
    # punto del test es que el serializador JSON de PostgreSQL/psycopg no
    # pierde precisión en el viaje de ida y vuelta.
    assert rehydrated.measurements[0].err_plus == 0.67247479
    assert rehydrated.measurements[0].err_minus == 0.00000028
    assert rehydrated.measurements[1].err_plus is None
    assert rehydrated.measurements[1].err_minus is None
    assert isinstance(rehydrated.measurements, tuple)
    for measurement in rehydrated.measurements:
        assert isinstance(measurement, Measurement)
