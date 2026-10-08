"""Migración `d2a8c5e7f104` (tabla `tension_evaluation`, T88) en bases efímeras."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from conftest import run_alembic_downgrade, run_alembic_upgrade
from sqlalchemy import inspect

_REVISION_BEFORE = "b4d7f1a26c93"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed_evaluation(connection) -> None:
    item_id, reading_id = uuid.uuid4(), uuid.uuid4()
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
            "'p', 'mass', 'awaiting_reference', CAST('{}' AS jsonb), :now, :now)"
        ),
        {"id": uuid.uuid4(), "reading": reading_id, "item": item_id, "now": _NOW},
    )


def test_upgrade_downgrade_upgrade_en_base_efimera(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        assert "tension_evaluation" in inspect(engine).get_table_names()

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        assert "tension_evaluation" not in inspect(engine).get_table_names()

        run_alembic_upgrade(scratch_database_url, "head")
        assert "tension_evaluation" in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_downgrade_con_filas_falla_ruidosamente_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _seed_evaluation(connection)

        with pytest.raises(RuntimeError, match="tension_evaluation"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert (
                connection.execute(sa.text("SELECT count(*) FROM tension_evaluation")).scalar_one()
                == 1
            )
            version = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
        assert version == "a3f6d9c1b852"

        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM tension_evaluation"))
        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        assert "tension_evaluation" not in inspect(engine).get_table_names()
    finally:
        engine.dispose()


def test_columnas_indices_checks_y_fks(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        insp = inspect(engine)
        columns = {c["name"]: c for c in insp.get_columns("tension_evaluation")}
        not_null = {name for name, c in columns.items() if not c["nullable"]}
        assert not_null == {
            "id",
            "reading_id",
            "item_id",
            "planet_name",
            "parameter",
            "status",
            "detail",
            "first_evaluated_at",
            "evaluated_at",
        }
        assert columns["status"]["type"].length == 30
        assert columns["parameter"]["type"].length == 20
        assert isinstance(columns["reference_solution_key"]["type"], sa.CHAR)
        assert columns["reference_solution_key"]["type"].length == 64
        assert isinstance(columns["own_solution_key"]["type"], sa.CHAR)
        assert isinstance(columns["reference_sigma"]["type"], sa.Double)
        assert columns["evaluated_at"]["type"].timezone is True
        assert columns["first_evaluated_at"]["type"].timezone is True

        indexes = {i["name"]: i for i in insp.get_indexes("tension_evaluation")}
        unique = indexes["uq_tension_evaluation_reading_id_planet_name_parameter"]
        assert unique["unique"]
        assert unique["column_names"] == ["reading_id", "planet_name", "parameter"]
        assert indexes["ix_tension_evaluation_status"]["column_names"] == ["status"]

        checks = {c["name"]: c["sqltext"] for c in insp.get_check_constraints("tension_evaluation")}
        assert set(checks) == {
            "ck_tension_evaluation_tension_evaluation_status",
            "ck_tension_evaluation_tension_evaluation_parameter",
        }
        for value in (
            "awaiting_reference",
            "evaluated",
            "consistent_with_limit",
            "incompatible_with_limit",
            "closed_loop",
        ):
            assert value in checks["ck_tension_evaluation_tension_evaluation_status"]
        for value in ("mass", "radius", "period"):
            assert value in checks["ck_tension_evaluation_tension_evaluation_parameter"]

        fks = {
            tuple(fk["constrained_columns"]): fk["referred_table"]
            for fk in insp.get_foreign_keys("tension_evaluation")
        }
        assert fks == {
            ("reading_id",): "readings",
            ("item_id",): "items",
            ("reference_solution_key",): "archive_solution",
            ("own_solution_key",): "archive_solution",
        }
    finally:
        engine.dispose()
