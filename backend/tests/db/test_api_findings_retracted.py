"""T100: un hallazgo retirado sale de la lista pero su detalle responde con la nota."""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime

import httpx
import pytest
from sqlalchemy.orm import Session
from test_api_findings import _seed_published_finding

from nocturna.api.app import create_app
from nocturna.api.deps import get_session
from nocturna.infrastructure.db.repositories import SqlAlchemyFindingRepository

RETRACTED_AT = datetime(2026, 3, 9, 8, 0, tzinfo=UTC)


@pytest.fixture
async def client(db_session: Session) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = create_app()

    def _override_get_session() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def _retract(db_session: Session, finding, note: str) -> None:
    finding.retract(RETRACTED_AT, note)
    SqlAlchemyFindingRepository(db_session).save_retraction(finding)
    db_session.flush()


@pytest.mark.anyio
async def test_el_detalle_de_un_retirado_responde_200_con_la_nota(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    finding = _seed_published_finding(db_session, title="Se retira")
    _retract(db_session, finding, "Confundía una masa mínima con una masa.")

    response = await client.get(f"/findings/{finding.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["retraction_note"] == "Confundía una masa mínima con una masa."
    assert body["retracted_at"].startswith("2026-03-09T08:00:00")


@pytest.mark.anyio
async def test_el_detalle_de_un_no_retirado_lleva_los_campos_a_null(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    finding = _seed_published_finding(db_session, title="Sigue vigente")

    body = (await client.get(f"/findings/{finding.id}")).json()

    assert body["retracted_at"] is None and body["retraction_note"] is None


@pytest.mark.anyio
async def test_la_lista_no_incluye_retirados_ni_los_cuenta(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    kept = _seed_published_finding(db_session, title="vigente")
    gone = _seed_published_finding(db_session, title="retirado")
    _retract(db_session, gone, "nota")

    body = (await client.get("/findings")).json()

    assert [item["id"] for item in body["items"]] == [str(kept.id)]
    assert body["total"] == 1
    assert set(body["items"][0]) == {"id", "title", "published_at", "type", "level_curious"}
