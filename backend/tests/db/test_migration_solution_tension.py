"""Migración `b6d2f8a4c917` (tabla `solution_tension` y tipo de finding, T85)."""

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

_REVISION = "b6d2f8a4c917"
_HEAD = "e3a7c5b9d214"  # el head posterior; el downgrade es una sola transaccion
_REVISION_BEFORE = "d8b1e4f7a203"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_KEY_A = "a" * 64
_KEY_B = "b" * 64
_DETAIL = '{"schema_version": 1}'


def _seed_archive(connection) -> None:
    snapshot_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO archive_snapshot (id, taken_at, kind, rows_total, defaults_total, "
            "duplicate_rows, payload_sha256, requests, duration_ms) "
            "VALUES (:id, :now, 'full', 0, 0, 0, repeat('0', 64), 0, 0)"
        ),
        {"id": snapshot_id, "now": _NOW},
    )
    for key in (_KEY_A, _KEY_B):
        connection.execute(
            sa.text(
                "INSERT INTO archive_solution (solution_key, pl_name, pl_refname, ref_key, "
                "soltype, releasedate, is_default, first_seen_snapshot_id, "
                "last_seen_snapshot_id, is_default_current) "
                "VALUES (:k, 'p b', 'r', 'r', 'Published Confirmed', '2026-09-01', false, "
                ":s, :s, false)"
            ),
            {"k": key, "s": snapshot_id},
        )


def _insert_tension(
    connection,
    *,
    parameter="mass",
    role="default",
    key=_KEY_A,
    sigma="1.0",
    skip_reason=None,
    item_id=None,
) -> uuid.UUID:
    tension_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO solution_tension (id, new_solution_key, reference_solution_key, "
            "planet_name, parameter, sigma, skip_reason, reference_role, labels, detail, "
            "item_id, computed_at) "
            "VALUES (:id, :k, :r, 'p b', :p, CAST(:sigma AS double precision), :skip, :role, "
            "'{}', CAST(:d AS jsonb), :item, :now)"
        ),
        {
            "id": tension_id,
            "k": key,
            "r": _KEY_B,
            "p": parameter,
            "role": role,
            "sigma": sigma,
            "skip": skip_reason,
            "item": item_id,
            "d": _DETAIL,
            "now": _NOW,
        },
    )
    return tension_id


def _insert_finding(connection, *, type_, payload, tension_id) -> None:
    item_id, run_id = uuid.uuid4(), uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO items (id, source, external_id, title, abstract, categories, "
            "published_at, fetched_at, status) "
            "VALUES (:i, 'exoplanet_archive', :e, 't', 'a', '{}', :now, :now, 'new')"
        ),
        {"i": item_id, "e": str(item_id), "now": _NOW},
    )
    connection.execute(
        sa.text(
            "INSERT INTO runs (id, started_at, finished_at, status, budget_tokens, tokens_used, "
            "items_fetched, items_read, findings_published, notes) "
            "VALUES (:r, :now, :now, 'completed', 1, 0, 0, 0, 0, '')"
        ),
        {"r": run_id, "now": _NOW},
    )
    connection.execute(
        sa.text(
            "INSERT INTO findings (id, item_id, run_id, type, title, level_curious, "
            "level_amateur, level_technical, solution_tension, solution_tension_id) "
            "VALUES (:id, :i, :r, :t, 't', 'c', 'a', 't', CAST(:p AS jsonb), :st)"
        ),
        {
            "id": uuid.uuid4(),
            "i": item_id,
            "r": run_id,
            "t": type_,
            "p": payload,
            "st": tension_id,
        },
    )


def _version(connection) -> str:
    return connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()


