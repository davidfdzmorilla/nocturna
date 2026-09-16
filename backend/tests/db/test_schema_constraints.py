"""Tests de las restricciones de esquema que solo se ven insertando SQL crudo.

Deliberado: estos tests **no** construyen las filas inválidas a través del
ORM ni de las entidades de dominio, porque el propio `sa.Enum` de
SQLAlchemy validaría el valor en Python antes de llegar a la base de datos,
y lo que se quiere probar es el `CHECK` de PostgreSQL (`create_constraint`
en `models.py`), no la validación de cliente. Se inserta con `sa.text(...)`
y parámetros, contra `db_session` (rollback garantizado por test).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from factories import aware
from sqlalchemy.exc import IntegrityError

from nocturna.infrastructure.db.models import ItemRow, RunRow

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _insert_raw(session, table: str, values: dict[str, object]) -> None:
    columns = ", ".join(values)
    placeholders = ", ".join(f":{name}" for name in values)
    session.execute(sa.text(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})"), values)


def _valid_item_row(**overrides: object) -> dict[str, object]:
    row = {
        "id": uuid.uuid4(),
        "source": "arxiv",
        "external_id": f"2601.{uuid.uuid4().hex[:5]}",
        "title": "t",
        "abstract": "a",
        "categories": ["astro-ph.EP"],
        "published_at": NOW,
        "fetched_at": NOW,
        "status": "new",
    }
    row.update(overrides)
    return row


def _valid_run_row(**overrides: object) -> dict[str, object]:
    row = {
        "id": uuid.uuid4(),
        "started_at": NOW,
        "finished_at": None,
        "status": "running",
        "budget_tokens": 300_000,
        "tokens_used": 0,
        "items_fetched": 0,
        "items_read": 0,
        "findings_published": 0,
        "notes": "",
    }
    row.update(overrides)
    return row


def _seed_item(session) -> uuid.UUID:
    row = ItemRow(**_valid_item_row())
    session.add(row)
    session.flush()
    return row.id


def _seed_run(session, status: str = "completed", finished_at=NOW) -> uuid.UUID:
    row = RunRow(**_valid_run_row(status=status, finished_at=finished_at))
    session.add(row)
    session.flush()
    return row.id


def _valid_finding_row(item_id: uuid.UUID, run_id: uuid.UUID, **overrides: object) -> dict:
    row = {
        "id": uuid.uuid4(),
        "item_id": item_id,
        "run_id": run_id,
        "type": "paper_explained",
        "title": "t",
        "level_curious": "c",
        "level_amateur": "am",
        "level_technical": "te",
        "confidence": None,
        "published_at": None,
    }
    row.update(overrides)
    return row


def _valid_agent_call_row(run_id: uuid.UUID, **overrides: object) -> dict:
    row = {
        "id": uuid.uuid4(),
        "run_id": run_id,
        "item_id": None,
        "agent": "reader",
        "model": "m",
        "tokens_in": 1,
        "tokens_out": 1,
        "duration_ms": 1,
        "status": "ok",
    }
    row.update(overrides)
    return row


def _valid_reading_row(item_id: uuid.UUID, **overrides: object) -> dict:
    row = {
        "id": uuid.uuid4(),
        "item_id": item_id,
        "summary": "s",
        "objects": ["Betelgeuse"],
        "claims": ["c"],
        "interest_score": 3,
        "tokens_in": 1,
        "tokens_out": 1,
        "model": "m",
    }
    row.update(overrides)
    return row


def test_items_status_fuera_de_conjunto_falla(db_session):
    row = _valid_item_row(status="bogus")

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "items", row)


def test_runs_status_fuera_de_conjunto_falla(db_session):
    row = _valid_run_row(status="bogus", finished_at=NOW)

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "runs", row)


def test_findings_type_fuera_de_conjunto_falla(db_session):
    item_id = _seed_item(db_session)
    run_id = _seed_run(db_session)
    row = _valid_finding_row(item_id, run_id, type="bogus")

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "findings", row)


def test_agent_calls_agent_fuera_de_conjunto_falla(db_session):
    run_id = _seed_run(db_session)
    row = _valid_agent_call_row(run_id, agent="bogus")

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "agent_calls", row)


def test_agent_calls_status_fuera_de_conjunto_falla(db_session):
    run_id = _seed_run(db_session)
    row = _valid_agent_call_row(run_id, status="bogus")

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "agent_calls", row)


def test_la_columna_guarda_el_valor_del_enum_no_el_nombre_del_miembro(db_session):
    item_row = _valid_item_row(status="new")
    _insert_raw(db_session, "items", item_row)
    run_id = _seed_run(db_session)
    call_row = _valid_agent_call_row(run_id, agent="reader")
    _insert_raw(db_session, "agent_calls", call_row)
    db_session.flush()

    item_status = db_session.execute(
        sa.text("SELECT status FROM items WHERE id = :id"), {"id": item_row["id"]}
    ).scalar_one()
    agent_value = db_session.execute(
        sa.text("SELECT agent FROM agent_calls WHERE id = :id"), {"id": call_row["id"]}
    ).scalar_one()

    # El valor persistido es "new"/"reader" (StrEnum.value), nunca "NEW"/"READER"
    # (el nombre del miembro Python), que es lo que espera el resto del dominio
    # y la API de lectura (T50).
    assert item_status == "new"
    assert agent_value == "reader"


def test_source_external_id_duplicado_falla(db_session):
    item_row = _valid_item_row(source="arxiv", external_id="2601.99999")
    _insert_raw(db_session, "items", item_row)
    db_session.flush()

    duplicate = _valid_item_row(source="arxiv", external_id="2601.99999")

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "items", duplicate)


def test_dos_runs_en_running_falla(db_session):
    _seed_run(db_session, status="running", finished_at=None)

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "runs", _valid_run_row(status="running", finished_at=None))


def test_dos_readings_para_el_mismo_item_falla(db_session):
    item_id = _seed_item(db_session)
    _insert_raw(db_session, "readings", _valid_reading_row(item_id))
    db_session.flush()

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "readings", _valid_reading_row(item_id))


def test_finding_con_confidence_y_sin_published_at_falla(db_session):
    item_id = _seed_item(db_session)
    run_id = _seed_run(db_session)
    row = _valid_finding_row(item_id, run_id, confidence=0.8, published_at=None)

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "findings", row)


def test_finding_con_published_at_y_sin_confidence_falla(db_session):
    item_id = _seed_item(db_session)
    run_id = _seed_run(db_session)
    row = _valid_finding_row(item_id, run_id, confidence=None, published_at=aware())

    with pytest.raises(IntegrityError):
        _insert_raw(db_session, "findings", row)
