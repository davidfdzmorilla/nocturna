"""Tests de `api/schemas.py` (T50 paso 3), sin base de datos ni FastAPI en marcha.

Cada esquema se comprueba por el **conjunto exacto** de claves que produce
al serializar, con asertos explícitos de que `confidence`, `run_id` e
`item_id` -- los tres campos que `CLAUDE.md` prohíbe exponer -- no están en
ninguno. El resto de la batería de esta capa (guarda AST de imports, la app
FastAPI levantada de verdad, base de datos) es del `tester`.
"""

from datetime import UTC, datetime
from uuid import uuid4

from nocturna.api.schemas import (
    FindingDetail,
    FindingsPageResponse,
    FindingSummary,
    HealthResponse,
    source_url,
)

_PUBLISHED_AT = datetime(2026, 1, 2, 3, 0, 0, tzinfo=UTC)
_FORBIDDEN_KEYS = {"confidence", "run_id", "item_id"}


def test_finding_summary_has_exactly_expected_keys() -> None:
    summary = FindingSummary(
        id=uuid4(),
        title="Un hallazgo de prueba",
        published_at=_PUBLISHED_AT,
        level_curious="Nivel curioso.",
    )
    dumped = summary.model_dump(mode="json")
    assert set(dumped.keys()) == {"id", "title", "published_at", "level_curious"}
    assert _FORBIDDEN_KEYS.isdisjoint(dumped.keys())


def test_finding_detail_has_exactly_expected_keys() -> None:
    detail = FindingDetail(
        id=uuid4(),
        title="Un hallazgo de prueba",
        published_at=_PUBLISHED_AT,
        type="paper_explained",
        level_curious="Nivel curioso.",
        level_amateur="Nivel aficionado.",
        level_technical="Nivel técnico.",
        source_url="https://arxiv.org/abs/2601.00001",
    )
    dumped = detail.model_dump(mode="json")
    assert set(dumped.keys()) == {
        "id",
        "title",
        "published_at",
        "type",
        "level_curious",
        "level_amateur",
        "level_technical",
        "source_url",
    }
    assert _FORBIDDEN_KEYS.isdisjoint(dumped.keys())


def test_findings_page_response_has_exactly_expected_keys() -> None:
    summary = FindingSummary(
        id=uuid4(),
        title="Un hallazgo de prueba",
        published_at=_PUBLISHED_AT,
        level_curious="Nivel curioso.",
    )
    page = FindingsPageResponse(items=[summary], page=1, size=20, total=1)
    dumped = page.model_dump(mode="json")
    assert set(dumped.keys()) == {"items", "page", "size", "total"}
    assert len(dumped["items"]) == 1
    assert set(dumped["items"][0].keys()) == {"id", "title", "published_at", "level_curious"}
    assert _FORBIDDEN_KEYS.isdisjoint(dumped.keys())
    assert _FORBIDDEN_KEYS.isdisjoint(dumped["items"][0].keys())


def test_health_response_has_exactly_expected_keys() -> None:
    health = HealthResponse(status="ok", database="ok")
    dumped = health.model_dump(mode="json")
    assert set(dumped.keys()) == {"status", "database"}


def test_source_url_builds_arxiv_link() -> None:
    assert source_url("arxiv", "2601.00001") == "https://arxiv.org/abs/2601.00001"


def test_source_url_returns_none_for_unknown_source() -> None:
    assert source_url("some-other-source", "whatever-id") is None
