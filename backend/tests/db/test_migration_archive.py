"""Migración `b4d7f1a26c93` (tablas del snapshot del Exoplanet Archive, T81)."""

from __future__ import annotations

import sqlalchemy as sa
from conftest import run_alembic_downgrade, run_alembic_upgrade
from sqlalchemy import inspect

_REVISION_BEFORE = "a79e3c5d8f12"
_TABLES = {"archive_snapshot", "archive_solution", "archive_default_change"}


def test_upgrade_downgrade_upgrade_en_base_efimera(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        assert _TABLES <= set(inspect(engine).get_table_names())

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        assert not _TABLES & set(inspect(engine).get_table_names())

        run_alembic_upgrade(scratch_database_url, "head")
        assert _TABLES <= set(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def test_indices_y_constraints_esperados(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        insp = inspect(engine)
        solution_indexes = {i["name"]: i for i in insp.get_indexes("archive_solution")}
        assert {
            "ix_archive_solution_pl_name",
            "ix_archive_solution_releasedate",
            "uq_archive_solution_pl_name_default_current",
        } <= set(solution_indexes)
        assert solution_indexes["uq_archive_solution_pl_name_default_current"]["unique"]
        assert "ix_archive_default_change_snapshot_id" in {
            i["name"] for i in insp.get_indexes("archive_default_change")
        }
        checks = {c["name"] for c in insp.get_check_constraints("archive_snapshot")}
        assert "ck_archive_snapshot_archive_snapshot_kind" in checks
        columns = {c["name"]: c for c in insp.get_columns("archive_solution")}
        assert isinstance(columns["solution_key"]["type"], sa.CHAR)
        assert columns["solution_key"]["type"].length == 64
        assert columns["removed_at"]["nullable"] is True
        assert columns["is_default_current"]["nullable"] is False
        assert columns["removed_at"]["type"].timezone is True
    finally:
        engine.dispose()
