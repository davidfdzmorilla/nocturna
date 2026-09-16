"""Tests de `SqlAlchemyItemRepository` contra PostgreSQL."""

from __future__ import annotations

from uuid import uuid4

from factories import make_item
from sqlalchemy import event

from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository


def test_add_many_con_lista_vacia_devuelve_cero_sin_tocar_la_base(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    executed_statements: list[str] = []

    def _capture_statement(conn, cursor, statement, parameters, context, executemany):
        executed_statements.append(statement)

    connection = db_session.connection()
    event.listen(connection, "before_cursor_execute", _capture_statement)
    try:
        inserted = repo.add_many([])
    finally:
        event.remove(connection, "before_cursor_execute", _capture_statement)

    assert inserted == 0
    assert executed_statements == []


def test_add_many_con_tres_nuevos_devuelve_tres(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    items = [make_item(external_id=f"2601.{i:05d}") for i in range(3)]

    inserted = repo.add_many(items)

    assert inserted == 3


def test_add_many_repitiendo_la_lista_devuelve_cero_sin_duplicar(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    items = [make_item(external_id=f"2601.{i:05d}") for i in range(3)]
    repo.add_many(items)
    db_session.flush()

    inserted_again = repo.add_many(items)

    assert inserted_again == 0


def test_add_many_con_duplicados_dentro_de_la_misma_lista_inserta_uno_solo(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    same_key = [
        make_item(external_id="2601.00099", title="primera aparición"),
        make_item(external_id="2601.00099", title="segunda aparición"),
    ]

    inserted = repo.add_many(same_key)
    db_session.flush()

    assert inserted == 1
    stored = repo.next_unread(limit=10)
    assert len([item for item in stored if item.external_id == "2601.00099"]) == 1


def test_add_many_mezcla_de_nuevos_y_existentes_devuelve_solo_los_nuevos(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    existing = make_item(external_id="2601.00001")
    repo.add_many([existing])
    db_session.flush()

    new_one = make_item(external_id="2601.00002")
    inserted = repo.add_many([existing, new_one])

    assert inserted == 1


def test_get_de_id_inexistente_devuelve_none(db_session):
    repo = SqlAlchemyItemRepository(db_session)

    assert repo.get(uuid4()) is None


def test_next_unread_devuelve_solo_los_new_en_orden_de_llegada_respetando_el_limite(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    new_items = [make_item(external_id=f"2601.{i:05d}") for i in range(4)]
    read_item = make_item(external_id="2601.09999")
    read_item.mark_read()
    repo.add_many([*new_items, read_item])
    db_session.flush()

    result = repo.next_unread(limit=2)

    assert len(result) == 2
    assert all(item.external_id.startswith("2601.0000") for item in result)
    returned_ids = [item.external_id for item in result]
    expected_order = [item.external_id for item in new_items][:2]
    assert returned_ids == expected_order


def test_save_tras_mark_read_persiste(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    item = make_item()
    repo.add_many([item])
    db_session.flush()

    item.mark_read()
    repo.save(item)
    db_session.flush()

    rehydrated = repo.get(item.id)
    assert rehydrated.status.value == "read"


def test_save_de_entidad_nunca_insertada_lanza_lookup_error(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    item = make_item()

    try:
        repo.save(item)
    except LookupError:
        pass
    else:
        raise AssertionError("se esperaba LookupError")
