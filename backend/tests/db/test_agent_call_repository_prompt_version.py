"""`SqlAlchemyAgentCallRepository.count_runs_with_prompt_version` contra PostgreSQL (T74)."""

from __future__ import annotations

from factories import aware, make_agent_call, make_run

from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyRunRepository,
)


def test_count_runs_with_prompt_version_cuenta_runs_distintos(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    calls = SqlAlchemyAgentCallRepository(db_session)
    run_a, run_b, run_c = (
        make_run(status=RunStatus.COMPLETED, finished_at=aware(1)) for _ in range(3)
    )
    for run in (run_a, run_b, run_c):
        runs.add(run)
    db_session.flush()
    calls.add(make_agent_call(run_a.id, prompt_version="reader-v3"))
    calls.add(make_agent_call(run_a.id, prompt_version="reader-v3"))
    calls.add(make_agent_call(run_b.id, prompt_version="reader-v3"))
    calls.add(make_agent_call(run_c.id, prompt_version="reader-v2"))
    calls.add(make_agent_call(run_c.id, prompt_version=None))
    db_session.flush()

    assert calls.count_runs_with_prompt_version("reader-v3") == 2
    assert calls.count_runs_with_prompt_version("reader-v2") == 1
    assert calls.count_runs_with_prompt_version("reader-v9") == 0
