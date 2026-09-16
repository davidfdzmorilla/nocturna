"""Tests de `SqlAlchemyFindingRepository` contra PostgreSQL."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa
from factories import make_finding, make_item, make_run

from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)

PUBLISHED_AT = datetime(2026, 3, 2, tzinfo=UTC)


def _seed_item_and_run(db_session, run_status: RunStatus = RunStatus.RUNNING):
    items = SqlAlchemyItemRepository(db_session)
    runs = SqlAlchemyRunRepository(db_session)
    item = make_item()
    run = make_run()
    if run_status != RunStatus.RUNNING:
        run.finish(run_status, at=run.started_at)
    items.add_many([item])
    runs.add(run)
    db_session.flush()
    return item, run


def test_unpublished_for_run_devuelve_solo_los_del_run_indicado_y_no_publicados(db_session):
    findings = SqlAlchemyFindingRepository(db_session)
    item, run_a = _seed_item_and_run(db_session)
    # El índice único parcial de `runs` solo permite un Run `running` a la
    # vez; el segundo run de este test se crea ya cerrado.
    _, run_b = _seed_item_and_run(db_session, run_status=RunStatus.COMPLETED)

    unpublished_a = make_finding(item_id=item.id, run_id=run_a.id, title="candidato A")
    published_a = make_finding(item_id=item.id, run_id=run_a.id, title="ya publicado en A")
    published_a.publish(confidence=0.9, at=PUBLISHED_AT)
    other_run_b = make_finding(item_id=item.id, run_id=run_b.id, title="candidato de otro run")

    findings.add(unpublished_a)
    findings.add(published_a)
    findings.add(other_run_b)
    db_session.flush()

    result = findings.unpublished_for_run(run_a.id)

    assert [f.id for f in result] == [unpublished_a.id]


def test_save_de_finding_tras_publish_persiste_confidence_y_published_at(db_session):
    findings = SqlAlchemyFindingRepository(db_session)
    item, run = _seed_item_and_run(db_session)
    finding = make_finding(item_id=item.id, run_id=run.id)
    findings.add(finding)
    db_session.flush()

    finding.publish(confidence=0.42, at=PUBLISHED_AT)
    findings.save(finding)
    db_session.flush()

    # `FindingRepository` no expone un `get` por id; se comprueba la fila
    # directamente, igual que ya no aparece entre los pendientes del run.
    row = db_session.execute(
        sa.text("SELECT confidence, published_at FROM findings WHERE id = :id"),
        {"id": finding.id},
    ).one()
    assert row.confidence == 0.42
    assert row.published_at == PUBLISHED_AT
    assert finding.id not in {f.id for f in findings.unpublished_for_run(run.id)}


def test_save_de_finding_inexistente_lanza_lookup_error(db_session):
    findings = SqlAlchemyFindingRepository(db_session)
    item, run = _seed_item_and_run(db_session)
    finding = make_finding(item_id=item.id, run_id=run.id)

    try:
        findings.save(finding)
    except LookupError:
        pass
    else:
        raise AssertionError("se esperaba LookupError")
