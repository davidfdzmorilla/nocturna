"""Constructores de `Measurement`, `CatalogSolution`, `Item` y `Reading` para
los tests de T73 (tensión frente al catálogo). Sin IO ni red."""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import TYPE_CHECKING
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

if TYPE_CHECKING:
    from nocturna.domain.tension import TensionResult

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
    soltype: str | None = "Published Confirmed",
    solution_key: str | None = None,
    pl_pubdate: str | None = None,
    releasedate: date | None = None,
    ttv_flag: bool | None = None,
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
        soltype=soltype,
        solution_key=solution_key,
        pl_pubdate=pl_pubdate,
        releasedate=releasedate,
        ttv_flag=ttv_flag,
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


# --- T72: CatalogTension real de V1298 Tau (datos de T74) -------------------

V1298_MEASURES = "2609.30038.reader-measures-exp1.derived-fullname.json"
V1298_ARCHIVE_URL = "https://exoplanetarchive.ipac.caltech.edu/overview/V1298%20Tau%20b"
V1298_THRESHOLD = 3.0


def v1298_tension_results() -> dict[str, TensionResult]:
    """`TensionResult` reales por planeta ("V1298 Tau b" / "V1298 Tau e").

    Origen: `ComputeTensions` sobre el catálogo sin red de `helpers.archive`
    (fixture `ps_v1298tau.csv`, filas reales del archivo) y las medidas del
    Reader de `tests/fixtures/t71c/` (paper 2609.30038). Síncrono: usa
    `anyio.run`, así que no debe llamarse desde un test async.
    """
    import anyio
    from fakes.clock import FakeClock

    from helpers.archive import load_t71c_measurements, make_catalog
    from nocturna.application.use_cases.compute_tensions import ComputeTensions

    async def run():
        catalog, _, _ = make_catalog()
        item = make_item("2609.30038")
        reading = make_reading(item.id, load_t71c_measurements(V1298_MEASURES))
        compute = ComputeTensions(
            catalog,
            threshold_sigma=V1298_THRESHOLD,
            period_rule=make_period_rule(),
            own_solution_rule=make_own_solution_rule(),
            clock=FakeClock(datetime(2026, 10, 2, tzinfo=UTC)),
        )
        return await compute([(item, reading)])

    report = anyio.run(run)
    return {e.result.planet_name: e.result for e in report.evaluations if e.result is not None}


def make_period_rule():
    """`PeriodRule` con los valores de `[tension.period]` de pipeline.toml."""
    from nocturna.domain.tension import PeriodRule

    return PeriodRule(
        min_relative_difference=1e-4,
        min_absolute_difference_days=1.0 / 24.0,
        alias_tolerance=0.01,
        alias_max_harmonic=5,
    )


def catalog_tension_v1298_b():
    """`CatalogTension` de V1298 Tau b (masa, referencia ~3,368 sigma, umbral 3).

    Conserva los cinco campos de T88 de las previas: el JSON persistido
    (ADR 0017, esquema v1) los guarda como claves opcionales."""
    from nocturna.domain.tension import catalog_tension_from

    return catalog_tension_from(
        v1298_tension_results()["V1298 Tau b"],
        threshold_sigma=V1298_THRESHOLD,
        archive_url=V1298_ARCHIVE_URL,
    )


def make_own_solution_rule():
    """`OwnSolutionRule` con los valores de `[tension.own_solution]` de pipeline.toml (T83)."""
    from nocturna.domain.own_solution import OwnSolutionRule

    return OwnSolutionRule(value_rel_tolerance=0.01, pubdate_margin_months=6)
