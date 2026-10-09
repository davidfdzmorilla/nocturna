"""Pérdida de default (T84), lector del resumen semanal y API, contra PostgreSQL."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session
from test_archive_repository import save, snapshot, solution

from nocturna.api.app import create_app
from nocturna.api.deps import get_digest_timezone, get_session
from nocturna.application.use_cases.archive_digest import GetWeeklyDigest, ListDigestWeeks
from nocturna.domain.archive import SnapshotKind
from nocturna.domain.archive_digest import TransitionKind
from nocturna.infrastructure.db.models import ArchiveDefaultChangeRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveDigestReader,
    SqlAlchemyArchiveRepository,
)

_TZ = ZoneInfo("Europe/Madrid")
_WEEK = "2026-W40"  # _T0 = 2026-10-01


def _seed(session: Session) -> None:
    repo = SqlAlchemyArchiveRepository(session)
    a = solution("A b", "R1", is_default=True)
    a2 = solution("A b", "R2", is_default=True, mass=3.0)
    gone = solution("G b", "R1", is_default=True)
    save(repo, snapshot(rows=2), [a, gone])
    save(
        repo,
        snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=1),
        [a2, solution("N b", "R9", is_default=True)],
        removal_scope=frozenset({"A b", "N b", "G b"}),
    )


def test_perdida_de_default_escribe_fila_con_old_y_sin_new(db_session):
    _seed(db_session)
    rows = {
        r.pl_name: r for r in db_session.execute(sa.select(ArchiveDefaultChangeRow)).scalars().all()
    }
    lost = rows["G b"]
    assert lost.old_solution_key == solution("G b", "R1", is_default=True).solution_key
    assert lost.new_solution_key is None
    assert rows["N b"].old_solution_key is None


def test_lector_y_casos_de_uso(db_session):
    _seed(db_session)
    reader = SqlAlchemyArchiveDigestReader(db_session)
    assert reader.snapshot_weeks(_TZ) == [(_WEEK, 2)]
    (summary,) = ListDigestWeeks(reader, _TZ)()
    assert summary.counts[TransitionKind.CHANGED] == 1
    assert summary.counts[TransitionKind.NEW_PLANET] == 1
    assert summary.counts[TransitionKind.LOST] == 1
    assert GetWeeklyDigest(reader, _TZ)("2026-W20") is None
    digest = GetWeeklyDigest(reader, _TZ)(_WEEK)
    assert digest is not None
    assert [(e.kind, e.pl_name) for e in digest.entries] == [
        (TransitionKind.CHANGED, "A b"),
        (TransitionKind.NEW_PLANET, "N b"),
        (TransitionKind.LOST, "G b"),
    ]
    assert digest.entries[0].parameter_changes[0].parameter.value == "mass"


@pytest.fixture
async def client(db_session: Session) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = create_app()

    def _session() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_session] = _session
    app.dependency_overrides[get_digest_timezone] = lambda: _TZ
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as c:
        yield c


@pytest.mark.anyio
async def test_api_semanas_y_detalle(client, db_session):
    _seed(db_session)
    listing = (await client.get("/archive/weeks")).json()
    assert listing["weeks"][0]["week"] == _WEEK
    assert listing["weeks"][0]["lost"] == 1
    detail = await client.get(f"/archive/weeks/{_WEEK}")
    assert detail.status_code == 200
    body = detail.json()
    assert "solution_key" not in detail.text
    assert body["entries"][0]["planet_url"].startswith("https://")
    assert (await client.get("/archive/weeks/2026-W20")).status_code == 404
    assert (await client.get("/archive/weeks/basura")).status_code == 422