def test_check_iff_y_indice_unico(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _seed_archive(connection)
            tension_id = _insert_tension(connection)
            _insert_tension(connection, parameter="radius")
            _insert_finding(
                connection, type_="solution_tension", payload=_DETAIL, tension_id=tension_id
            )

        # tipo solution_tension sin payload / sin id
        with pytest.raises(IntegrityError, match="solution_tension_iff_type"):
            with engine.begin() as connection:
                _insert_finding(
                    connection, type_="solution_tension", payload=None, tension_id=tension_id
                )
        with pytest.raises(IntegrityError, match="solution_tension_id_iff_type"):
            with engine.begin() as connection:
                _insert_finding(
                    connection, type_="solution_tension", payload=_DETAIL, tension_id=None
                )
        # payload o id en un tipo que no es solution_tension
        with pytest.raises(IntegrityError, match="solution_tension_iff_type"):
            with engine.begin() as connection:
                _insert_finding(
                    connection, type_="paper_explained", payload=_DETAIL, tension_id=None
                )
        with pytest.raises(IntegrityError, match="solution_tension_id_iff_type"):
            with engine.begin() as connection:
                _insert_finding(
                    connection, type_="paper_explained", payload=None, tension_id=tension_id
                )
        # una tension no puede tener dos findings
        with pytest.raises(IntegrityError, match="uq_findings_solution_tension_id_type"):
            with engine.begin() as connection:
                _insert_finding(
                    connection, type_="solution_tension", payload=_DETAIL, tension_id=tension_id
                )
        # unico (new_solution_key, parameter)
        with pytest.raises(IntegrityError, match="uq_solution_tension_new_solution_key_parameter"):
            with engine.begin() as connection:
                _insert_tension(connection)
        # CHECK de parametro y de rol
        with pytest.raises(IntegrityError, match="solution_tension_parameter"):
            with engine.begin() as connection:
                _insert_tension(connection, parameter="mass2", key=_KEY_B)
        with pytest.raises(IntegrityError, match="solution_tension_reference_role"):
            with engine.begin() as connection:
                _insert_tension(connection, role="other", key=_KEY_B)
        # CHECK de skip_reason, sigma NOT NULL y item_id solo si elegible
        with pytest.raises(IntegrityError, match="solution_tension_skip_reason"):
            with engine.begin() as connection:
                _insert_tension(connection, skip_reason="inventado", key=_KEY_B)
        with pytest.raises(IntegrityError, match="sigma"):
            with engine.begin() as connection:
                _insert_tension(connection, sigma=None, key=_KEY_B)
        with engine.begin() as connection:
            item_id = _insert_item(connection, "exoplanet_archive")
        with pytest.raises(IntegrityError, match="item_only_if_eligible"):
            with engine.begin() as connection:
                _insert_tension(connection, skip_reason="old_paper", item_id=item_id, key=_KEY_B)
        with engine.begin() as connection:  # elegible con item: permitido
            _insert_tension(connection, item_id=item_id, key=_KEY_B)
    finally:
        engine.dispose()


def _insert_item(connection, source: str) -> uuid.UUID:
    item_id = uuid.uuid4()
    connection.execute(
        sa.text(
            "INSERT INTO items (id, source, external_id, title, abstract, categories, "
            "published_at, fetched_at, status) "
            "VALUES (:i, :s, :e, 't', 'a', '{}', :now, :now, 'new')"
        ),
        {"i": item_id, "s": source, "e": str(item_id), "now": _NOW},
    )
    return item_id


def test_downgrade_con_items_no_arxiv_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _insert_item(connection, "exoplanet_archive")

        with pytest.raises(RuntimeError, match="source <> 'arxiv'"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert _version(connection) == _HEAD
            assert "solution_tension" in sa.inspect(connection).get_table_names()
    finally:
        engine.dispose()


def test_downgrade_con_tensiones_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _seed_archive(connection)
            _insert_tension(connection)

        with pytest.raises(RuntimeError, match="solution_tension"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert _version(connection) == _HEAD
            assert (
                connection.execute(sa.text("SELECT count(*) FROM solution_tension")).scalar_one()
                == 1
            )
    finally:
        engine.dispose()


def test_downgrade_vacio_funciona_y_upgrade_vuelve(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            assert _version(connection) == _REVISION_BEFORE
            inspector = sa.inspect(connection)
            assert "solution_tension" not in inspector.get_table_names()
            columns = {c["name"] for c in inspector.get_columns("findings")}
            assert not {"solution_tension", "solution_tension_id"} & columns
        run_alembic_upgrade(scratch_database_url, "head")
        with engine.connect() as connection:
            assert _version(connection) == _HEAD
            assert "solution_tension" in sa.inspect(connection).get_table_names()
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
