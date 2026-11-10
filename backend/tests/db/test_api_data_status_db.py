"""T108 (E): `GET /data-status` y `SqlAlchemyDataFreshnessReader` contra PostgreSQL.

`data_updated_at` = greatest(max(runs.finished_at), max(archive_snapshot.taken_at)). Un Run en
marcha (`finished_at` nulo) no cuenta. Mismo montaje que `test_api_archive_weeks_db.py`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from factories import make_run
from sqlalchemy.orm import Session
from test_archive_repository import save, snapshot

from nocturna.api.app import create_app
from nocturna.api.deps import get_session
from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyDataFreshnessReader,
    SqlAlchemyRunRepository,
)

_BASE = datetime(2026, 11, 1, 0, 0, tzinfo=UTC)


@pytest.fixture
async def client(db_session: Session) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = create_app()

    def _session() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


def _closed_run(session: Session, *, started: datetime, finished: datetime) -> None:
    run = make_run(started_at=started)
    repo = SqlAlchemyRunRepository(session)
    repo.add(run)
    run.finish(RunStatus.COMPLETED, finished)
    repo.save(run)


def _running_run(session: Session, *, started: datetime) -> None:
    SqlAlchemyRunRepository(session).add(make_run(started_at=started))


def _snapshot(session: Session, *, taken_at: datetime) -> None:
    snap = snapshot()
    object.__setattr__(snap, "taken_at", taken_at)
    save(SqlAlchemyArchiveRepository(session), snap, [])


def _last_update(session: Session) -> datetime | None:
    return SqlAlchemyDataFreshnessReader(session).last_update()


def test_con_la_base_vacia_es_none(db_session):
    assert _last_update(db_session) is None


def test_solo_runs_da_el_finished_at_mas_reciente(db_session):
    _closed_run(db_session, started=_BASE, finished=_BASE + timedelta(hours=3))
    _closed_run(
        db_session, started=_BASE + timedelta(days=1), finished=_BASE + timedelta(days=1, hours=2)
    )

    assert _last_update(db_session) == _BASE + timedelta(days=1, hours=2)


def test_solo_snapshot_da_su_taken_at(db_session):
    _snapshot(db_session, taken_at=_BASE + timedelta(days=5))

    assert _last_update(db_session) == _BASE + timedelta(days=5)


def test_gana_el_snapshot_si_es_mas_reciente_que_el_run(db_session):
    _closed_run(db_session, started=_BASE, finished=_BASE + timedelta(hours=3))
    _snapshot(db_session, taken_at=_BASE + timedelta(days=4))

    assert _last_update(db_session) == _BASE + timedelta(days=4)


def test_gana_el_run_si_es_mas_reciente_que_el_snapshot(db_session):
    _snapshot(db_session, taken_at=_BASE + timedelta(days=1))
    _closed_run(
        db_session, started=_BASE + timedelta(days=6), finished=_BASE + timedelta(days=6, hours=3)
    )

    assert _last_update(db_session) == _BASE + timedelta(days=6, hours=3)


def test_un_run_en_marcha_mas_reciente_que_el_cerrado_se_ignora(db_session):
    _closed_run(db_session, started=_BASE, finished=_BASE + timedelta(hours=3))
    _running_run(db_session, started=_BASE + timedelta(days=2))

    assert _last_update(db_session) == _BASE + timedelta(hours=3)


def test_un_run_en_marcha_solo_no_da_fecha(db_session):
    _running_run(db_session, started=_BASE)

    assert _last_update(db_session) is None


def test_la_fecha_devuelta_lleva_zona_horaria(db_session):
    _closed_run(db_session, started=_BASE, finished=_BASE + timedelta(hours=1))

    result = _last_update(db_session)
    assert result is not None and result.utcoffset() == timedelta(0)


@pytest.mark.anyio
async def test_endpoint_con_la_base_vacia_devuelve_null(client):
    response = await client.get("/data-status")

    assert response.status_code == 200
    assert response.json() == {"data_updated_at": None}


@pytest.mark.anyio
async def test_endpoint_devuelve_el_maximo_con_claves_exactas(client, db_session):
    _closed_run(db_session, started=_BASE, finished=_BASE + timedelta(hours=3))
    _snapshot(db_session, taken_at=_BASE + timedelta(days=2))

    response = await client.get("/data-status")

    body = response.json()
    assert set(body) == {"data_updated_at"}
    assert datetime.fromisoformat(body["data_updated_at"]) == _BASE + timedelta(days=2)


@pytest.mark.anyio
async def test_endpoint_ignora_el_run_en_marcha(client, db_session):
    _closed_run(db_session, started=_BASE, finished=_BASE + timedelta(hours=3))
    _running_run(db_session, started=_BASE + timedelta(days=9))

    body = (await client.get("/data-status")).json()

    assert datetime.fromisoformat(body["data_updated_at"]) == _BASE + timedelta(hours=3)


@pytest.mark.anyio
async def test_endpoint_solo_admite_get(client):
    assert (await client.post("/data-status")).status_code == 405
