"""Tests de `SqlAlchemyReadingRepository` contra PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

import pytest
import sqlalchemy as sa
from factories import make_item, make_reading
from sqlalchemy.exc import IntegrityError

from nocturna.domain.errors import InvariantViolation
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


def _item_with_reading(db_session, **reading_overrides):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    item = make_item()
    items.add_many([item])
    db_session.flush()
    previous = make_reading(item_id=item.id, **reading_overrides)
    readings.add(previous)
    db_session.flush()
    return readings, item, previous


def test_supersede_marca_la_previa_y_la_nueva_es_la_vigente(db_session):
    readings, item, previous = _item_with_reading(db_session)
    new = make_reading(item_id=item.id, prompt_version="reader-v3", measurements=())

    readings.supersede(previous.id, new)

    current = readings.get_for_item(item.id)
    assert current is not None
    assert current.id == new.id
    assert current.prompt_version == "reader-v3"
    rows = db_session.execute(
        sa.text("SELECT id, superseded_at FROM readings WHERE item_id = :i"), {"i": item.id}
    ).all()
    by_id = {r.id: r.superseded_at for r in rows}
    assert by_id[previous.id] is not None
    assert by_id[new.id] is None


def test_with_measurements_excluye_las_sustituidas(db_session):
    readings, item, previous = _item_with_reading(db_session, measurements=())
    new = make_reading(item_id=item.id, measurements=())

    readings.supersede(previous.id, new)

    assert [r.id for r in readings.with_measurements()] == [new.id]


def test_supersede_sobre_previa_ya_sustituida_lanza_y_no_anade_nada(db_session):
    readings, item, previous = _item_with_reading(db_session)
    readings.supersede(previous.id, make_reading(item_id=item.id))
    intento = make_reading(item_id=item.id)

    with pytest.raises(InvariantViolation):
        readings.supersede(previous.id, intento)

    db_session.flush()
    count = db_session.execute(
        sa.text("SELECT count(*) FROM readings WHERE id = :i"), {"i": intento.id}
    ).scalar_one()
    assert count == 0


def test_supersede_con_previa_de_otro_item_lanza(db_session):
    readings, _item, previous = _item_with_reading(db_session)
    otro = make_item(external_id="2601.99999")
    SqlAlchemyItemRepository(db_session).add_many([otro])
    db_session.flush()

    with pytest.raises(InvariantViolation):
        readings.supersede(previous.id, make_reading(item_id=otro.id))


def test_supersede_con_previa_inexistente_lanza(db_session):
    readings, item, _previous = _item_with_reading(db_session)

    with pytest.raises(InvariantViolation):
        readings.supersede(uuid4(), make_reading(item_id=item.id))


def test_el_indice_parcial_rechaza_dos_vigentes_del_mismo_item(db_session):
    readings, item, _previous = _item_with_reading(db_session)

    readings.add(make_reading(item_id=item.id))
    with pytest.raises(IntegrityError, match="uq_readings_item_id_current"):
        db_session.flush()


def test_supersede_ejecuta_el_update_antes_del_insert(db_session, test_engine):
    readings, item, previous = _item_with_reading(db_session)
    statements: list[str] = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement.split()[0].upper())

    sa.event.listen(test_engine, "before_cursor_execute", record)
    try:
        readings.supersede(previous.id, make_reading(item_id=item.id))
    finally:
        sa.event.remove(test_engine, "before_cursor_execute", record)

    assert statements.index("UPDATE") < statements.index("INSERT")


def test_prompt_version_ida_y_vuelta(db_session):
    readings, item, _previous = _item_with_reading(db_session, prompt_version="reader-v2")

    current = readings.get_for_item(item.id)
    assert current is not None
    assert current.prompt_version == "reader-v2"
