"""Migración `7c1e4a9b2d35` (findings.catalog_tension) en bases efímeras."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from conftest import run_alembic_downgrade, run_alembic_upgrade
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

_REVISION_BEFORE = "52ccb04d0f06"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_item_and_run(connection) -> tuple[uuid.UUID, uuid.UUID]:
    item_id, run_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO items (id, source, external_id, title, abstract, categories, "
            "published_at, fetched_at, status) VALUES (:id, 'arxiv', 'ext-1', 't', 'a', "
            "ARRAY['astro-ph.EP'], :now, :now, 'new')"
        ),
        {"id": item_id, "now": _NOW},
    )
    connection.execute(
        sa.text(
            "INSERT INTO runs (id, started_at, finished_at, status, budget_tokens, "
            "tokens_used, items_fetched, items_read, findings_published, notes) "
            "VALUES (:id, :now, :now, 'completed', 1, 0, 0, 0, 0, '')"
        ),
        {"id": run_id, "now": _NOW},
    )
    return item_id, run_id


def _insert_finding(connection, item_id, run_id, type_: str, tension: str | None) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO findings (id, item_id, run_id, type, title, level_curious, "
            "level_amateur, level_technical, catalog_tension) "
            "VALUES (:id, :item, :run, :type, 't', 'c', 'a', 'te', CAST(:ct AS jsonb))"
        ),
        {"id": uuid.uuid4(), "item": item_id, "run": run_id, "type": type_, "ct": tension},
    )


def _findings_columns(engine) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns("findings")}


def test_downgrade_sin_filas_catalog_tension_quita_la_columna_y_upgrade_la_restaura(
    scratch_database_url,
):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        assert "catalog_tension" in _findings_columns(engine)

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        assert "catalog_tension" not in _findings_columns(engine)
        with engine.connect() as connection:
            item_id, run_id = _seed_item_and_run(connection)
            with pytest.raises(IntegrityError):
                connection.execute(
                    sa.text(
                        "INSERT INTO findings (id, item_id, run_id, type, title, "
                        "level_curious, level_amateur, level_technical) "
                        "VALUES (:id, :item, :run, 'catalog_tension', 't', 'c', 'a', 'te')"
                    ),
                    {"id": uuid.uuid4(), "item": item_id, "run": run_id},
                )
            connection.rollback()

        run_alembic_upgrade(scratch_database_url, "head")

        assert "catalog_tension" in _findings_columns(engine)
        with engine.begin() as connection:
            item_id, run_id = _seed_item_and_run(connection)
            _insert_finding(connection, item_id, run_id, "catalog_tension", '{"schema_version": 1}')
    finally:
        engine.dispose()


def test_downgrade_con_fila_catalog_tension_falla_y_deja_fila_y_columna_intactas(
    scratch_database_url,
):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id, run_id = _seed_item_and_run(connection)
            _insert_finding(connection, item_id, run_id, "catalog_tension", '{"schema_version": 1}')

        with pytest.raises(RuntimeError, match="catalog_tension"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        assert "catalog_tension" in _findings_columns(engine)
        with engine.connect() as connection:
            rows = connection.execute(
                sa.text("SELECT type, catalog_tension->>'schema_version' FROM findings")
            ).all()
            version = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert [tuple(r) for r in rows] == [("catalog_tension", "1")]
        # El head actual: el downgrade entero se revierte (f7c2d8e4a951 y e5b3a9d1c746 incluidos).
        assert version == "f7c2d8e4a951"
    finally:
        engine.dispose()
