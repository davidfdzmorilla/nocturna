"""`SqlAlchemyItemRepository.last_fetched_at` contra PostgreSQL (T93)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from factories import make_item

from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository

pytestmark = pytest.mark.db


def test_last_fetched_at_con_base_vacia_devuelve_none(db_session):
    assert SqlAlchemyItemRepository(db_session).last_fetched_at("arxiv") is None


def test_last_fetched_at_devuelve_el_maximo_de_la_fuente(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(
                external_id="2610.00001", fetched_at=datetime(2026, 10, 1, 22, 5, tzinfo=UTC)
            ),
            make_item(
                external_id="2610.00002", fetched_at=datetime(2026, 10, 9, 22, 5, tzinfo=UTC)
            ),
        ]
    )

    assert repo.last_fetched_at("arxiv") == datetime(2026, 10, 9, 22, 5, tzinfo=UTC)


def test_last_fetched_at_ignora_otras_fuentes(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    repo.add_many(
        [
            make_item(
                external_id="2610.00001", fetched_at=datetime(2026, 10, 1, 22, 5, tzinfo=UTC)
            ),
            make_item(
                source="exoplanet_archive",
                external_id="sol-1",
                fetched_at=datetime(2026, 10, 12, 3, 0, tzinfo=UTC),
            ),
        ]
    )

    assert repo.last_fetched_at("arxiv") == datetime(2026, 10, 1, 22, 5, tzinfo=UTC)
    assert repo.last_fetched_at("exoplanet_archive") == datetime(2026, 10, 12, 3, 0, tzinfo=UTC)
