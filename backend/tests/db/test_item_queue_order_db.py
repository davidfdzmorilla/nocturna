"""Orden de la cola (`published_at DESC`), `stale_unread` y `expired` en PostgreSQL (T94)."""

from __future__ import annotations

from datetime import timedelta

from factories import aware, make_item

from nocturna.application.use_cases.expire_unread import expire_stale_unread
from nocturna.domain.entities import ItemStatus
from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository


def _ids(items) -> list[str]:
    return [i.external_id for i in items]


def test_a_fetched_antes_pero_published_antes_va_despues_de_b(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(external_id="2601.00001", fetched_at=aware(10), published_at=aware(0)),
            make_item(external_id="2601.00002", fetched_at=aware(20), published_at=aware(60 * 24)),
        ]
    )
    db_session.flush()

    assert _ids(repo.next_unread(10)) == ["2601.00002", "2601.00001"]


def test_el_marcado_antiguo_va_antes_que_el_no_marcado_reciente(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(external_id="2601.00001", published_at=aware(0), exoplanet_match=True),
            make_item(external_id="2601.00002", published_at=aware(60 * 24 * 30)),
        ]
    )
    db_session.flush()

    assert _ids(repo.next_unread(10)) == ["2601.00001", "2601.00002"]


def test_limit_uno_devuelve_el_mas_reciente(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(external_id="2601.00001", published_at=aware(0)),
            make_item(external_id="2601.00002", published_at=aware(100)),
            make_item(external_id="2601.00003", published_at=aware(50)),
        ]
    )
    db_session.flush()

    assert _ids(repo.next_unread(1)) == ["2601.00002"]


def test_published_at_igual_desempata_por_external_id(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [make_item(external_id=f"2601.0000{i}", published_at=aware(5)) for i in (3, 1, 2)]
    )
    db_session.flush()

    assert _ids(repo.next_unread(10)) == ["2601.00001", "2601.00002", "2601.00003"]


def test_next_unread_no_devuelve_expired(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(external_id="2601.00001", status=ItemStatus.EXPIRED),
            make_item(external_id="2601.00002"),
        ]
    )
    db_session.flush()

    assert _ids(repo.next_unread(10)) == ["2601.00002"]


# --- stale_unread ----------------------------------------------------------


def test_stale_unread_filtra_por_estado_fuente_marca_y_fecha(db_session):
    cutoff = aware(60 * 24 * 10)
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(external_id="2601.00001", published_at=cutoff - timedelta(seconds=1)),
            make_item(external_id="2601.00002", published_at=cutoff),  # límite exacto
            make_item(external_id="2601.00003", published_at=aware(0), exoplanet_match=True),
            make_item(external_id="2601.00004", published_at=aware(0), status=ItemStatus.READ),
            make_item(external_id="2601.00005", published_at=aware(0), status=ItemStatus.FAILED),
            make_item(external_id="solucion-1", source="exoplanet_archive", published_at=aware(0)),
        ]
    )
    db_session.flush()

    assert _ids(repo.stale_unread(cutoff)) == ["2601.00001"]


def test_save_persiste_expired_y_next_unread_deja_de_verlo(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    item = make_item(external_id="2601.00001")
    repo.add_many([item])
    db_session.flush()

    item.expire()
    repo.save(item)
    db_session.flush()
    db_session.expire_all()

    assert repo.get(item.id).status is ItemStatus.EXPIRED
    assert repo.next_unread(10) == []


def test_reingestar_un_expired_lo_deja_expired(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    item = make_item(external_id="2601.00001")
    repo.add_many([item])
    item.expire()
    repo.save(item)
    db_session.flush()

    inserted = repo.add_many([make_item(external_id="2601.00001")])
    db_session.flush()
    db_session.expire_all()

    assert inserted == 0
    assert repo.get(item.id).status is ItemStatus.EXPIRED
    assert repo.next_unread(10) == []


def test_expire_stale_unread_persiste_en_la_base_solo_los_viejos_no_marcados(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    now = aware(60 * 24 * 30)
    old = make_item(external_id="2601.00001", published_at=aware(0))
    recent = make_item(external_id="2601.00002", published_at=aware(60 * 24 * 29))
    old_exo = make_item(external_id="2601.00003", published_at=aware(0), exoplanet_match=True)
    repo.add_many([old, recent, old_exo])
    db_session.flush()

    result = expire_stale_unread(repo, now=now, max_age_days=7)
    db_session.flush()
    db_session.expire_all()

    assert result.expired == 1
    assert repo.get(old.id).status is ItemStatus.EXPIRED
    assert repo.get(recent.id).status is ItemStatus.NEW
    assert repo.get(old_exo.id).status is ItemStatus.NEW
