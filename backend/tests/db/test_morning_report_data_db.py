"""T105: `started_between`, `calls_for_run` y `SqlMorningReportData` contra PostgreSQL."""

from __future__ import annotations

from datetime import timedelta
from zoneinfo import ZoneInfo

from factories import aware, make_agent_call, make_run

from nocturna.domain.entities import AgentCallStatus, RunStatus
from nocturna.infrastructure.db.morning_report_data import SqlMorningReportData
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyRunRepository,
)


def test_started_between_filtra_y_ordena_ascendente(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    finish = {"status": RunStatus.COMPLETED, "finished_at": aware(10_000)}
    early = make_run(started_at=aware(10), **finish)
    middle = make_run(started_at=aware(500), **finish)
    late = make_run(started_at=aware(9_000), **finish)
    for run in (late, early, middle):
        runs.add(run)
    db_session.flush()

    found = runs.started_between(aware(0), aware(600))

    assert [r.id for r in found] == [early.id, middle.id]
    assert runs.started_between(aware(9_001), aware(9_999)) == []


def test_calls_for_run_devuelve_todas_las_llamadas_de_cualquier_estado(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    run, other = make_run(), make_run(status=RunStatus.COMPLETED, finished_at=aware(5))
    runs.add(run)
    runs.add(other)
    db_session.flush()
    calls = SqlAlchemyAgentCallRepository(db_session)
    calls.add(make_agent_call(run.id))
    calls.add(make_agent_call(run.id, status=AgentCallStatus.ERROR, tokens_in=0, tokens_out=0))
    calls.add(make_agent_call(other.id))
    db_session.flush()

    found = calls.calls_for_run(run.id)

    assert sorted(c.status.value for c in found) == ["error", "ok"]


def test_sql_morning_report_data_sin_snapshots_ni_resumen(db_session):
    data = SqlMorningReportData(db_session, ZoneInfo("Europe/Madrid"))
    run = make_run()
    SqlAlchemyRunRepository(db_session).add(run)
    db_session.flush()

    assert data.last_snapshot() is None
    assert data.latest_digest() is None
    assert [r.id for r in data.started_between(aware(0) - timedelta(days=1), aware(1))] == [run.id]
    assert data.tokens_used_for_run(run.id) == 0


def test_tokens_used_for_run_suma_entrada_y_salida_de_todas_las_llamadas_del_run(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    run, other = make_run(), make_run(status=RunStatus.COMPLETED, finished_at=aware(5))
    runs.add(run)
    runs.add(other)
    db_session.flush()
    calls = SqlAlchemyAgentCallRepository(db_session)
    calls.add(make_agent_call(run.id))
    calls.add(make_agent_call(run.id, status=AgentCallStatus.ERROR, tokens_in=7, tokens_out=0))
    calls.add(make_agent_call(other.id))
    db_session.flush()

    data = SqlMorningReportData(db_session, ZoneInfo("Europe/Madrid"))

    assert data.tokens_used_for_run(run.id) == 157
