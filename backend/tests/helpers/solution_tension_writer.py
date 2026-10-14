"""Tensiones entre soluciones del archivo para los tests del redactor (T85.e).

Caso real: HD 114082 b, periodo, Zakhozhay et al. 2022 (109,75 d) frente a
del Burgo et al. 2026 (225,5504 d, por defecto): razón 2,055, σ 289,5.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from nocturna.domain.entities import (
    ITEM_SOURCE_EXOPLANET_ARCHIVE,
    CatalogSolution,
    Item,
    MeasuredParameter,
    MeasurementLimit,
    MeasurementUnit,
    ReferenceRole,
    SolutionComparison,
    SolutionTensionLabel,
)
from nocturna.domain.solution_tension import SkipReason, SolutionTension
from nocturna.domain.tension import PeriodCheck

COMPUTED_AT = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
NEW_KEY = "a" * 64
REFERENCE_KEY = "b" * 64


def catalog_solution(
    reference: str,
    value: float,
    err: float,
    *,
    parameter: MeasuredParameter = MeasuredParameter.PERIOD,
    unit: MeasurementUnit = MeasurementUnit.DAY,
    is_default: bool = False,
    releasedate: date | None = None,
    solution_key: str | None = None,
    planet: str = "HD 114082 b",
) -> CatalogSolution:
    return CatalogSolution(
        planet_name=planet,
        parameter=parameter,
        value=value,
        err_plus=err,
        err_minus=err,
        unit=unit,
        limit=MeasurementLimit.NONE,
        reference=reference,
        is_default=is_default,
        arxiv_id=None,
        solution_key=solution_key,
        soltype="Published Confirmed",
        pl_pubdate="2026-05",
        releasedate=releasedate,
        ttv_flag=False,
    )


def make_solution_tension(
    item_id: UUID | None,
    *,
    sigma: float = 289.5,
    parameter: MeasuredParameter = MeasuredParameter.PERIOD,
    new_key: str = NEW_KEY,
    skip_reason: SkipReason | None = None,
    labels: tuple[SolutionTensionLabel, ...] = (),
    others: tuple[SolutionComparison, ...] = (),
    releasedate: date = date(2026, 9, 18),
    planet: str = "HD 114082 b",
    reference_role: ReferenceRole = ReferenceRole.MOST_RECENT,
    new_reference: str = "del Burgo et al. 2026",
) -> SolutionTension:
    if parameter is MeasuredParameter.PERIOD:
        new = catalog_solution(
            new_reference, 225.5504, 0.0004, is_default=True, releasedate=releasedate, planet=planet
        )
        reference = catalog_solution("Zakhozhay et al. 2022", 109.75, 0.4, planet=planet)
        period_check = PeriodCheck(min_difference_met=True, alias_suspected=False)
    else:
        new = catalog_solution(
            new_reference,
            1.2,
            0.01,
            parameter=parameter,
            unit=MeasurementUnit.M_JUP,
            is_default=True,
            releasedate=releasedate,
            planet=planet,
        )
        reference = catalog_solution(
            "Otro et al. 2020",
            0.8,
            0.02,
            parameter=parameter,
            unit=MeasurementUnit.M_JUP,
            planet=planet,
        )
        period_check = None
    return SolutionTension(
        new_solution_key=new_key,
        reference_solution_key=REFERENCE_KEY,
        planet_name=planet,
        parameter=parameter,
        new=new,
        reference=reference,
        reference_role=reference_role,
        computed_at=COMPUTED_AT,
        sigma=sigma,
        others=others,
        period_check=period_check,
        labels=labels,
        skip_reason=skip_reason,
        item_id=item_id,
    )


def make_archive_item(*, external_id: str = NEW_KEY, planet: str = "HD 114082 b") -> Item:
    now = datetime(2026, 10, 14, 4, 0, tzinfo=UTC)
    return Item(
        source=ITEM_SOURCE_EXOPLANET_ARCHIVE,
        external_id=external_id,
        title=f"{planet}: del Burgo et al. 2026",
        abstract="Nueva solución en el archivo.",
        categories=["exoplanet_archive"],
        published_at=now,
        fetched_at=now,
    )
