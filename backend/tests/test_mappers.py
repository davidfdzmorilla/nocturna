"""Tests de la traducción entidad <-> fila ORM (`infrastructure/db/mappers.py`).

Puros, sin base de datos: construyen entidades de dominio válidas, las
pasan por `*_to_row` y `*_from_row` (sin tocar PostgreSQL; `ItemRow` y
compañía son solo objetos Python fuera de una sesión) y comprueban que el
resultado es una entidad igual a la original. Vive fuera de `tests/db/` a
propósito, así que no se marca con `db` ni necesita el PostgreSQL de
compose levantado.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from nocturna.domain.entities import (
    AgentCall,
    AgentCallStatus,
    Finding,
    FindingType,
    Item,
    ItemStatus,
    Reading,
    Run,
    RunStatus,
)
from nocturna.domain.errors import GuardedFieldAssignment
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.mappers import (
    agent_call_from_row,
    agent_call_to_row,
    finding_from_row,
    finding_to_row,
    item_from_row,
    item_to_row,
    reading_from_row,
    reading_to_row,
    run_from_row,
    run_to_row,
)

AWARE_NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


def _item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "source": "arxiv",
        "external_id": "2603.00001",
        "title": "Título de prueba",
        "abstract": "Abstract de prueba.",
        "categories": ["astro-ph.EP", "astro-ph.GA"],
        "published_at": AWARE_NOW,
        "fetched_at": AWARE_NOW,
        "status": ItemStatus.NEW,
    }
    defaults.update(overrides)
    return Item(**defaults)


def _reading(**overrides: object) -> Reading:
    defaults: dict[str, object] = {
        "item_id": uuid4(),
        "summary": "Resumen",
        "objects": ["Betelgeuse", "M87"],
        "claims": ["Afirmación uno", "Afirmación dos"],
        "interest_score": 4,
        "tokens_in": 1000,
        "tokens_out": 200,
        "model": "claude-sonnet-test",
    }
    defaults.update(overrides)
    return Reading(**defaults)


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "item_id": uuid4(),
        "run_id": uuid4(),
        "type": FindingType.PAPER_EXPLAINED,
        "title": "Título del hallazgo",
        "level_curious": "curioso",
        "level_amateur": "aficionado",
        "level_technical": "técnico",
    }
    defaults.update(overrides)
    return Finding(**defaults)


def _run(**overrides: object) -> Run:
    defaults: dict[str, object] = {
        "started_at": AWARE_NOW,
        "budget_tokens": 300_000,
        "status": RunStatus.RUNNING,
    }
    defaults.update(overrides)
    return Run(**defaults)


def _agent_call(**overrides: object) -> AgentCall:
    defaults: dict[str, object] = {
        "run_id": uuid4(),
        "item_id": uuid4(),
        "agent": AgentRole.READER,
        "model": "claude-sonnet-test",
        "tokens_in": 500,
        "tokens_out": 100,
        "duration_ms": 2200,
        "status": AgentCallStatus.OK,
    }
    defaults.update(overrides)
    return AgentCall(**defaults)


def test_item_round_trip_produce_una_entidad_igual_con_el_mismo_id():
    item = _item()

    rehydrated = item_from_row(item_to_row(item))

    assert rehydrated == item
    assert rehydrated.id == item.id


def test_item_categories_rehidratado_no_comparte_objeto_con_la_fila_orm():
    item = _item()
    row = item_to_row(item)

    rehydrated = item_from_row(row)

    assert rehydrated.categories == row.categories
    assert rehydrated.categories is not row.categories


def test_item_discarded_se_rehidrata_sin_lanzar_y_la_guarda_sigue_activa():
    item = _item(status=ItemStatus.DISCARDED)

    rehydrated = item_from_row(item_to_row(item))

    assert rehydrated.status == ItemStatus.DISCARDED
    try:
        rehydrated.status = ItemStatus.NEW
    except GuardedFieldAssignment:
        pass
    else:
        raise AssertionError("se esperaba GuardedFieldAssignment tras rehidratar")


def test_reading_round_trip_produce_una_entidad_igual_con_el_mismo_id():
    reading = _reading()

    rehydrated = reading_from_row(reading_to_row(reading))

    assert rehydrated == reading
    assert rehydrated.id == reading.id


def test_reading_objects_y_claims_vuelven_como_tupla():
    reading = _reading()

    rehydrated = reading_from_row(reading_to_row(reading))

    assert isinstance(rehydrated.objects, tuple)
    assert isinstance(rehydrated.claims, tuple)


def test_reading_con_objects_y_claims_vacios_sobrevive_el_round_trip():
    reading = _reading(objects=[], claims=[])

    rehydrated = reading_from_row(reading_to_row(reading))

    assert rehydrated.objects == ()
    assert rehydrated.claims == ()


def test_finding_round_trip_produce_una_entidad_igual_con_el_mismo_id():
    finding = _finding()

    rehydrated = finding_from_row(finding_to_row(finding))

    assert rehydrated == finding
    assert rehydrated.id == finding.id


def test_finding_publicado_se_rehidrata_sin_lanzar_y_la_guarda_sigue_activa():
    finding = _finding()
    finding.publish(confidence=0.75, at=AWARE_NOW)

    rehydrated = finding_from_row(finding_to_row(finding))

    assert rehydrated.is_published
    assert rehydrated.confidence == 0.75
    assert rehydrated.published_at == AWARE_NOW
    try:
        rehydrated.confidence = 0.1
    except GuardedFieldAssignment:
        pass
    else:
        raise AssertionError("se esperaba GuardedFieldAssignment tras rehidratar")


def test_run_round_trip_produce_una_entidad_igual_con_el_mismo_id():
    run = _run()

    rehydrated = run_from_row(run_to_row(run))

    assert rehydrated == run
    assert rehydrated.id == run.id


def test_run_completed_se_rehidrata_sin_lanzar_y_la_guarda_sigue_activa():
    run = _run()
    run.finish(RunStatus.COMPLETED, at=AWARE_NOW)

    rehydrated = run_from_row(run_to_row(run))

    assert rehydrated.status == RunStatus.COMPLETED
    assert rehydrated.finished_at == AWARE_NOW
    try:
        rehydrated.status = RunStatus.RUNNING
    except GuardedFieldAssignment:
        pass
    else:
        raise AssertionError("se esperaba GuardedFieldAssignment tras rehidratar")


def test_agent_call_round_trip_produce_una_entidad_igual_con_el_mismo_id():
    call = _agent_call()

    rehydrated = agent_call_from_row(agent_call_to_row(call))

    assert rehydrated == call
    assert rehydrated.id == call.id


def test_todos_los_datetimes_rehidratados_son_aware_y_el_mismo_instante():
    item = _item()
    finding = _finding()
    finding.publish(confidence=0.5, at=AWARE_NOW)
    run = _run()
    run.finish(RunStatus.FAILED, at=AWARE_NOW)

    rehydrated_item = item_from_row(item_to_row(item))
    rehydrated_finding = finding_from_row(finding_to_row(finding))
    rehydrated_run = run_from_row(run_to_row(run))

    for value in (
        rehydrated_item.published_at,
        rehydrated_item.fetched_at,
        rehydrated_finding.published_at,
        rehydrated_run.started_at,
        rehydrated_run.finished_at,
    ):
        assert value is not None
        assert value.tzinfo is not None
        assert value.utcoffset() is not None
        assert value == AWARE_NOW
