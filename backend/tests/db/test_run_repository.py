"""Tests de `SqlAlchemyRunRepository` contra PostgreSQL."""

from __future__ import annotations

from factories import make_run

from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.db.repositories import SqlAlchemyRunRepository


def test_current_devuelve_none_sin_runs(db_session):
    repo = SqlAlchemyRunRepository(db_session)

    assert repo.current() is None


def test_current_devuelve_el_running_cuando_lo_hay(db_session):
    repo = SqlAlchemyRunRepository(db_session)
    run = make_run(status=RunStatus.RUNNING)
    repo.add(run)
    db_session.flush()

    current = repo.current()

    assert current is not None
    assert current.id == run.id
    assert current.status == RunStatus.RUNNING


def test_current_devuelve_none_cuando_el_unico_run_esta_cerrado(db_session):
    repo = SqlAlchemyRunRepository(db_session)
    run = make_run()
    run.finish(RunStatus.COMPLETED, at=run.started_at)
    repo.add(run)
    db_session.flush()

    assert repo.current() is None
