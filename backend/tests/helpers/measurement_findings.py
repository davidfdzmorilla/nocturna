"""Evaluaciones de ejemplo para los tests de T89 (findings de medidas).

Casos reales: TOI-6981 b y TOI-210 b (ausentes del archivo, `primera_medida`),
HIP 67522 b (dos medidas de masa del paper 2609.35979 frente a Chakraborty et
al. 2026, σ = 1,4242 y 0,7476) y HIP 67522 c (solo cota superior, consistente).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helpers.archive import fixture_rows
from helpers.exoplanet import make_measurement
from nocturna.domain.archive import catalog_solution_from_archive
from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.entities import (
    Item,
    MeasuredParameter,
    Measurement,
    MeasurementUnit,
)
from nocturna.domain.tension import (
    EvaluationStatus,
    TensionEvaluation,
    TensionResult,
    compare,
    compare_with_limit,
)
from nocturna.infrastructure.exoplanet_archive.mappers import archive_solution_from_ps_row

MASS = MeasuredParameter.MASS
RADIUS = MeasuredParameter.RADIUS
M_EARTH = MeasurementUnit.M_EARTH
R_EARTH = MeasurementUnit.R_EARTH
EVALUATED_AT = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)
FIXTURE = "ps_hip67522_chakraborty2026.csv"


def _fixture_solution(planet: str, default_flag: str, **overrides) -> CatalogSolution:
    row = next(
        r
        for r in fixture_rows(FIXTURE)
        if r["pl_name"] == planet and r["default_flag"] == default_flag
    )
    solution = catalog_solution_from_archive(
        archive_solution_from_ps_row(row), MASS, is_default=default_flag == "1"
    )
    assert solution is not None
    return replace(solution, **overrides) if overrides else solution


def chakraborty_b(**overrides) -> CatalogSolution:
    """Chakraborty et al. 2026, masa de HIP 67522 b: 13,8 ± 1,0 M⊕, 2026-10-01."""
    return _fixture_solution("HIP 67522 b", "0", **overrides)


def limit_c() -> CatalogSolution:
    """Cota superior de 22 M⊕ de HIP 67522 c (Chakraborty et al. 2026)."""
    return _fixture_solution("HIP 67522 c", "0")


def awaiting(
    item_id: UUID,
    measurements: list[Measurement],
    *,
    parameter: MeasuredParameter = RADIUS,
    archive_planet_name: str | None = None,
) -> TensionEvaluation:
    return TensionEvaluation(
        reading_id=uuid4(),
        item_id=item_id,
        planet_name=measurements[0].planet_name,
        parameter=parameter,
        measurements=tuple(measurements),
        status=EvaluationStatus.AWAITING_REFERENCE,
        evaluated_at=EVALUATED_AT,
        archive_planet_name=archive_planet_name,
    )


def toi_6981_b(item_id: UUID) -> TensionEvaluation:
    """Radio de TOI-6981 b, planeta ausente del archivo."""
    return awaiting(
        item_id,
        [make_measurement(2.4, 0.1, 0.1, planet_name="TOI-6981 b", parameter=RADIUS, unit=R_EARTH)],
    )


def toi_210_b_mass(item_id: UUID) -> TensionEvaluation:
    return awaiting(
        item_id,
        [make_measurement(6.75, 1.25, 1.25, planet_name="TOI-210 b", unit=M_EARTH)],
        parameter=MASS,
    )


def present_without_reference(item_id: UUID) -> TensionEvaluation:
    """Planeta presente en el archivo pero sin solución comparable (rama ii)."""
    return awaiting(
        item_id,
        [make_measurement(3.1, 0.2, 0.3, planet_name="Foo-1 b", parameter=RADIUS, unit=R_EARTH)],
        archive_planet_name="Foo-1 b",
    )


def hip67522_b(item_id: UUID, *, reference: CatalogSolution | None = None) -> TensionEvaluation:
    papers = (
        make_measurement(25.0, 7.6, 7.8, planet_name="HIP 67522 b", unit=M_EARTH),
        make_measurement(23.1, 15.4, 12.4, planet_name="HIP 67522 b", unit=M_EARTH),
    )
    prior = reference or chakraborty_b()
    result = TensionResult(
        item_id=item_id,
        planet_name="HIP 67522 b",
        parameter=MASS,
        comparisons=tuple(compare(m, prior) for m in papers),
    )
    return TensionEvaluation(
        reading_id=uuid4(),
        item_id=item_id,
        planet_name="HIP 67522 b",
        parameter=MASS,
        measurements=papers,
        status=EvaluationStatus.EVALUATED,
        evaluated_at=EVALUATED_AT,
        archive_planet_name="HIP 67522 b",
        result=result,
    )


def hip67522_c(item_id: UUID) -> TensionEvaluation:
    paper = make_measurement(11.2, 1.4, 1.4, planet_name="HIP 67522 c", unit=M_EARTH)
    return TensionEvaluation(
        reading_id=uuid4(),
        item_id=item_id,
        planet_name="HIP 67522 c",
        parameter=MASS,
        measurements=(paper,),
        status=EvaluationStatus.CONSISTENT_WITH_LIMIT,
        evaluated_at=EVALUATED_AT,
        archive_planet_name="HIP 67522 c",
        limit=compare_with_limit([paper], limit_c(), threshold_sigma=3.0),
    )


def make_arxiv_item(external_id: str, **overrides) -> Item:
    defaults = {
        "source": "arxiv",
        "external_id": external_id,
        "title": "Un artículo de exoplanetas",
        "abstract": "Un abstract de prueba.",
        "categories": ["astro-ph.EP"],
        "published_at": datetime(2026, 10, 2, 8, 0, tzinfo=UTC),
        "fetched_at": datetime(2026, 10, 2, 9, 0, tzinfo=UTC),
        "exoplanet_match": True,
    }
    defaults.update(overrides)
    return Item(**defaults)
