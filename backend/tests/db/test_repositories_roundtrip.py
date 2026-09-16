"""Round-trip de las cinco entidades a través de los repositorios, contra PostgreSQL.

Complementa `tests/test_mappers.py` (puro, sin base de datos): aquí el
round-trip pasa por `session.add`/`flush`/`get` de verdad, así que también
comprueba que las columnas de la base (arrays, timestamps con zona horaria,
enums) sobreviven una vuelta completa por PostgreSQL, no solo por Python.
"""

from __future__ import annotations

import sqlalchemy as sa
from factories import make_agent_call, make_finding, make_item, make_reading, make_run

from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)


def test_item_round_trip_por_el_repositorio(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    item = make_item()

    repo.add_many([item])
    db_session.flush()
    rehydrated = repo.get(item.id)

    assert rehydrated == item
    assert rehydrated.id == item.id


def test_item_categories_rehidratado_no_comparte_objeto_con_la_fila(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    item = make_item()
    repo.add_many([item])
    db_session.flush()

    first = repo.get(item.id)
    second = repo.get(item.id)

    assert first.categories == second.categories
    assert first.categories is not second.categories


def test_run_round_trip_por_el_repositorio(db_session):
    repo = SqlAlchemyRunRepository(db_session)
    run = make_run()

    repo.add(run)
    db_session.flush()
    rehydrated = repo.get(run.id)

    assert rehydrated == run
    assert rehydrated.id == run.id


def test_reading_round_trip_por_el_repositorio(db_session):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    item = make_item()
    items.add_many([item])
    db_session.flush()
    reading = make_reading(item_id=item.id)

    readings.add(reading)
    db_session.flush()
    rehydrated = readings.get_for_item(item.id)

    assert rehydrated == reading
    assert rehydrated.id == reading.id
    assert isinstance(rehydrated.objects, tuple)
    assert isinstance(rehydrated.claims, tuple)


def test_finding_round_trip_por_el_repositorio(db_session):
    items = SqlAlchemyItemRepository(db_session)
    runs = SqlAlchemyRunRepository(db_session)
    findings = SqlAlchemyFindingRepository(db_session)
    item = make_item()
    run = make_run()
    items.add_many([item])
    runs.add(run)
    db_session.flush()
    finding = make_finding(item_id=item.id, run_id=run.id)

    findings.add(finding)
    db_session.flush()
    [rehydrated] = findings.unpublished_for_run(run.id)

    assert rehydrated == finding
    assert rehydrated.id == finding.id


def test_agent_call_round_trip_via_sql_directa(db_session):
    # AgentCallRepository no expone un `get`; se comprueba el round-trip
    # leyendo la fila directamente, igual que hace `tokens_used_for_run`.
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run()
    runs.add(run)
    db_session.flush()
    call = make_agent_call(run_id=run.id)

    agent_calls.add(call)
    db_session.flush()

    row = db_session.execute(
        sa.text("SELECT tokens_in, tokens_out, agent, status FROM agent_calls WHERE id = :id"),
        {"id": call.id},
    ).one()

    assert row.tokens_in == call.tokens_in
    assert row.tokens_out == call.tokens_out
    assert row.agent == call.agent.value
    assert row.status == call.status.value


def test_datetimes_rehidratados_desde_postgres_son_aware_y_el_mismo_instante(db_session):
    repo = SqlAlchemyItemRepository(db_session)
    item = make_item()
    repo.add_many([item])
    db_session.flush()

    rehydrated = repo.get(item.id)

    assert rehydrated.published_at.tzinfo is not None
    assert rehydrated.fetched_at.tzinfo is not None
    assert rehydrated.published_at == item.published_at
    assert rehydrated.fetched_at == item.fetched_at


def test_item_discarded_se_rehidrata_desde_postgres_sin_lanzar(db_session):
    from nocturna.domain.entities import ItemStatus
    from nocturna.domain.errors import GuardedFieldAssignment

    repo = SqlAlchemyItemRepository(db_session)
    item = make_item(status=ItemStatus.DISCARDED)
    repo.add_many([item])
    db_session.flush()

    rehydrated = repo.get(item.id)

    assert rehydrated.status == ItemStatus.DISCARDED
    try:
        rehydrated.status = ItemStatus.NEW
    except GuardedFieldAssignment:
        pass
    else:
        raise AssertionError("se esperaba GuardedFieldAssignment tras rehidratar desde Postgres")


def test_run_completed_se_rehidrata_desde_postgres_sin_lanzar(db_session):
    repo = SqlAlchemyRunRepository(db_session)
    run = make_run()
    run.finish(RunStatus.COMPLETED, at=run.started_at)
    repo.add(run)
    db_session.flush()

    rehydrated = repo.get(run.id)

    assert rehydrated.status == RunStatus.COMPLETED
