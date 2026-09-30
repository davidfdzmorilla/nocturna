"""Tests de `SqlAlchemyReadingRepository` contra PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

from factories import make_item, make_reading

from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
)


def test_get_for_item_sin_lectura_devuelve_none(db_session):
    repo = SqlAlchemyReadingRepository(db_session)

    assert repo.get_for_item(uuid4()) is None


def test_add_y_get_for_item_devuelve_la_lectura_guardada(db_session):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    item = make_item()
    items.add_many([item])
    db_session.flush()
    reading = make_reading(item_id=item.id, interest_score=5)

    readings.add(reading)
    db_session.flush()

    rehydrated = readings.get_for_item(item.id)
    assert rehydrated is not None
    assert rehydrated.id == reading.id
    assert rehydrated.interest_score == 5


def test_get_for_item_de_otro_item_devuelve_none(db_session):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    item_with_reading = make_item(external_id="2601.00001")
    item_without_reading = make_item(external_id="2601.00002")
    items.add_many([item_with_reading, item_without_reading])
    db_session.flush()
    readings.add(make_reading(item_id=item_with_reading.id))
    db_session.flush()

    assert readings.get_for_item(item_without_reading.id) is None


def test_with_measurements_incluye_vacia_y_con_medidas_y_excluye_null(db_session):
    from nocturna.domain.entities import (
        MeasuredParameter,
        Measurement,
        MeasurementLimit,
        MeasurementOrigin,
        MeasurementUnit,
    )

    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    item_null = make_item(external_id="2601.10001")
    item_empty = make_item(external_id="2601.10002")
    item_full = make_item(external_id="2601.10003")
    items.add_many([item_null, item_empty, item_full])
    db_session.flush()
    measurement = Measurement(
        planet_name="Kepler-1 b",
        parameter=MeasuredParameter.MASS,
        value=1.3,
        err_plus=None,
        err_minus=None,
        unit=MeasurementUnit.M_JUP,
        limit=MeasurementLimit.NONE,
        origin=MeasurementOrigin.THIS_WORK,
        evidence="a mass of 1.3 M_jup",
    )
    r_null = make_reading(item_id=item_null.id, measurements=None)
    r_empty = make_reading(item_id=item_empty.id, measurements=())
    r_full = make_reading(item_id=item_full.id, measurements=(measurement,))
    for r in (r_null, r_empty, r_full):
        readings.add(r)
    db_session.flush()

    result = readings.with_measurements()

    assert [r.id for r in result] == sorted([r_empty.id, r_full.id])
    by_id = {r.id: r for r in result}
    assert by_id[r_empty.id].measurements == ()
    assert by_id[r_full.id].measurements == (measurement,)
