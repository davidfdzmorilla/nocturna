"""`SqlAlchemyAgentCallRepository.count_for_item` contra PostgreSQL (T76, D6)."""

from __future__ import annotations

from factories import aware, make_agent_call, make_item, make_run

from nocturna.domain.entities import AgentCallStatus, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)


def test_count_for_item_filtra_por_item_agente_y_estado_y_suma_runs(db_session):
    item_a, item_b = make_item(external_id="a"), make_item(external_id="b")
    SqlAlchemyItemRepository(db_session).add_many([item_a, item_b])
    runs = SqlAlchemyRunRepository(db_session)
    run_1, run_2 = (make_run(status=RunStatus.COMPLETED, finished_at=aware(1)) for _ in range(2))
    runs.add(run_1)
    runs.add(run_2)
    db_session.flush()
    calls = SqlAlchemyAgentCallRepository(db_session)
    invalid = AgentCallStatus.INVALID_OUTPUT
    for run in (run_1, run_2):
        calls.add(
            make_agent_call(run.id, item_id=item_a.id, agent=AgentRole.WRITER, status=invalid)
        )
    calls.add(make_agent_call(run_1.id, item_id=item_a.id, agent=AgentRole.WRITER))  # ok
    calls.add(make_agent_call(run_1.id, item_id=item_a.id, agent=AgentRole.READER, status=invalid))
    calls.add(make_agent_call(run_1.id, item_id=item_b.id, agent=AgentRole.WRITER, status=invalid))
    calls.add(make_agent_call(run_1.id, item_id=None, agent=AgentRole.WRITER, status=invalid))
    db_session.flush()

    assert calls.count_for_item(item_a.id, AgentRole.WRITER, invalid) == 2
    assert calls.count_for_item(item_a.id, AgentRole.WRITER, AgentCallStatus.OK) == 1
    assert calls.count_for_item(item_a.id, AgentRole.READER, invalid) == 1
    assert calls.count_for_item(item_b.id, AgentRole.WRITER, invalid) == 1
    assert calls.count_for_item(item_b.id, AgentRole.READER, invalid) == 0
