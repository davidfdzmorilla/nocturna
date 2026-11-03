"""Migración `b7e3a1c9d482` (T100: `items.authors` y retirada de findings).

Base efímera por test (`scratch_database_url`). Sin Claude.
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

_REVISION = "b7e3a1c9d482"
_REVISION_BEFORE = "a4d9e1b7c352"
_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_CHECKS = {
    "ck_findings_retraction_together",
    "ck_findings_retraction_only_published",
    "ck_findings_retraction_note_not_blank",
}


def _seed_finding(connection, *, published: bool, retracted_at=None, note=None) -> uuid.UUID:
    item_id, run_id, finding_id = (uuid.uuid4() for _ in range(3))
    connection.execute(
        sa.text(
            "INSERT INTO items (id, source, external_id, title, abstract, categories, "
            "published_at, fetched_at, status) VALUES (:id, 'arxiv', :ext, 't', 'a', "
            "ARRAY['astro-ph.EP'], :now, :now, 'new')"
        ),
        {"id": item_id, "ext": f"x-{item_id.hex[:6]}", "now": _NOW},
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
            "INSERT INTO findings (id, item_id, run_id, type, title, level_curious, "
            "level_amateur, level_technical, confidence, published_at, retracted_at, "
            "retraction_note) VALUES (:id, :item, :run, 'paper_explained', 't', 'c', 'a', 'x', "
            ":conf, :pub, :ret, :note)"
        ),
        {
            "id": finding_id,
            "item": item_id,
            "run": run_id,
            "conf": 0.5 if published else None,
            "pub": _NOW if published else None,
            "ret": retracted_at,
            "note": note,
        },
    )
    return finding_id


def _check_names(connection) -> set[str]:
    rows = connection.execute(
        sa.text("SELECT conname FROM pg_constraint WHERE conrelid = 'findings'::regclass")
    )
    return {r[0] for r in rows} & _CHECKS


def _columns(connection, table: str) -> set[str]:
    return {c["name"] for c in sa.inspect(connection).get_columns(table)}


def test_upgrade_downgrade_upgrade_limpio(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.connect() as connection:
            assert "authors" in _columns(connection, "items")
            assert {"retracted_at", "retraction_note"} <= _columns(connection, "findings")
            assert _check_names(connection) == _CHECKS

        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
        with engine.connect() as connection:
            assert "authors" not in _columns(connection, "items")
            assert not {"retracted_at", "retraction_note"} & _columns(connection, "findings")
            assert _check_names(connection) == set()

        run_alembic_upgrade(scratch_database_url, "head")
        with engine.connect() as connection:
            assert _check_names(connection) == _CHECKS
    finally:
        engine.dispose()


def test_downgrade_con_finding_retirado_falla_y_no_toca_nada(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _seed_finding(connection, published=True, retracted_at=_NOW, note="retirado")

        with pytest.raises(RuntimeError, match="retirado"):
            run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)

        with engine.connect() as connection:
            assert {"retracted_at", "retraction_note"} <= _columns(connection, "findings")
            assert _check_names(connection) == _CHECKS
            assert (
                connection.execute(sa.text("SELECT version_num FROM alembic_version")).scalar_one()
                == _REVISION
            )
    finally:
        engine.dispose()


def test_downgrade_sin_retirados_funciona_con_filas_existentes(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with engine.begin() as connection:
            _seed_finding(connection, published=True)
        run_alembic_downgrade(scratch_database_url, _REVISION_BEFORE)
    finally:
        engine.dispose()


def test_retirar_un_finding_no_publicado_falla(scratch_database_url):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with pytest.raises(IntegrityError, match="retraction_only_published"):
            with engine.begin() as connection:
                _seed_finding(connection, published=False, retracted_at=_NOW, note="n")
    finally:
        engine.dispose()


@pytest.mark.parametrize(
    ("retracted_at", "note"), [(_NOW, None), (None, "solo nota")], ids=["solo-fecha", "solo-nota"]
)
def test_un_solo_campo_informado_falla(scratch_database_url, retracted_at, note):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with pytest.raises(IntegrityError, match="retraction_together"):
            with engine.begin() as connection:
                _seed_finding(connection, published=True, retracted_at=retracted_at, note=note)
    finally:
        engine.dispose()


@pytest.mark.parametrize("note", ["", "   "])
def test_nota_vacia_falla(scratch_database_url, note):
    run_alembic_upgrade(scratch_database_url, "head")
    engine = sa.create_engine(scratch_database_url)
    try:
        with pytest.raises(IntegrityError, match="retraction_note_not_blank"):
            with engine.begin() as connection:
                _seed_finding(connection, published=True, retracted_at=_NOW, note=note)
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
