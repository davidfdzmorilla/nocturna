"""T85.e: API de lectura de `solution_tension` contra `nocturna_test` (sin Claude).

Sin la fixture `client` ni commits sobre `db_session`: la app usa sesiones propias de
`db_session_factory`.
"""

from __future__ import annotations

from collections.abc import Generator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from factories import make_finding
from helpers.solution_tension_smoke import seed_hd202206c_solution_tension
from sqlalchemy.orm import Session

from nocturna.api.app import create_app
from nocturna.api.deps import get_session
from nocturna.domain.entities import (
    FindingType,
    SolutionComparison,
    SolutionTensionLabel,
)
from nocturna.domain.solution_tension import solution_tension_data_from
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.repositories import SqlAlchemyFindingRepository
from nocturna.infrastructure.db.session import unit_of_work

pytestmark = pytest.mark.anyio

NOW = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)
SOLUTION_KEYS = {"reference", "value", "err_plus", "err_minus", "unit", "is_default", "arxiv_id"}
DETAIL_KEYS = {
    "planet_name",
    "parameter",
    "archive_url",
    "threshold_sigma",
    "sigma",
    "reference_role",
    "labels",
    "new",
    "reference",
    "others",
    "new_releasedate",
}
FORBIDDEN = {"solution_key", "soltype", "pl_pubdate", "ttv_flag", "period_check", "computed_at"}


def _walk(node, path=""):
    """Rutas de todas las claves de un JSON, a cualquier profundidad."""
    if isinstance(node, dict):
        for key, value in node.items():
            yield f"{path}/{key}", key
            yield from _walk(value, f"{path}/{key}")
    elif isinstance(node, list):
        for value in node:
            yield from _walk(value, path + "[]")


def _seed(db_session_factory, *, published: bool):
    config = load_pipeline_config(Path(__file__).resolve().parents[3] / "config" / "pipeline.toml")
    seeded = seed_hd202206c_solution_tension(db_session_factory, config, run_budget_tokens=26_000)
    data = solution_tension_data_from(seeded.mass, threshold_sigma=3.0, archive_url="https://x/y")
    data = replace(
        data,
        labels=(SolutionTensionLabel.MODEL_DEPENDENT,),
        others=(SolutionComparison(data.reference, 4.5), SolutionComparison(data.reference, 6.5)),
    )
    finding = make_finding(
        seeded.item.id,
        seeded.run_id,
        type=FindingType.SOLUTION_TENSION,
        solution_tension=data,
        solution_tension_id=seeded.mass.id,
    )
    if published:
        finding.publish(0.7, NOW)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyFindingRepository(session).add(finding)
    return finding


def _app(db_session_factory):
    app = create_app()

    def _override() -> Generator[Session, None, None]:
        with db_session_factory() as own_session:
            yield own_session

    app.dependency_overrides[get_session] = _override
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")


async def test_claves_exactas_a_cualquier_profundidad_y_nunca_las_prohibidas(
    db_session_factory,
) -> None:
    finding = _seed(db_session_factory, published=True)
    async with _app(db_session_factory) as c:
        response = await c.get(f"/findings/{finding.id}")

    assert response.status_code == 200
    detail = response.json()["solution_tension"]
    assert set(detail) == DETAIL_KEYS
    assert set(detail["new"]) == SOLUTION_KEYS == set(detail["reference"])
    assert len(detail["others"]) == 2
    for other in detail["others"]:
        assert set(other) == {"solution", "sigma"} and set(other["solution"]) == SOLUTION_KEYS
    assert detail["labels"] == ["model_dependent"]
    leaked = {key for _, key in _walk(response.json())} & FORBIDDEN
    assert leaked == set()
    assert FORBIDDEN.isdisjoint({key for _, key in _walk(response.json())})


async def test_solution_tension_no_publicado_es_404_indistinguible_de_uno_inexistente(
    db_session_factory,
) -> None:
    from uuid import uuid4

    finding = _seed(db_session_factory, published=False)
    async with _app(db_session_factory) as c:
        unpublished = await c.get(f"/findings/{finding.id}")
        missing = await c.get(f"/findings/{uuid4()}")
        listing = await c.get("/findings", params={"type": "solution_tension"})

    assert unpublished.status_code == missing.status_code == 404
    assert unpublished.json() == missing.json()
    assert listing.status_code == 200 and listing.json()["total"] == 0


async def test_listado_por_tipo_solution_tension_devuelve_el_publicado_con_su_tipo(
    db_session_factory,
) -> None:
    finding = _seed(db_session_factory, published=True)
    async with _app(db_session_factory) as c:
        listing = await c.get("/findings", params={"type": "solution_tension"})
        other = await c.get("/findings", params={"type": "catalog_tension"})

    assert listing.status_code == 200 and listing.json()["total"] == 1
    assert listing.json()["items"][0]["id"] == str(finding.id)
    assert other.json()["total"] == 0
    assert FORBIDDEN.isdisjoint({key for _, key in _walk(listing.json())})
