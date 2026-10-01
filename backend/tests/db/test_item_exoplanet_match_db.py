"""`items.exoplanet_match` contra PostgreSQL (T79): orden de `next_unread`,
persistencia de la marca en `add_many`/mapper y no sobrescritura en duplicados."""

from __future__ import annotations

from datetime import timedelta

from factories import aware, make_item

from nocturna.domain.entities import ItemStatus
from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository


def _external_ids(items) -> list[str]:
    return [i.external_id for i in items]


def test_next_unread_pone_primero_los_marcados_luego_fetched_at_luego_external_id(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(external_id="2601.00001", fetched_at=aware(1)),
            make_item(external_id="2601.00002", fetched_at=aware(2)),
            make_item(external_id="2601.00009", fetched_at=aware(5), exoplanet_match=True),
            make_item(external_id="2601.00008", fetched_at=aware(3), exoplanet_match=True),
            # mismo fetched_at que el anterior: desempata external_id
            make_item(external_id="2601.00007", fetched_at=aware(3), exoplanet_match=True),
        ]
    )
    db_session.flush()

    result = repo.next_unread(10)

    assert _external_ids(result) == [
        "2601.00007",
        "2601.00008",
        "2601.00009",
        "2601.00001",
        "2601.00002",
    ]


def test_next_unread_aplica_el_limit_despues_de_priorizar(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [make_item(external_id=f"2601.0000{i}", fetched_at=aware(i)) for i in range(1, 5)]
        + [make_item(external_id="2601.00099", fetched_at=aware(60), exoplanet_match=True)]
    )
    db_session.flush()

    result = repo.next_unread(2)

    # El marcado es el más reciente de todos: sin priorizar antes del LIMIT no entraría.
    assert _external_ids(result) == ["2601.00099", "2601.00001"]


def test_next_unread_excluye_los_que_no_son_new_aunque_esten_marcados(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(external_id="2601.00001", exoplanet_match=True, status=ItemStatus.READ),
            make_item(external_id="2601.00002", exoplanet_match=True, status=ItemStatus.FAILED),
            make_item(external_id="2601.00003", exoplanet_match=True, status=ItemStatus.DISCARDED),
            make_item(external_id="2601.00004", exoplanet_match=False),
        ]
    )
    db_session.flush()

    assert _external_ids(repo.next_unread(10)) == ["2601.00004"]


def test_add_many_y_el_mapper_conservan_la_marca(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    marked = make_item(external_id="2601.00001", exoplanet_match=True)
    plain = make_item(external_id="2601.00002")
    repo.add_many([marked, plain])
    db_session.flush()
    db_session.expire_all()

    assert repo.get(marked.id).exoplanet_match is True
    assert repo.get(plain.id).exoplanet_match is False


def test_la_marca_por_defecto_de_un_item_es_false(db_session):
    assert make_item().exoplanet_match is False


def test_un_duplicado_en_add_many_no_pisa_la_marca_existente(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    original = make_item(external_id="2601.00001", exoplanet_match=True)
    repo.add_many([original])
    db_session.flush()

    inserted = repo.add_many([make_item(external_id="2601.00001", exoplanet_match=False)])
    db_session.flush()
    db_session.expire_all()

    assert inserted == 0
    assert repo.get(original.id).exoplanet_match is True


def test_un_duplicado_marcado_tampoco_cambia_un_item_no_marcado(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    original = make_item(external_id="2601.00001", exoplanet_match=False)
    repo.add_many([original])
    db_session.flush()

    repo.add_many([make_item(external_id="2601.00001", exoplanet_match=True)])
    db_session.flush()
    db_session.expire_all()

    assert repo.get(original.id).exoplanet_match is False


def test_save_no_toca_la_marca(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    item = make_item(external_id="2601.00001", exoplanet_match=True)
    repo.add_many([item])
    db_session.flush()

    item.mark_read()
    repo.save(item)
    db_session.flush()
    db_session.expire_all()

    reloaded = repo.get(item.id)
    assert reloaded.status is ItemStatus.READ
    assert reloaded.exoplanet_match is True


def test_orden_estable_con_fetched_at_identico_y_sin_marcas(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    same = aware(1)
    repo.add_many(
        [make_item(external_id=f"2601.0000{i}", fetched_at=same + timedelta(0)) for i in (3, 1, 2)]
    )
    db_session.flush()

    assert _external_ids(repo.next_unread(10)) == ["2601.00001", "2601.00002", "2601.00003"]
