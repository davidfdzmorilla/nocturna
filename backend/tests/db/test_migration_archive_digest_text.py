"""Migración de la tabla `archive_digest_text` (T84.r), la que sigue a `e3a7c5b9d214`.

Base efímera por test (`scratch_database_url`). Sin Claude. Fichero en español.
"""

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

_REVISION_BEFORE = "e3a7c5b9d214"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_COLUMNS = {
    "week",
    "status",
    "title",
    "summary",
    "input_sha256",
    "prompt_version",
    "run_id",
    "created_at",
}


def _tables(connection) -> set[str]:
    return set(sa.inspect(connection).get_table_names())


def _insert_run(connection) -> uuid.UUID:
    run_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO runs (id, started_at, finished_at, status, budget_tokens, "
            "tokens_used, items_fetched, items_read, findings_published, notes) "
            "VALUES (:id, :now, :now, 'completed', 1000, 0, 0, 0, 0, '')"
        ),
        {"id": run_id, "now": _NOW},
    )
    return run_id


def _insert_text(connection, run_id, week: str = "2026-W42") -> None:
    connection.execute(
        sa.text(
            "INSERT INTO archive_digest_text "
            "(week, status, title, summary, input_sha256, prompt_version, run_id, created_at) "
            "VALUES (:w, 'written', 't', 's', repeat('a', 64), 'writer-digest-v1', :r, :now)"
        ),
        {"w": week, "r": run_id, "now": _NOW},
    )


def test_upgrade_crea_la_tabla_con_sus_columnas(scratch_database_url) -> None:
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            assert "archive_digest_text" in _tables(connection)
            columns = {c["name"] for c in sa.inspect(connection).get_columns("archive_digest_text")}
            pk = sa.inspect(connection).get_pk_constraint("archive_digest_text")
        assert columns == _COLUMNS
        assert pk["constrained_columns"] == ["week"]
    finally:
        engine.dispose()


def test_downgrade_sin_filas_la_borra_y_el_upgrade_la_recrea(scratch_database_url) -> None:
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        with engine.connect() as connection:
            assert "archive_digest_text" not in _tables(connection)
            version = connection.execute(sa.text("SELECT version_num FROM alembic_version"))
            assert version.scalar_one() == _REVISION_BEFORE

        run_alembic_upgrade(scratch_database_url, "head")
        with engine.connect() as connection:
            assert "archive_digest_text" in _tables(connection)
    finally:
        engine.dispose()


def test_downgrade_con_filas_falla_y_no_toca_nada(scratch_database_url) -> None:
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _insert_text(connection, _insert_run(connection))
            head = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()

        with pytest.raises(RuntimeError, match="archive_digest_text"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert "archive_digest_text" in _tables(connection)
            count = connection.execute(sa.text("SELECT count(*) FROM archive_digest_text"))
            assert count.scalar_one() == 1
            version = connection.execute(sa.text("SELECT version_num FROM alembic_version"))
            assert version.scalar_one() == head != _REVISION_BEFORE
    finally:
        engine.dispose()


def test_las_restricciones_de_la_migracion_rechazan_filas_invalidas(scratch_database_url) -> None:
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            run_id = _insert_run(connection)
            _insert_text(connection, run_id)
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_text(connection, run_id)  # misma semana
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_text(connection, run_id, week="2026-W5")  # formato
        with pytest.raises(IntegrityError), engine.begin() as connection:
            _insert_text(connection, uuid.uuid4(), week="2026-W43")  # run inexistente
    finally:
        engine.dispose()


def test_autogenerate_tras_la_migracion_no_produce_operaciones(scratch_database_url) -> None:
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            context = MigrationContext.configure(connection, opts={"compare_type": False})
            diff = compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()
    assert diff == [], f"autogenerate detecta diferencias no migradas: {diff!r}"
