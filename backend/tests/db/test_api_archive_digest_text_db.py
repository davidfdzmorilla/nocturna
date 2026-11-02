"""`GET /archive/weeks/{week}` con `writer_text` (T84.r) contra PostgreSQL.

`writer_text` es `null` sin texto o con fila fallida; con texto redactado es exactamente
`{title, summary}`: nunca `prompt_version`, `run_id` ni `input_sha256`. Solo lectura. Fichero
en español.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from zoneinfo import ZoneInfo

import httpx
import pytest
from factories import make_run
from helpers.archive_digest import digest_text
from sqlalchemy.orm import Session
from test_api_archive_weeks_db import _WEEK, _seed
from test_api_schemas import all_keys

from nocturna.api.app import create_app
from nocturna.api.deps import get_digest_timezone, get_session
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveDigestTextRepository,
    SqlAlchemyRunRepository,
)

_TZ = ZoneInfo("Europe/Madrid")
_SHA = "9" * 64
_FORBIDDEN_KEYS = {"prompt_version", "run_id", "input_sha256", "status", "created_at", "week_text"}


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


def _store(session: Session, **kw):
    run = make_run()
    SqlAlchemyRunRepository(session).add(run)
    text = digest_text(_WEEK, run.id, sha=_SHA, **kw)
    SqlAlchemyArchiveDigestTextRepository(session).add(text)
    session.flush()
    return run.id


@pytest.mark.anyio
async def test_sin_texto_writer_text_es_null(client, db_session):
    _seed(db_session)
    body = (await client.get(f"/archive/weeks/{_WEEK}")).json()
    assert "writer_text" in body and body["writer_text"] is None


@pytest.mark.anyio
async def test_con_fila_fallida_writer_text_es_null(client, db_session):
    _seed(db_session)
    _store(db_session, status="failed")
    response = await client.get(f"/archive/weeks/{_WEEK}")
    assert response.status_code == 200
    assert response.json()["writer_text"] is None


@pytest.mark.anyio
async def test_con_texto_redactado_writer_text_es_exactamente_titulo_y_resumen(client, db_session):
    _seed(db_session)
    _store(db_session, title="Un título", summary="Primer párrafo.\n\nSegundo.")
    body = (await client.get(f"/archive/weeks/{_WEEK}")).json()
    assert body["writer_text"] == {"title": "Un título", "summary": "Primer párrafo.\n\nSegundo."}
    assert set(body) == {"week", "snapshots", "entries", "writer_text"}


@pytest.mark.anyio
async def test_nunca_salen_prompt_version_run_id_ni_hash(client, db_session):
    _seed(db_session)
    run_id = _store(db_session)
    response = await client.get(f"/archive/weeks/{_WEEK}")
    assert all_keys(response.json()).isdisjoint(_FORBIDDEN_KEYS)
    for leaked in ("writer-digest-v1", _SHA, str(run_id), "prompt_version", "input_sha256"):
        assert leaked not in response.text


@pytest.mark.anyio
async def test_el_texto_de_otra_semana_no_aparece(client, db_session):
    _seed(db_session)
    run = make_run()
    SqlAlchemyRunRepository(db_session).add(run)
    SqlAlchemyArchiveDigestTextRepository(db_session).add(digest_text("2026-W41", run.id))
    db_session.flush()
    body = (await client.get(f"/archive/weeks/{_WEEK}")).json()
    assert body["writer_text"] is None


@pytest.mark.anyio
async def test_el_listado_de_semanas_no_cambia_con_un_texto(client, db_session):
    _seed(db_session)
    before = (await client.get("/archive/weeks")).json()
    _store(db_session)
    after = (await client.get("/archive/weeks")).json()
    assert after == before
    assert all_keys(after).isdisjoint(_FORBIDDEN_KEYS | {"writer_text"})


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
async def test_la_ruta_sigue_siendo_de_solo_lectura(client, db_session, method):
    _seed(db_session)
    _store(db_session)
    response = await getattr(client, method)(f"/archive/weeks/{_WEEK}")
    assert response.status_code == 405


@pytest.mark.anyio
async def test_get_no_escribe_en_la_tabla(client, db_session):
    _seed(db_session)
    _store(db_session)
    repo = SqlAlchemyArchiveDigestTextRepository(db_session)
    before = repo.all()
    await client.get(f"/archive/weeks/{_WEEK}")
    db_session.expire_all()
    assert repo.all() == before


def test_openapi_expone_digest_text_out_con_solo_titulo_y_resumen():
    spec = create_app().openapi()
    schemas = spec["components"]["schemas"]
    assert set(schemas["DigestTextOut"]["properties"]) == {"title", "summary"}
    assert "writer_text" in schemas["WeeklyDigestOut"]["properties"]
    names = {name for schema in schemas.values() for name in schema.get("properties", {})}
    assert names.isdisjoint({"prompt_version", "input_sha256", "run_id", "solution_key"})
