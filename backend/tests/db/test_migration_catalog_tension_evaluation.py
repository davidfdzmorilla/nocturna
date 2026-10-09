"""Migración `a3f6d9c1b852` (`tension_evaluation_id` obligatorio en `catalog_tension`, T76)."""

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

_REVISION = "a3f6d9c1b852"
_HEAD = "d8b1e4f7a203"  # el downgrade fallido es una sola transaccion: el head sigue puesto
_REVISION_BEFORE = "c9e4b2a7d135"
_CHECK = "ck_findings_tension_evaluation_id_iff_type"
_UNIQUE = "uq_findings_tension_evaluation_id_type"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _seed(connection) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """(item, run, evaluation)."""
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
            "'p', 'radius', 'evaluated', CAST('{}' AS jsonb), :now, :now)"
        ),
        {"id": evaluation_id, "reading": reading_id, "item": item_id, "now": _NOW},
    )
    return item_id, run_id, evaluation_id


def _insert(connection, item_id, run_id, type_: str, evaluation_id) -> None:
    payload = {
        "catalog_tension": "CAST('{\"schema_version\": 1}' AS jsonb)"
        if type_ == "catalog_tension"
        else "NULL",
    }
    connection.execute(
        sa.text(
            "INSERT INTO findings (id, item_id, run_id, type, title, level_curious, "
            "level_amateur, level_technical, catalog_tension, tension_evaluation_id) "
            f"VALUES (:id, :item, :run, :type, 't', 'c', 'a', 'te', {payload['catalog_tension']}, "
            ":ev)"
        ),
        {"id": uuid.uuid4(), "item": item_id, "run": run_id, "type": type_, "ev": evaluation_id},
    )


def _check_definition(connection) -> str:
    return connection.execute(
        sa.text("SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = :n"),
        {"n": _CHECK},
    ).scalar_one()


def test_upgrade_downgrade_upgrade_deja_el_check_identico(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            after_upgrade = _check_definition(connection)
        assert "catalog_tension" in after_upgrade

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        with engine.connect() as connection:
            after_downgrade = _check_definition(connection)
        assert "catalog_tension" not in after_downgrade
        assert "primera_medida" in after_downgrade

        run_alembic_upgrade(scratch_database_url, "head")
        with engine.connect() as connection:
            assert _check_definition(connection) == after_upgrade
    finally:
        engine.dispose()


def test_upgrade_con_catalog_tension_sin_evaluacion_falla_claro_y_no_toca_nada(
    scratch_database_url,
):
    run_alembic_upgrade(scratch_database_url, _REVISION_BEFORE)
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id, run_id, _ = _seed(connection)
            _insert(connection, item_id, run_id, "catalog_tension", None)
            before = _check_definition(connection)

        with pytest.raises(RuntimeError, match="sin tension_evaluation_id"):
            run_alembic_upgrade(scratch_database_url, "head")

        with engine.connect() as connection:
            assert _check_definition(connection) == before
            assert (
                connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == _REVISION_BEFORE
            )
    finally:
        engine.dispose()


def test_downgrade_con_fila_catalog_tension_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id, run_id, evaluation_id = _seed(connection)
            _insert(connection, item_id, run_id, "catalog_tension", evaluation_id)
            before = _check_definition(connection)

        with pytest.raises(RuntimeError, match="catalog_tension"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert _check_definition(connection) == before
            assert (
                connection.execute(
                    sa.text("SELECT count(*) FROM findings WHERE type = 'catalog_tension'")
                ).scalar_one()
                == 1
            )
            assert (
                connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == _HEAD
            )
    finally:
        engine.dispose()


def test_check_rechaza_catalog_tension_sin_evaluacion(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id, run_id, evaluation_id = _seed(connection)
            _insert(connection, item_id, run_id, "catalog_tension", evaluation_id)
        with pytest.raises(IntegrityError, match=_CHECK), engine.begin() as connection:
            _insert(connection, item_id, run_id, "catalog_tension", None)
    finally:
        engine.dispose()


def test_check_rechaza_paper_explained_con_evaluacion(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id, run_id, evaluation_id = _seed(connection)
            _insert(connection, item_id, run_id, "paper_explained", None)
        with pytest.raises(IntegrityError, match=_CHECK), engine.begin() as connection:
            _insert(connection, item_id, run_id, "paper_explained", evaluation_id)
    finally:
        engine.dispose()


def test_indice_unico_impide_dos_catalog_tension_para_la_misma_evaluacion(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id, run_id, evaluation_id = _seed(connection)
            _insert(connection, item_id, run_id, "catalog_tension", evaluation_id)
        with pytest.raises(IntegrityError, match=_UNIQUE), engine.begin() as connection:
            _insert(connection, item_id, run_id, "catalog_tension", evaluation_id)
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
