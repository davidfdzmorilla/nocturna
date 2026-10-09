"""Migración `f7c2d8e4a951` (historia de `readings`, T82)."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from conftest import run_alembic_downgrade, run_alembic_upgrade
from sqlalchemy import inspect

_REVISION_BEFORE = "e5b3a9d1c746"
_REVISION = "f7c2d8e4a951"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _insert_item(connection, external_id: str) -> uuid.UUID:
    item_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO items (id, source, external_id, title, abstract, categories, "
            "published_at, fetched_at, status) VALUES (:id, 'arxiv', :ext, 't', 'a', "
            "ARRAY['astro-ph.EP'], :now, :now, 'read')"
        ),
        {"id": item_id, "ext": external_id, "now": _NOW},
    )
    return item_id


def _insert_run(connection) -> uuid.UUID:
    run_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO runs (id, started_at, finished_at, status, budget_tokens, "
            "tokens_used, items_fetched, items_read, findings_published, notes) "
            "VALUES (:id, :now, :now, 'completed', 1, 0, 0, 0, 0, '')"
        ),
        {"id": run_id, "now": _NOW},
    )
    return run_id


def _insert_reading(connection, item_id: uuid.UUID, *, superseded: bool = False) -> uuid.UUID:
    reading_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO readings (id, item_id, summary, objects, claims, interest_score, "
            "tokens_in, tokens_out, model" + (", superseded_at" if superseded else "") + ") "
            "VALUES (:id, :item, 's', ARRAY[]::text[], ARRAY[]::text[], 4, 1, 1, 'm'"
            + (", :now" if superseded else "")
            + ")"
        ),
        {"id": reading_id, "item": item_id, "now": _NOW},
    )
    return reading_id


def _insert_call(
    connection, run_id: uuid.UUID, item_id: uuid.UUID, status: str, prompt_version: str | None
) -> None:
    connection.execute(
        sa.text(
            "INSERT INTO agent_calls (id, run_id, item_id, agent, model, tokens_in, tokens_out, "
            "duration_ms, status, prompt_version) VALUES (:id, :run, :item, 'reader', 'm', 1, 1, "
            "1, :status, :pv)"
        ),
        {
            "id": uuid.uuid4(),
            "run": run_id,
            "item": item_id,
            "status": status,
            "pv": prompt_version,
        },
    )


def _schema_snapshot(engine) -> dict:
    inspector = inspect(engine)
    with engine.connect() as connection:
        constraints = connection.execute(
            sa.text(
                "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'readings'::regclass ORDER BY conname"
            )
        ).all()
        indexes = connection.execute(
            sa.text("SELECT indexname, indexdef FROM pg_indexes WHERE tablename = 'readings'")
        ).all()
    return {
        "columns": {
            c["name"]: (str(c["type"]), c["nullable"]) for c in inspector.get_columns("readings")
        },
        "constraints": [tuple(r) for r in constraints],
        "indexes": sorted(tuple(r) for r in indexes),
        "fks": inspector.get_foreign_keys("readings"),
    }


def test_upgrade_downgrade_upgrade_y_el_esquema_vuelve_exactamente_al_anterior(
    scratch_database_url,
):
    run_alembic_upgrade(scratch_database_url, _REVISION_BEFORE)
    engine = sa.create_engine(scratch_database_url)
    try:
        before = _schema_snapshot(engine)

        run_alembic_upgrade(scratch_database_url, "head")
        columns = {c["name"]: c for c in inspect(engine).get_columns("readings")}
        assert str(columns["prompt_version"]["type"]) == "VARCHAR(50)"
        assert columns["prompt_version"]["nullable"] is True
        assert columns["superseded_at"]["type"].timezone is True
        assert columns["superseded_at"]["nullable"] is True
        snapshot = _schema_snapshot(engine)
        index_defs = dict(snapshot["indexes"])
        assert "uq_readings_item_id" not in index_defs
        definition = index_defs["uq_readings_item_id_current"]
        assert "UNIQUE" in definition
        assert "superseded_at IS NULL" in definition

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        assert _schema_snapshot(engine) == before

        run_alembic_upgrade(scratch_database_url, "head")
        assert _schema_snapshot(engine) == snapshot
    finally:
        engine.dispose()


def test_relleno_de_prompt_version_solo_si_no_es_ambiguo(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, _REVISION_BEFORE)
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            run_id = _insert_run(connection)
            una_ok = _insert_item(connection, "una-ok")
            ninguna = _insert_item(connection, "ninguna")
            ambigua = _insert_item(connection, "ambigua")
            ok_y_error = _insert_item(connection, "ok-y-error")
            sin_version = _insert_item(connection, "sin-version")
            dos_ok_misma = _insert_item(connection, "dos-ok-misma")
            ok_con_null = _insert_item(connection, "ok-con-null")
            readings = {
                name: _insert_reading(connection, item)
                for name, item in {
                    "una_ok": una_ok,
                    "ninguna": ninguna,
                    "ambigua": ambigua,
                    "ok_y_error": ok_y_error,
                    "sin_version": sin_version,
                    "dos_ok_misma": dos_ok_misma,
                    "ok_con_null": ok_con_null,
                }.items()
            }
            _insert_call(connection, run_id, una_ok, "ok", "reader-v3")
            _insert_call(connection, run_id, ambigua, "ok", "reader-v2")
            _insert_call(connection, run_id, ambigua, "ok", "reader-v3")
            _insert_call(connection, run_id, ok_y_error, "invalid_output", "reader-v2")
            _insert_call(connection, run_id, ok_y_error, "ok", "reader-v2")
            _insert_call(connection, run_id, sin_version, "ok", None)
            _insert_call(connection, run_id, dos_ok_misma, "ok", "reader-v3")
            _insert_call(connection, run_id, dos_ok_misma, "ok", "reader-v3")
            _insert_call(connection, run_id, ok_con_null, "ok", "reader-v3")
            _insert_call(connection, run_id, ok_con_null, "ok", None)

        run_alembic_upgrade(scratch_database_url, "head")

        with engine.connect() as connection:
            rows = dict(
                connection.execute(sa.text("SELECT id, prompt_version FROM readings")).all()
            )
            superseded = connection.execute(
                sa.text("SELECT count(*) FROM readings WHERE superseded_at IS NOT NULL")
            ).scalar_one()
        assert rows[readings["una_ok"]] == "reader-v3"
        assert rows[readings["ok_y_error"]] == "reader-v2"
        assert rows[readings["ninguna"]] is None
        assert rows[readings["ambigua"]] is None
        assert rows[readings["sin_version"]] is None
        assert rows[readings["dos_ok_misma"]] == "reader-v3"
        assert rows[readings["ok_con_null"]] is None
        assert superseded == 0
    finally:
        engine.dispose()


def test_el_indice_parcial_rechaza_dos_vigentes_y_admite_una_sustituida(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id = _insert_item(connection, "x")
            _insert_reading(connection, item_id, superseded=True)
            _insert_reading(connection, item_id)
        with pytest.raises(sa.exc.IntegrityError, match="uq_readings_item_id_current"):
            with engine.begin() as connection:
                _insert_reading(connection, item_id)
    finally:
        engine.dispose()


def test_downgrade_con_lecturas_sustituidas_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            item_id = _insert_item(connection, "x")
            _insert_reading(connection, item_id, superseded=True)
            _insert_reading(connection, item_id)
        with pytest.raises(RuntimeError, match="sustituida"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            version = connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalar_one()
            count = connection.execute(sa.text("SELECT count(*) FROM readings")).scalar_one()
        assert version == "d8b1e4f7a203"  # head actual; el downgrade es una sola transaccion
        assert count == 2
        assert "superseded_at" in {c["name"] for c in inspect(engine).get_columns("readings")}

        with engine.begin() as connection:
            connection.execute(sa.text("DELETE FROM readings WHERE superseded_at IS NOT NULL"))
        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        assert "superseded_at" not in {c["name"] for c in inspect(engine).get_columns("readings")}
    finally:
        engine.dispose()
