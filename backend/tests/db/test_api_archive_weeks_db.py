"""`GET /archive/weeks` y `/archive/weeks/{week}` (T84) contra PostgreSQL.

Complementa `test_archive_digest_db.py` (humo). Mismo montaje que `test_api_findings.py`:
`httpx.ASGITransport` y `dependency_overrides`.
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Generator
from zoneinfo import ZoneInfo

import httpx
import pytest
from sqlalchemy.orm import Session
from test_api_schemas import all_keys
from test_archive_repository import save, snapshot, solution

from nocturna.api.app import create_app
from nocturna.api.deps import get_digest_timezone, get_session
from nocturna.domain.archive import SnapshotKind
from nocturna.infrastructure.db.repositories import SqlAlchemyArchiveRepository

_TZ = ZoneInfo("Europe/Madrid")
_WEEK = "2026-W40"
_INTERNAL_KEYS = {
    "solution_key",
    "old_solution_key",
    "new_solution_key",
    "old_key",
    "new_key",
    "snapshot_id",
    "payload_sha256",
    "ref_key",
    "pl_refname",
    "id",
}


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


def _seed(session: Session) -> list[str]:
    """Devuelve las `solution_key` sembradas (no deben aparecer en ninguna respuesta)."""
    repo = SqlAlchemyArchiveRepository(session)
    a = solution("A b", "Ref A", is_default=True, mass=1.0)
    gone = solution("G b", "Ref G", is_default=True)
    save(repo, snapshot(rows=2), [a, gone])
    a2 = solution("A b", "Ref A2", is_default=True, mass=3.0)
    n = solution("N b", "Ref N", is_default=True)
    save(
        repo,
        snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=1),
        [a2, n],
        removal_scope=frozenset({"A b", "N b", "G b"}),
    )
    return [s.solution_key for s in (a, a2, gone, n)]


@pytest.mark.anyio
async def test_listado_vacio_sin_snapshots(client):
    response = await client.get("/archive/weeks")
    assert response.status_code == 200
    assert response.json() == {"weeks": []}


@pytest.mark.anyio
async def test_listado_con_recuentos_y_claves_exactas(client, db_session):
    _seed(db_session)
    body = (await client.get("/archive/weeks")).json()
    assert set(body) == {"weeks"}
    (week,) = body["weeks"]
    assert week == {
        "week": _WEEK,
        "snapshots": 2,
        "changed": 1,
        "new_planet": 1,
        "regained": 0,
        "lost": 1,
    }


@pytest.mark.anyio
async def test_listado_mas_reciente_primero(client, db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    a = solution("A b", "R1", is_default=True)
    save(repo, snapshot(), [a])
    save(
        repo, snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=8), [a], removal_scope=frozenset()
    )
    weeks = [w["week"] for w in (await client.get("/archive/weeks")).json()["weeks"]]
    assert weeks == ["2026-W41", "2026-W40"]


@pytest.mark.anyio
async def test_detalle_contenido_orden_y_unidades(client, db_session):
    _seed(db_session)
    response = await client.get(f"/archive/weeks/{_WEEK}")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"week", "snapshots", "entries"}
    assert (body["week"], body["snapshots"]) == (_WEEK, 2)
    assert [(e["kind"], e["pl_name"]) for e in body["entries"]] == [
        ("changed", "A b"),
        ("new_planet", "N b"),
        ("lost", "G b"),
    ]
    changed, new, lost = body["entries"]
    assert changed["old"]["reference"] == "Ref A" and changed["new"]["reference"] == "Ref A2"
    (change,) = changed["parameter_changes"]
    assert change["parameter"] == "mass"
    assert change["old"]["value"] == 1.0 and change["new"]["value"] == 3.0
    assert change["old"]["unit"] == "M_earth"
    assert change["old"]["err_minus"] >= 0  # errores en valor absoluto
    assert new["old"] is None and new["new"]["reference"] == "Ref N"
    assert lost["new"] is None and lost["old"]["reference"] == "Ref G"
    assert new["parameter_changes"] == [] and lost["parameter_changes"] == []
    for entry in body["entries"]:
        assert entry["planet_url"].startswith("https://")
        assert entry["detected_at"].endswith(("Z", "+00:00"))


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["/archive/weeks", f"/archive/weeks/{_WEEK}"])
async def test_ningun_cuerpo_contiene_claves_internas_a_ningun_nivel(client, db_session, path):
    keys = _seed(db_session)
    response = await client.get(path)
    assert response.status_code == 200
    assert all_keys(response.json()).isdisjoint(_INTERNAL_KEYS), all_keys(response.json())
    for key in keys:  # tampoco como valor
        assert key not in response.text
    assert "solution_key" not in response.text


@pytest.mark.anyio
@pytest.mark.parametrize("bad", ["basura", "2026-41", "2026-W5", "2026-W00", "2027-W53", "2026w40"])
async def test_formato_invalido_es_422_sin_filtrar_claves(client, db_session, bad):
    _seed(db_session)
    response = await client.get(f"/archive/weeks/{bad}")
    assert response.status_code == 422
    assert all_keys(response.json()).isdisjoint(_INTERNAL_KEYS)


@pytest.mark.anyio
@pytest.mark.parametrize("week", ["2026-W20", "2026-W41", "2026-W53"])
async def test_semana_valida_sin_snapshot_es_404(client, db_session, week):
    _seed(db_session)
    response = await client.get(f"/archive/weeks/{week}")
    assert response.status_code == 404
    assert response.json() == {"detail": "week not found"}


@pytest.mark.anyio
async def test_404_en_base_vacia(client):
    assert (await client.get(f"/archive/weeks/{_WEEK}")).status_code == 404


@pytest.mark.anyio
@pytest.mark.parametrize("method", ["post", "put", "patch", "delete"])
@pytest.mark.parametrize("path", ["/archive/weeks", f"/archive/weeks/{_WEEK}"])
async def test_las_rutas_son_de_solo_lectura(client, method, path):
    response = await getattr(client, method)(path)
    assert response.status_code == 405


def test_openapi_expone_solo_get_en_las_rutas_nuevas_y_ninguna_clave_interna():
    spec = create_app().openapi()
    for path in ("/archive/weeks", "/archive/weeks/{week}"):
        assert set(spec["paths"][path]) == {"get"}
    schemas = spec["components"]["schemas"]
    for name in ("DigestWeekOut", "DigestWeeksResponse", "WeeklyDigestOut", "DigestEntryOut"):
        assert name in schemas
    text = json.dumps(spec)
    for forbidden in ("solution_key", "snapshot_id", "payload_sha256", "old_solution_key"):
        assert forbidden not in text
