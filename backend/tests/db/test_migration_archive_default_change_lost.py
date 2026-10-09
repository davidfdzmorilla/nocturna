"""Migración `d8b1e4f7a203` (`new_solution_key` nullable con CHECK, T84)."""

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

_REVISION = "d8b1e4f7a203"
_REVISION_BEFORE = "a3f6d9c1b852"
_CHECK = "ck_archive_default_change_old_or_new"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_KEY_A = "a" * 64
_KEY_B = "b" * 64


def _insert_snapshot(connection) -> uuid.UUID:
    snapshot_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO archive_snapshot (id, taken_at, kind, rows_total, defaults_total, "
            "duplicate_rows, payload_sha256, requests, duration_ms) "
            "VALUES (:id, :now, 'full', 0, 0, 0, repeat('0', 64), 0, 0)"
        ),
        {"id": snapshot_id, "now": _NOW},
    )
    return snapshot_id


def _insert_change(connection, snapshot_id, old, new) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO archive_default_change (id, pl_name, old_solution_key, "
            "new_solution_key, snapshot_id, detected_at) "
            "VALUES (:id, 'p', :old, :new, :snap, :now)"
        ),
        {"id": uuid.uuid4(), "old": old, "new": new, "snap": snapshot_id, "now": _NOW},
    )


def _is_nullable(connection) -> bool:
    return (
        connection.execute(
            sa.text(
                "SELECT is_nullable FROM information_schema.columns WHERE "
                "table_name = 'archive_default_change' AND column_name = 'new_solution_key'"
            )
        ).scalar_one()
        == "YES"
    )


def _check_exists(connection) -> bool:
    return (
        connection.execute(
            sa.text("SELECT count(*) FROM pg_constraint WHERE conname = :n"), {"n": _CHECK}
        ).scalar_one()
        == 1
    )


def test_check_rechaza_ambas_nulas_y_acepta_old_sin_new(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            snapshot_id = _insert_snapshot(connection)
            assert _is_nullable(connection)
            _insert_change(connection, snapshot_id, _KEY_A, None)
            _insert_change(connection, snapshot_id, None, _KEY_B)
            _insert_change(connection, snapshot_id, _KEY_A, _KEY_B)
        with pytest.raises(IntegrityError, match=_CHECK), engine.begin() as connection:
            _insert_change(connection, snapshot_id, None, None)
    finally:
        engine.dispose()


def test_downgrade_con_new_nula_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _insert_change(connection, _insert_snapshot(connection), _KEY_A, None)

        with pytest.raises(RuntimeError, match="new_solution_key NULL"):
            run_alembic_downgrade(scratch_database_url, "-1")

        with engine.connect() as connection:
            assert _check_exists(connection)
            assert _is_nullable(connection)
            assert (
                connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == _REVISION
            )
    finally:
        engine.dispose()


def test_downgrade_sin_filas_nulas_funciona_y_upgrade_vuelve(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _insert_change(connection, _insert_snapshot(connection), _KEY_A, _KEY_B)

        run_alembic_downgrade(scratch_database_url, "-1")
        with engine.connect() as connection:
            assert not _check_exists(connection)
            assert not _is_nullable(connection)
            assert (
                connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == _REVISION_BEFORE
            )

        run_alembic_upgrade(scratch_database_url, "head")
        with engine.connect() as connection:
            assert _check_exists(connection)
            assert _is_nullable(connection)
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
