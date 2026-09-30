"""Constructores de `Measurement`, `CatalogSolution`, `Item` y `Reading` para
los tests de T73 (tension frente al catalogo). Sin IO ni red."""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.entities import (
    Item,
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
    Reading,
)

MASS = MeasuredParameter.MASS
RADIUS = MeasuredParameter.RADIUS
M_JUP = MeasurementUnit.M_JUP
M_EARTH = MeasurementUnit.M_EARTH


def make_measurement(
    value: float,
    err_plus: float | None,
    err_minus: float | None,
    *,
    planet_name: str = "V1298 Tau b",
    parameter: MeasuredParameter = MASS,
    unit: MeasurementUnit = M_JUP,
    limit: MeasurementLimit = MeasurementLimit.NONE,
    origin: MeasurementOrigin = MeasurementOrigin.THIS_WORK,
) -> Measurement:
    return Measurement(
        planet_name=planet_name,
        parameter=parameter,
        value=value,
        err_plus=err_plus,
        err_minus=err_minus,
        unit=unit,
        limit=limit,
        origin=origin,
        evidence=f"{value} +{err_plus}/-{err_minus} {unit.value}",
    )


def make_solution(
    value: float,
    err_plus: float | None,
    err_minus: float | None,
    *,
    planet_name: str = "V1298 Tau b",
    parameter: MeasuredParameter = MASS,
    unit: MeasurementUnit = M_JUP,
    limit: MeasurementLimit = MeasurementLimit.NONE,
    reference: str = "Livingston et al. 2026",
    is_default: bool = False,
    arxiv_id: str | None = None,
) -> CatalogSolution:
    return CatalogSolution(
        planet_name=planet_name,
        parameter=parameter,
        value=value,
        err_plus=err_plus,
        err_minus=err_minus,
        unit=unit,
        limit=limit,
        reference=reference,
        is_default=is_default,
        arxiv_id=arxiv_id,
    )


def make_item(external_id: str = "2601.00001") -> Item:
    now = datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
    return Item(
        source="arxiv",
        external_id=external_id,
        title="Un titulo",
        abstract="Un abstract.",
        categories=["astro-ph.EP"],
        published_at=now,
        fetched_at=now,
    )


def make_reading(
    item_id: UUID | None = None,
    measurements: tuple[Measurement, ...] | None = (),
) -> Reading:
    return Reading(
        item_id=item_id or uuid4(),
        summary="Resumen.",
        objects=("V1298 Tau b",),
        claims=("Una afirmacion.",),
        interest_score=4,
        tokens_in=100,
        tokens_out=50,
        model="fake-model",
        measurements=measurements,
    )
