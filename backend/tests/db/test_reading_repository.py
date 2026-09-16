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
