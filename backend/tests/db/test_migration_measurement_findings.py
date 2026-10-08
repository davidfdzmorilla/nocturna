"""Migración `e5b3a9d1c746` (findings primera_medida / confirmacion_independiente, T89)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from conftest import run_alembic_downgrade, run_alembic_upgrade
from sqlalchemy import inspect

_REVISION_BEFORE = "d2a8c5e7f104"
_REVISION = "e5b3a9d1c746"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_NEW_COLUMNS = {"first_measurement", "independent_confirmation", "tension_evaluation_id"}


def _seed(connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    item_id, run_id, reading_id, evaluation_id = (uuid.uuid4() for _ in range(4))
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
    connection.execute(
        sa.text(
            "INSERT INTO readings (id, item_id, summary, objects, claims, interest_score, "
            "tokens_in, tokens_out, model) VALUES (:id, :item, 's', ARRAY[]::text[], "
            "ARRAY[]::text[], 4, 1, 1, 'm')"
        ),
        {"id": reading_id, "item": item_id},
    )
    connection.execute(
        sa.text(
            "INSERT INTO tension_evaluation (id, reading_id, item_id, planet_name, parameter, "
            "status, detail, first_evaluated_at, evaluated_at) VALUES (:id, :reading, :item, "
            "'p', 'radius', 'awaiting_reference', CAST('{}' AS jsonb), :now, :now)"
        ),
        {"id": evaluation_id, "reading": reading_id, "item": item_id, "now": _NOW},
    )
    return item_id, run_id, evaluation_id


def _columns(engine) -> dict[str, dict]:
    return {c["name"]: c for c in inspect(engine).get_columns("findings")}


def _schema_snapshot(engine) -> dict:
    inspector = inspect(engine)
    with engine.connect() as connection:
        checks = connection.execute(
            sa.text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'findings'::regclass ORDER BY conname"
            )
        ).all()
        indexes = connection.execute(
            sa.text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'findings'")
        ).all()
    return {
        "columns": {name: (str(c["type"]), c["nullable"]) for name, c in _columns(engine).items()},
        "constraints": [tuple(r) for r in checks],
        "indexes": sorted(tuple(r) for r in indexes),
        "fks": inspector.get_foreign_keys("findings"),
    }


def test_upgrade_downgrade_upgrade_y_el_esquema_vuelve_exactamente_al_anterior(
    scratch_database_url,
):
    run_alembic_upgrade(scratch_database_url, _REVISION_BEFORE)
    engine = sa.create_engine(scratch_database_url)
    try:
        before = _schema_snapshot(engine)
        assert str(_columns(engine)["type"]["type"]) == "VARCHAR(20)"

        run_alembic_upgrade(scratch_database_url, "head")
        columns = _columns(engine)
        assert _NEW_COLUMNS <= set(columns)
        assert str(columns["type"]["type"]) == "VARCHAR(32)"
        assert all(columns[name]["nullable"] for name in _NEW_COLUMNS)

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        assert _schema_snapshot(engine) == before

        run_alembic_upgrade(scratch_database_url, "head")
        assert _NEW_COLUMNS <= set(_columns(engine))
    finally:
        engine.dispose()


def test_downgrade_con_filas_de_tipos_nuevos_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id, run_id, evaluation_id = _seed(connection)
            connection.execute(
                sa.text(
                    "INSERT INTO findings (id, item_id, run_id, type, title, level_curious, "
                    "level_amateur, level_technical, first_measurement, tension_evaluation_id) "
                    "VALUES (:id, :item, :run, 'primera_medida', 't', 'c', 'a', 'te', "
                    "CAST('{\"schema_version\": 1}' AS jsonb), :ev)"
                ),
                {"id": uuid.uuid4(), "item": item_id, "run": run_id, "ev": evaluation_id},
            )
        with pytest.raises(RuntimeError, match="primera_medida"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        assert _NEW_COLUMNS <= set(_columns(engine))
        with engine.connect() as connection:
            version = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            count = connection.execute(sa.text("SELECT count(*) FROM findings")).scalar_one()
        # El downgrade es una sola transaccion: nada se revierte, el head sigue puesto.
        assert version == "a3f6d9c1b852"
        assert count == 1

        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM findings"))
        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        assert not (_NEW_COLUMNS & set(_columns(engine)))
    finally:
        engine.dispose()
