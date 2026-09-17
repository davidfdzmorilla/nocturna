"""Tests de la migración inicial de Alembic contra PostgreSQL real.

No se fía de leer `alembic/versions/*.py`: todo se comprueba introspeccionando
la base de datos después de correr la migración de verdad.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from conftest import run_alembic_downgrade, run_alembic_upgrade
from sqlalchemy import inspect

from nocturna.infrastructure.db.models import Base

EXPECTED_TABLES = {"items", "readings", "findings", "runs", "agent_calls"}

# Revisión anterior a "950738867fb9" (item failed status and agent call
# prompt version): el `down_revision` declarado en esa migración.
_REVISION_BEFORE_FAILED_STATUS = "a318e7fd86a9"


def test_upgrade_head_crea_las_cinco_tablas(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")

    engine = sa.create_engine(scratch_database_url)
    try:
        inspector = inspect(engine)
        # "alembic_version" es la tabla de control de la propia herramienta,
        # no una tabla del dominio: se excluye a propósito.
        tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert tables == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_columnas_y_nulabilidad_de_items(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")

    engine = sa.create_engine(scratch_database_url)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("items")}
    finally:
        engine.dispose()

    expected_not_null = {
        "id",
        "source",
        "external_id",
        "title",
        "abstract",
        "categories",
        "published_at",
        "fetched_at",
        "status",
    }
    assert set(columns) == expected_not_null
    for name in expected_not_null:
        assert columns[name]["nullable"] is False, f"'{name}' debería ser NOT NULL"


def test_columnas_y_nulabilidad_de_readings(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")

    engine = sa.create_engine(scratch_database_url)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("readings")}
    finally:
        engine.dispose()

    expected_not_null = {
        "id",
        "item_id",
        "summary",
        "objects",
        "claims",
        "interest_score",
        "tokens_in",
        "tokens_out",
        "model",
    }
    assert set(columns) == expected_not_null
    for name in expected_not_null:
        assert columns[name]["nullable"] is False, f"'{name}' debería ser NOT NULL"


def test_columnas_y_nulabilidad_de_findings(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")

    engine = sa.create_engine(scratch_database_url)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("findings")}
    finally:
        engine.dispose()

    expected_not_null = {
        "id",
        "item_id",
        "run_id",
        "type",
        "title",
        "level_curious",
        "level_amateur",
        "level_technical",
    }
    expected_nullable = {"confidence", "published_at"}
    assert set(columns) == expected_not_null | expected_nullable
    for name in expected_not_null:
        assert columns[name]["nullable"] is False, f"'{name}' debería ser NOT NULL"
    for name in expected_nullable:
        assert columns[name]["nullable"] is True, f"'{name}' debería admitir NULL"


def test_columnas_y_nulabilidad_de_runs(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")

    engine = sa.create_engine(scratch_database_url)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("runs")}
    finally:
        engine.dispose()

    expected_not_null = {
        "id",
        "started_at",
        "status",
        "budget_tokens",
        "tokens_used",
        "items_fetched",
        "items_read",
        "findings_published",
        "notes",
    }
    expected_nullable = {"finished_at"}
    assert set(columns) == expected_not_null | expected_nullable
    for name in expected_not_null:
        assert columns[name]["nullable"] is False, f"'{name}' debería ser NOT NULL"
    for name in expected_nullable:
        assert columns[name]["nullable"] is True, f"'{name}' debería admitir NULL"


def test_columnas_y_nulabilidad_de_agent_calls(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")

    engine = sa.create_engine(scratch_database_url)
    try:
        columns = {c["name"]: c for c in inspect(engine).get_columns("agent_calls")}
    finally:
        engine.dispose()

    expected_not_null = {
        "id",
        "run_id",
        "agent",
        "model",
        "tokens_in",
        "tokens_out",
        "duration_ms",
        "status",
    }
    expected_nullable = {"item_id", "prompt_version"}
    assert set(columns) == expected_not_null | expected_nullable
    for name in expected_not_null:
        assert columns[name]["nullable"] is False, f"'{name}' debería ser NOT NULL"
    for name in expected_nullable:
        assert columns[name]["nullable"] is True, f"'{name}' debería admitir NULL"


def test_downgrade_base_deja_el_esquema_vacio_y_permite_upgrade_de_nuevo(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")

    run_alembic_downgrade(scratch_database_url, "base")

    engine = sa.create_engine(scratch_database_url)
    try:
        inspector = inspect(engine)
        remaining_tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert remaining_tables == set()
    finally:
        engine.dispose()

    run_alembic_upgrade(scratch_database_url, "head")

    engine = sa.create_engine(scratch_database_url)
    try:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names()) - {"alembic_version"}
        assert tables == EXPECTED_TABLES
    finally:
        engine.dispose()


def test_downgrade_con_item_en_estado_failed_falla_ruidosamente_y_no_lo_reescribe(
    scratch_database_url,
):
    """La migración `950738867fb9` documenta que su `downgrade()` rechaza
    bajar el esquema si hay filas `items.status = 'failed'`, en vez de
    reescribirlas en silencio a otro estado: reabrir en silencio un ítem
    dado por perdido le haría gastar presupuesto otra vez la noche
    siguiente sin que nadie lo decidiera.
    """
    run_alembic_upgrade(scratch_database_url, "head")

    engine = sa.create_engine(scratch_database_url)
    item_id = uuid.uuid4()
    now = datetime.now(UTC)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "INSERT INTO items "
                    "(id, source, external_id, title, abstract, categories, "
                    "published_at, fetched_at, status) "
                    "VALUES (:id, 'arxiv', 'ext-failed-1', 'título', 'abstract', "
                    "ARRAY['astro-ph.EP'], :now, :now, 'failed')"
                ),
                {"id": item_id, "now": now},
            )

        with pytest.raises(RuntimeError, match="failed"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE_FAILED_STATUS)

        # La fila no se reescribió en silencio: sigue en 'failed', y el
        # esquema sigue teniendo la columna que el downgrade iba a borrar.
        with engine.connect() as connection:
            status = connection.execute(
                sa.text("SELECT status FROM items WHERE id = :id"), {"id": item_id}
            ).scalar_one()
            columns = {c["name"] for c in inspect(engine).get_columns("agent_calls")}
        assert status == "failed"
        assert "prompt_version" in columns
    finally:
        engine.dispose()


def test_autogenerate_contra_el_esquema_migrado_no_produce_operaciones(test_database_url):
    engine = sa.create_engine(test_database_url)
    try:
        with engine.connect() as connection:
            migration_context = MigrationContext.configure(connection, opts={"compare_type": False})
            diff = compare_metadata(migration_context, Base.metadata)
    finally:
        engine.dispose()

    assert diff == [], f"autogenerate detecta diferencias no migradas: {diff!r}"


def test_los_cinco_indices_existen_por_nombre(test_database_url):
    engine = sa.create_engine(test_database_url)
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                sa.text(
                    "SELECT indexname, indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = ANY(:names)"
                ),
                {
                    "names": [
                        "uq_items_source_external_id",
                        "uq_readings_item_id",
                        "ix_findings_published_at",
                        "uq_runs_status_running",
                        "ix_agent_calls_run_id_agent",
                    ]
                },
            ).all()
    finally:
        engine.dispose()

    found = {row.indexname: row.indexdef for row in rows}
    assert set(found) == {
        "uq_items_source_external_id",
        "uq_readings_item_id",
        "ix_findings_published_at",
        "uq_runs_status_running",
        "ix_agent_calls_run_id_agent",
    }


def test_ix_findings_published_at_es_parcial_con_su_predicado(test_database_url):
    engine = sa.create_engine(test_database_url)
    try:
        with engine.connect() as connection:
            indexdef = connection.execute(
                sa.text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = 'ix_findings_published_at'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert "WHERE (published_at IS NOT NULL)" in indexdef


def test_uq_runs_status_running_es_parcial_con_su_predicado(test_database_url):
    engine = sa.create_engine(test_database_url)
    try:
        with engine.connect() as connection:
            indexdef = connection.execute(
                sa.text(
                    "SELECT indexdef FROM pg_indexes "
                    "WHERE schemaname = 'public' AND indexname = 'uq_runs_status_running'"
                )
            ).scalar_one()
    finally:
        engine.dispose()

    assert "CREATE UNIQUE INDEX" in indexdef
    assert "WHERE" in indexdef
    assert "'running'" in indexdef
