"""Persistencia de `Finding.catalog_tension` (T72) contra PostgreSQL.

Las filas inválidas se insertan con SQL crudo para que salte el CHECK de la
base y no la validación del dominio o de `sa.Enum`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from factories import make_finding, make_item, make_run
from helpers.exoplanet import catalog_tension_v1298_b
from sqlalchemy.exc import IntegrityError
from test_finding_measurement_types import _seed_evaluation
from test_schema_constraints import _seed_item, _seed_run, _valid_finding_row

from nocturna.domain.entities import FindingType
from nocturna.infrastructure.db.mappers import _catalog_tension_to_json
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)

PUBLISHED_AT = datetime(2026, 3, 2, tzinfo=UTC)


def _insert_finding_raw(session, row: dict, catalog_tension: dict | None) -> None:
    values = dict(row)
    values["catalog_tension"] = json.dumps(catalog_tension) if catalog_tension is not None else None
    columns = ", ".join(values)
    placeholders = ", ".join(
        "CAST(:catalog_tension AS jsonb)" if name == "catalog_tension" else f":{name}"
        for name in values
    )
    session.execute(sa.text(f"INSERT INTO findings ({columns}) VALUES ({placeholders})"), values)


def _payload() -> dict:
    payload = _catalog_tension_to_json(catalog_tension_v1298_b())
    assert payload is not None
    return payload


def _seed(db_session):
    return _seed_item(db_session), _seed_run(db_session)


def test_insert_catalog_tension_con_dato_es_valido(db_session):
    item_id, run_id = _seed(db_session)
    row = _valid_finding_row(
        item_id,
        run_id,
        type="catalog_tension",
        tension_evaluation_id=_seed_evaluation(db_session, item_id),
    )

    _insert_finding_raw(db_session, row, _payload())

    stored = db_session.execute(
        sa.text("SELECT catalog_tension->>'schema_version' FROM findings WHERE id = :id"),
        {"id": row["id"]},
    ).scalar_one()
    assert stored == "1"


def test_catalog_tension_sin_dato_falla(db_session):
    item_id, run_id = _seed(db_session)
    row = _valid_finding_row(item_id, run_id, type="catalog_tension")

    with pytest.raises(IntegrityError, match="catalog_tension_iff_type"):
        _insert_finding_raw(db_session, row, None)


def test_paper_explained_con_dato_falla(db_session):
    item_id, run_id = _seed(db_session)
    row = _valid_finding_row(item_id, run_id, type="paper_explained")

    with pytest.raises(IntegrityError, match="catalog_tension_iff_type"):
        _insert_finding_raw(db_session, row, _payload())


def test_tipo_inventado_falla_incluso_con_dato(db_session):
    item_id, run_id = _seed(db_session)
    row = _valid_finding_row(item_id, run_id, type="bogus")

    with pytest.raises(IntegrityError):
        _insert_finding_raw(db_session, row, _payload())


def test_paper_explained_guardado_por_el_repositorio_deja_sql_null(db_session):
    item = make_item()
    run = make_run()
    SqlAlchemyItemRepository(db_session).add_many([item])
    SqlAlchemyRunRepository(db_session).add(run)
    db_session.flush()
    finding = make_finding(item_id=item.id, run_id=run.id)
    SqlAlchemyFindingRepository(db_session).add(finding)
    db_session.flush()

    is_sql_null, as_text = db_session.execute(
        sa.text(
            "SELECT catalog_tension IS NULL, catalog_tension::text FROM findings WHERE id = :i"
        ),
        {"i": finding.id},
    ).one()

    assert is_sql_null is True
    assert as_text is None


def test_repositorio_hace_round_trip_de_un_catalog_tension_publicado(db_session):
    item = make_item()
    run = make_run()
    SqlAlchemyItemRepository(db_session).add_many([item])
    SqlAlchemyRunRepository(db_session).add(run)
    db_session.flush()
    tension = catalog_tension_v1298_b()
    finding = make_finding(
        item_id=item.id,
        run_id=run.id,
        type=FindingType.CATALOG_TENSION,
        catalog_tension=tension,
        tension_evaluation_id=_seed_evaluation(db_session, item.id),
    )
    finding.publish(confidence=0.8, at=PUBLISHED_AT)
    repo = SqlAlchemyFindingRepository(db_session)
    repo.add(finding)
    db_session.flush()
    db_session.expire_all()

    loaded = repo.get_published(finding.id)

    assert loaded == finding
    assert loaded is not None and loaded.catalog_tension == tension
    assert [f.id for f in repo.published_page(limit=10, offset=0)] == [finding.id]


def test_nombres_de_los_check_de_findings_en_pg_constraint(db_session):
    names = set(
        db_session.execute(
            sa.text(
                "SELECT conname FROM pg_constraint "
                "WHERE conrelid = 'findings'::regclass AND contype = 'c'"
            )
        ).scalars()
    )

    assert {"ck_findings_finding_type", "ck_findings_catalog_tension_iff_type"} <= names


def test_repositorio_hace_round_trip_de_tension_evaluation_id_en_catalog_tension(db_session):
    item = make_item()
    run = make_run()
    SqlAlchemyItemRepository(db_session).add_many([item])
    SqlAlchemyRunRepository(db_session).add(run)
    db_session.flush()
    evaluation_id = uuid.uuid4()
    reading_id = uuid.uuid4()
    db_session.execute(
        sa.text(
            "INSERT INTO readings (id, item_id, summary, objects, claims, interest_score, "
            "tokens_in, tokens_out, model) VALUES (:id, :item, 's', ARRAY[]::text[], "
            "ARRAY[]::text[], 4, 1, 1, 'm')"
        ),
        {"id": reading_id, "item": item.id},
    )
    db_session.execute(
        sa.text(
            "INSERT INTO tension_evaluation (id, reading_id, item_id, planet_name, parameter, "
            "status, detail, first_evaluated_at, evaluated_at) VALUES (:id, :reading, :item, "
            "'p', 'mass', 'evaluated', CAST('{}' AS jsonb), :now, :now)"
        ),
        {"id": evaluation_id, "reading": reading_id, "item": item.id, "now": PUBLISHED_AT},
    )
    finding = make_finding(
        item_id=item.id,
        run_id=run.id,
        type=FindingType.CATALOG_TENSION,
        catalog_tension=catalog_tension_v1298_b(),
        tension_evaluation_id=evaluation_id,
    )
    finding.publish(confidence=0.8, at=PUBLISHED_AT)
    repo = SqlAlchemyFindingRepository(db_session)
    repo.add(finding)
    db_session.flush()
    db_session.expire_all()

    loaded = repo.get_published(finding.id)

    assert loaded is not None and loaded.tension_evaluation_id == evaluation_id
