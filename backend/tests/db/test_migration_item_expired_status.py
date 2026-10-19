"""Migración `e3a7c5b9d214` (estado `expired` en `items.status`, T94)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import run_alembic_downgrade, run_alembic_upgrade
from sqlalchemy.exc import IntegrityError

from nocturna.infrastructure.db.models import Base

_REVISION = "e3a7c5b9d214"
_REVISION_BEFORE = "b6d2f8a4c917"
_CHECK = "ck_items_item_status"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _check_definition(connection) -> str:
    return connection.execute(
        sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
        {"n": _CHECK},
    ).scalar_one()


def _insert_item(connection, status: str) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO items (id, source, external_id, title, abstract, categories, "
            "published_at, fetched_at, status) "
            "VALUES (:id, 'arxiv', :ext, 't', 'a', ARRAY['astro-ph.EP'], :now, :now, :status)"
        ),
        {"id": uuid.uuid4(), "ext": f"2601.{uuid.uuid4().hex[:5]}", "now": _NOW, "status": status},
    )


def test_upgrade_downgrade_upgrade_deja_el_check_identico(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            after_upgrade = _check_definition(connection)
        assert "expired" in after_upgrade

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        with engine.connect() as connection:
            after_downgrade = _check_definition(connection)
        assert "expired" not in after_downgrade
        assert "failed" in after_downgrade

        run_alembic_upgrade(scratch_database_url, "head")
        with engine.connect() as connection:
            assert _check_definition(connection) == after_upgrade
    finally:
        engine.dispose()


def test_tras_upgrade_expired_entra_y_un_valor_inventado_falla(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _insert_item(connection, "expired")
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_item(connection, "bogus")
    finally:
        engine.dispose()


def test_downgrade_con_fila_expired_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _insert_item(connection, "expired")
            before = _check_definition(connection)

        with pytest.raises(RuntimeError, match="expired"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert _check_definition(connection) == before
            assert (
                connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == _REVISION
            )
    finally:
        engine.dispose()


def test_autogenerate_tras_la_migracion_no_produce_operaciones(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": False})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"autogenerate detecta diferencias no migradas: {diff!r}"
