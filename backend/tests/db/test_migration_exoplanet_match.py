"""Migración `a79e3c5d8f12` (items.exoplanet_match) en bases efímeras."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from conftest import run_alembic_downgrade, run_alembic_upgrade
from sqlalchemy import inspect

_REVISION_BEFORE = "7c1e4a9b2d35"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _items_columns(engine) -> dict[str, dict]:
    return {c["name"]: c for c in inspect(engine).get_columns("items")}


def _insert_legacy_item(engine, external_id: str) -> None:
    """Inserta SIN la columna nueva: lo que hacía el código anterior a T79."""
    with engine.begin() as connection:
        connection.execute(
            sa.text(
                "INSERT INTO items (id, source, external_id, title, abstract, categories, "
                "published_at, fetched_at, status) VALUES (:id, 'arxiv', :ext, 't', 'a', "
                "ARRAY['astro-ph.EP'], :now, :now, 'new')"
            ),
            {"id": uuid.uuid4(), "ext": external_id, "now": _NOW},
        )


def test_upgrade_downgrade_upgrade_en_base_efimera(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        assert "exoplanet_match" in _items_columns(engine)

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        assert "exoplanet_match" not in _items_columns(engine)

        run_alembic_upgrade(scratch_database_url, "head")
        assert "exoplanet_match" in _items_columns(engine)
    finally:
        engine.dispose()


def test_la_columna_es_boolean_not_null_con_default_false(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        column = _items_columns(engine)["exoplanet_match"]

        assert isinstance(column["type"], sa.Boolean)
        assert column["nullable"] is False
        assert "false" in str(column["default"]).lower()
    finally:
        engine.dispose()


def test_las_filas_existentes_quedan_en_false_tras_el_upgrade(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, _REVISION_BEFORE)
    engine = sa.create_engine(scratch_database_url)
    try:
        _insert_legacy_item(engine, "2601.00001")

        run_alembic_upgrade(scratch_database_url, "head")

        with engine.connect() as connection:
            values = connection.execute(sa.text("SELECT exoplanet_match FROM items")).scalars()
            assert list(values) == [False]
    finally:
        engine.dispose()


def test_el_downgrade_conserva_las_filas(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO items (id, source, external_id, title, abstract, categories, "
                    "published_at, fetched_at, status, exoplanet_match) VALUES (:id, 'arxiv', "
                    "'2601.00001', 't', 'a', ARRAY['astro-ph.EP'], :now, :now, 'new', true)"
                ),
                {"id": uuid.uuid4(), "now": _NOW},
            )

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert connection.execute(sa.text("SELECT count(*) FROM items")).scalar_one() == 1
    finally:
        engine.dispose()
