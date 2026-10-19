"""`expire_stale_unread` y la transición `NEW -> EXPIRED` (T94, ADR 0033), con fakes."""

from __future__ import annotations

import copy
from datetime import UTC, datetime, timedelta

import pytest
from fakes.work import InMemoryItemRepository
from helpers.run_night import make_item

from nocturna.application.use_cases.expire_unread import ExpiryResult, expire_stale_unread
from nocturna.domain.entities import ItemStatus
from nocturna.domain.errors import InvalidTransition

NOW = datetime(2026, 10, 17, 2, 0, tzinfo=UTC)


def test_caduca_el_new_anterior_al_limite_y_devuelve_el_resultado():
    old = make_item(published_at=NOW - timedelta(days=7, seconds=1))
    repo = InMemoryItemRepository(old)

    result = expire_stale_unread(repo, now=NOW, max_age_days=7)

    assert result == ExpiryResult(cutoff=NOW - timedelta(days=7), expired=1)
    assert old.status is ItemStatus.EXPIRED
    assert repo.get(old.id).status is ItemStatus.EXPIRED


def test_el_limite_exacto_no_caduca():
    edge = make_item(published_at=NOW - timedelta(days=7))
    repo = InMemoryItemRepository(edge)

    result = expire_stale_unread(repo, now=NOW, max_age_days=7)

    assert result.expired == 0
    assert edge.status is ItemStatus.NEW


def test_el_exoplanet_match_no_caduca():
    exo = make_item(published_at=NOW - timedelta(days=60), exoplanet_match=True)

    result = expire_stale_unread(InMemoryItemRepository(exo), now=NOW, max_age_days=7)

    assert result.expired == 0
    assert exo.status is ItemStatus.NEW


def test_los_items_del_archivo_no_caducan():
    archive = make_item(source="exoplanet_archive", published_at=NOW - timedelta(days=60))

    result = expire_stale_unread(InMemoryItemRepository(archive), now=NOW, max_age_days=7)

    assert result.expired == 0
    assert archive.status is ItemStatus.NEW


@pytest.mark.parametrize("status", [ItemStatus.FAILED, ItemStatus.READ, ItemStatus.EXPIRED])
def test_los_que_no_son_new_no_se_tocan(status):
    item = make_item(published_at=NOW - timedelta(days=60), status=status)

    result = expire_stale_unread(InMemoryItemRepository(item), now=NOW, max_age_days=7)

    assert result.expired == 0
    assert item.status is status


def test_cola_del_fake_respeta_el_nuevo_orden():
    a = make_item(external_id="2501.000001", published_at=NOW - timedelta(days=2))
    b = make_item(external_id="2501.000002", published_at=NOW - timedelta(days=1))
    exo = make_item(
        external_id="2501.000003", published_at=NOW - timedelta(days=5), exoplanet_match=True
    )

    ordered = InMemoryItemRepository(a, b, exo).next_unread(10)

    assert [i.id for i in ordered] == [exo.id, b.id, a.id]


# --- dominio ---------------------------------------------------------------


def test_expire_lleva_new_a_expired():
    item = make_item()
    item.expire()
    assert item.status is ItemStatus.EXPIRED


def test_expired_es_terminal():
    item = make_item()
    item.expire()
    for action in (item.mark_read, item.mark_failed, item.discard, item.publish, item.expire):
        with pytest.raises(InvalidTransition):
            action()


@pytest.mark.parametrize("status", [ItemStatus.READ, ItemStatus.FAILED, ItemStatus.DISCARDED])
def test_expire_solo_desde_new(status):
    item = make_item(status=status)
    with pytest.raises(InvalidTransition):
        item.expire()


class _CopyingItemRepository(InMemoryItemRepository):
    """Como un repositorio real: `stale_unread` devuelve copias, así que el
    cambio de estado solo persiste si el caso de uso llama a `save`."""

    def stale_unread(self, published_before):
        return [copy.copy(i) for i in super().stale_unread(published_before)]


def test_el_caso_de_uso_persiste_la_caducidad_con_save():
    old = make_item(published_at=NOW - timedelta(days=30))
    repo = _CopyingItemRepository(old)

    result = expire_stale_unread(repo, now=NOW, max_age_days=7)

    assert result.expired == 1
    assert repo.get(old.id).status is ItemStatus.EXPIRED
