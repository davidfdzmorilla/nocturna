"""Migración `c9e4b2a7d135` (rol `writer` en `agent_calls.agent`, T75)."""

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

_REVISION = "c9e4b2a7d135"
_REVISION_BEFORE = "f7c2d8e4a951"
_CHECK = "ck_agent_calls_agent_role"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _check_definition(connection) -> str:
    return connection.execute(
        sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
        {"n": _CHECK},
    ).scalar_one()


def _insert_run(connection) -> uuid.UUID:
    run_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO runs (id, started_at, finished_at, status, budget_tokens, tokens_used, "
            "items_fetched, items_read, findings_published, notes) "
            "VALUES (:id, :now, :now, 'completed', 1, 0, 0, 0, 0, '')"
        ),
        {"id": run_id, "now": _NOW},
    )
    return run_id


def _insert_call(connection, run_id: uuid.UUID, agent: str) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO agent_calls (id, run_id, agent, model, tokens_in, tokens_out, "
            "duration_ms, status) "
            "VALUES (:id, :run, :agent, 'm', 1, 1, 1, 'ok')"
        ),
        {"id": uuid.uuid4(), "run": run_id, "agent": agent},
    )


def test_upgrade_downgrade_upgrade_deja_el_check_identico(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            after_upgrade = _check_definition(connection)
        assert "writer" in after_upgrade

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        with engine.connect() as connection:
            after_downgrade = _check_definition(connection)
        assert "writer" not in after_downgrade
        assert "editor" in after_downgrade

        run_alembic_upgrade(scratch_database_url, "head")
        with engine.connect() as connection:
            assert _check_definition(connection) == after_upgrade
    finally:
        engine.dispose()


def test_tras_upgrade_writer_entra_y_un_rol_inventado_falla(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            run_id = _insert_run(connection)
            _insert_call(connection, run_id, "writer")
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_call(connection, run_id, "bogus")
    finally:
        engine.dispose()


def test_downgrade_con_fila_writer_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _insert_call(connection, _insert_run(connection), "writer")
            before = _check_definition(connection)

        with pytest.raises(RuntimeError, match="writer"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert _check_definition(connection) == before
            assert (
                connection.execute(
                    sa.text("SELECT count(*) FROM agent_calls WHERE agent = 'writer'")
                ).scalar_one()
                == 1
            )
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
