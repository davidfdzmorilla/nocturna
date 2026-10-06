"""Tests de `api/schemas.py` (T50 paso 3), sin base de datos ni FastAPI en marcha.

Cada esquema se comprueba por el **conjunto exacto** de claves que produce
al serializar, con asertos explícitos de que `confidence`, `run_id` e
`item_id` -- los tres campos que `CLAUDE.md` prohíbe exponer -- no están en
ninguno. El resto de la batería de esta capa (guarda AST de imports, la app
FastAPI levantada de verdad, base de datos) es del `tester`.
"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from helpers.finding_payloads import (
    TENSION_EVIDENCE,
    catalog_tension_two_priors,
    first_measurement_absent,
    first_measurement_no_comparable,
    independent_confirmation,
)

from nocturna.api.schemas import (
    CatalogTensionOut,
    ConfirmationReferenceOut,
    FindingDetail,
    FindingsPageResponse,
    FindingSummary,
    FirstMeasurementOut,
    HealthResponse,
    IndependentConfirmationOut,
    MeasurementValueOut,
    PaperMeasurementWithEvidenceOut,
    PriorSolutionOut,
    TensionComparisonOut,
    source_url,
)
from nocturna.domain.entities import FindingType

_PUBLISHED_AT = datetime(2026, 1, 2, 3, 0, 0, tzinfo=UTC)
_FORBIDDEN_KEYS = {"confidence", "run_id", "item_id"}
#: Claves que no pueden aparecer en ningún nivel de anidamiento (T77).
_FORBIDDEN_NESTED_KEYS = _FORBIDDEN_KEYS | {
    "tension_evaluation_id",
    "solution_key",
    "soltype",
    "pl_pubdate",
    "ttv_flag",
    "window_days",
    "paper_published_at",
    "limit",
    "origin",
}


def all_keys(node: object) -> set[str]:
    """Todas las claves de un JSON, recorriendo dicts y listas a cualquier profundidad."""
    if isinstance(node, dict):
        keys = set(node)
        for value in node.values():
            keys |= all_keys(value)
        return keys
    if isinstance(node, list):
        keys: set[str] = set()
        for value in node:
            keys |= all_keys(value)
        return keys
    return set()


def test_finding_summary_has_exactly_expected_keys() -> None:
    summary = FindingSummary(
        id=uuid4(),
        title="Un hallazgo de prueba",
        published_at=_PUBLISHED_AT,
        type=FindingType.PAPER_EXPLAINED,
        level_curious="Nivel curioso.",
    )
    dumped = summary.model_dump(mode="json")
    assert set(dumped.keys()) == {"id", "title", "published_at", "type", "level_curious"}
    assert _FORBIDDEN_KEYS.isdisjoint(dumped.keys())


def test_finding_detail_has_exactly_expected_keys() -> None:
    detail = FindingDetail(
        id=uuid4(),
        title="Un hallazgo de prueba",
        published_at=_PUBLISHED_AT,
        type=FindingType.PAPER_EXPLAINED,
        level_curious="Nivel curioso.",
        level_amateur="Nivel aficionado.",
        level_technical="Nivel técnico.",
        source_url="https://arxiv.org/abs/2601.00001",
        catalog_tension=None,
        first_measurement=None,
        independent_confirmation=None,
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
        "catalog_tension",
        "first_measurement",
        "independent_confirmation",
    }
    assert _FORBIDDEN_KEYS.isdisjoint(dumped.keys())
    assert dumped["type"] == "paper_explained"


def test_findings_page_response_has_exactly_expected_keys() -> None:
    summary = FindingSummary(
        id=uuid4(),
        title="Un hallazgo de prueba",
        published_at=_PUBLISHED_AT,
        type=FindingType.PAPER_EXPLAINED,
        level_curious="Nivel curioso.",
    )
    page = FindingsPageResponse(items=[summary], page=1, size=20, total=1)
    dumped = page.model_dump(mode="json")
    assert set(dumped.keys()) == {"items", "page", "size", "total"}
    assert len(dumped["items"]) == 1
    assert set(dumped["items"][0].keys()) == {
        "id",
        "title",
        "published_at",
        "type",
        "level_curious",
    }
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


# --- T77: payloads con lista blanca explícita --------------------------------


def _dump(model: object) -> dict:
    return model.model_dump(mode="json")  # type: ignore[attr-defined]


def test_catalog_tension_out_claves_exactas_y_sin_metadatos_internos() -> None:
    domain = catalog_tension_two_priors()
    # La entidad sí lleva los metadatos internos: el test tiene sentido.
    assert domain.comparisons[0].prior.solution_key is not None
    assert domain.comparisons[0].prior.ttv_flag is not None

    dumped = _dump(CatalogTensionOut.from_domain(domain))

    assert set(dumped) == {
        "planet_name",
        "parameter",
        "archive_url",
        "threshold_sigma",
        "reference_sigma",
        "comparisons",
    }
    assert len(dumped["comparisons"]) == 2
    comparison = dumped["comparisons"][0]
    assert set(comparison) == {"paper", "prior", "sigma"}
    assert set(comparison["paper"]) == {
        "planet_name",
        "value",
        "err_plus",
        "err_minus",
        "unit",
        "evidence",
    }
    assert set(comparison["prior"]) == {
        "reference",
        "value",
        "err_plus",
        "err_minus",
        "unit",
        "is_default",
        "arxiv_id",
    }
    assert _FORBIDDEN_NESTED_KEYS.isdisjoint(all_keys(dumped))


def test_catalog_tension_out_conserva_evidence_sigma_y_previas() -> None:
    domain = catalog_tension_two_priors()

    dumped = _dump(CatalogTensionOut.from_domain(domain))

    assert dumped["parameter"] == "mass"
    assert dumped["archive_url"] == domain.archive_url
    assert dumped["threshold_sigma"] == 3.0
    assert dumped["reference_sigma"] == domain.reference_sigma
    first, second = dumped["comparisons"]
    assert first["paper"]["evidence"] == TENSION_EVIDENCE
    assert first["paper"]["unit"] == "M_jup"
    assert first["prior"]["is_default"] is True
    assert first["prior"]["arxiv_id"] is None
    assert first["sigma"] == domain.comparisons[0].sigma
    assert second["prior"]["is_default"] is False
    assert second["prior"]["arxiv_id"] == "2201.00001"
    assert second["sigma"] == domain.comparisons[1].sigma


@pytest.mark.parametrize(
    ("domain", "status", "archive_name", "has_url"),
    [
        (first_measurement_absent(), "absent", None, False),
        (first_measurement_no_comparable(), "no_comparable_solution", "HIP 67522 c", True),
    ],
)
def test_first_measurement_out_claves_exactas(domain, status, archive_name, has_url) -> None:
    dumped = _dump(FirstMeasurementOut.from_domain(domain))

    assert set(dumped) == {
        "paper_planet_name",
        "archive_planet_name",
        "parameter",
        "archive_status",
        "archive_url",
        "measurements",
    }
    assert dumped["archive_status"] == status
    assert dumped["archive_planet_name"] == archive_name
    assert (dumped["archive_url"] is not None) is has_url
    assert dumped["parameter"] == "radius"
    assert len(dumped["measurements"]) == 1
    assert set(dumped["measurements"][0]) == {"value", "err_plus", "err_minus", "unit"}
    assert _FORBIDDEN_NESTED_KEYS.isdisjoint(all_keys(dumped))


@pytest.mark.parametrize("arxiv_id", ["2601.00002", None])
def test_independent_confirmation_out_claves_exactas(arxiv_id) -> None:
    domain = independent_confirmation(arxiv_id=arxiv_id)

    dumped = _dump(IndependentConfirmationOut.from_domain(domain))

    assert set(dumped) == {
        "paper_planet_name",
        "archive_planet_name",
        "parameter",
        "archive_url",
        "measurements",
        "reference",
        "sigmas",
        "max_sigma",
    }
    assert set(dumped["reference"]) == {
        "refname",
        "arxiv_id",
        "value",
        "err_plus",
        "err_minus",
        "unit",
        "releasedate",
    }
    assert dumped["reference"]["arxiv_id"] == arxiv_id
    assert dumped["reference"]["releasedate"] == "2026-10-01"
    assert dumped["sigmas"] == [0.3]
    assert dumped["max_sigma"] == 2.0
    assert _FORBIDDEN_NESTED_KEYS.isdisjoint(all_keys(dumped))


def test_los_esquemas_de_payload_son_inmutables_y_tienen_from_domain() -> None:
    for schema in (
        MeasurementValueOut,
        PaperMeasurementWithEvidenceOut,
        PriorSolutionOut,
        TensionComparisonOut,
        CatalogTensionOut,
        FirstMeasurementOut,
        ConfirmationReferenceOut,
        IndependentConfirmationOut,
    ):
        assert schema.model_config.get("frozen") is True
        assert callable(schema.from_domain)


def test_all_keys_recorre_listas_y_dicts_anidados() -> None:
    assert all_keys({"a": [{"b": {"c": 1}}, [{"d": 2}]]}) == {"a", "b", "c", "d"}
