"""Payloads de `Finding` para los tests de la API de lectura (T77).

Entidades de dominio completas, incluidos los metadatos internos que la API
NO debe exponer (`solution_key`, `soltype`, `pl_pubdate`, `releasedate` de la
previa, `ttv_flag`, `window_days`, `paper_published_at`), para que los tests
detecten una fuga si alguien vuelca la entidad en vez de la lista blanca.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, date, datetime

from helpers.exoplanet import make_measurement, make_solution
from nocturna.domain.entities import (
    ArchiveStatus,
    CatalogTension,
    CatalogTensionComparison,
    ConfirmationReference,
    FirstMeasurement,
    IndependentConfirmation,
    MeasuredParameter,
    MeasurementUnit,
    PaperMeasurement,
)
from nocturna.domain.tension import compare

ARCHIVE_URL = "https://exoplanetarchive.ipac.caltech.edu/overview/V1298%20Tau%20b"
TENSION_EVIDENCE = "We measure a mass of 0.5 +/- 0.1 M_jup for V1298 Tau b."
R_EARTH = MeasurementUnit.R_EARTH
RADIUS = MeasuredParameter.RADIUS


def catalog_tension_two_priors() -> CatalogTension:
    """Una medida del paper frente a dos previas: la por defecto y otra con `arxiv_id`."""
    paper = make_measurement(0.5, 0.1, 0.1)
    default = make_solution(
        0.04,
        0.02,
        0.02,
        reference="Livingston et al. 2026",
        is_default=True,
        solution_key="solkey-default",
        pl_pubdate="2026-05",
        releasedate=date(2026, 6, 1),
        ttv_flag=False,
    )
    other = make_solution(
        0.3,
        0.1,
        0.1,
        reference="Suarez Mascareno et al. 2022",
        arxiv_id="2201.00001",
        solution_key="solkey-other",
        ttv_flag=True,
    )
    paper = replace(paper, evidence=TENSION_EVIDENCE)
    comparisons = tuple(
        CatalogTensionComparison(paper=paper, prior=prior, sigma=compare(paper, prior).sigma)
        for prior in (default, other)
    )
    return CatalogTension(
        planet_name="V1298 Tau b",
        parameter=MeasuredParameter.MASS,
        archive_url=ARCHIVE_URL,
        threshold_sigma=3.0,
        reference_sigma=comparisons[0].sigma,
        comparisons=comparisons,
    )


def first_measurement_absent() -> FirstMeasurement:
    return FirstMeasurement(
        paper_planet_name="TOI-6981 b",
        archive_planet_name=None,
        parameter=RADIUS,
        archive_status=ArchiveStatus.ABSENT,
        measurements=(PaperMeasurement(2.4, 0.1, 0.1, R_EARTH),),
    )


def first_measurement_no_comparable() -> FirstMeasurement:
    return FirstMeasurement(
        paper_planet_name="HIP 67522 c",
        archive_planet_name="HIP 67522 c",
        parameter=RADIUS,
        archive_status=ArchiveStatus.NO_COMPARABLE_SOLUTION,
        measurements=(PaperMeasurement(5.0, 0.4, 0.3, R_EARTH),),
        archive_url="https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20c",
    )


def independent_confirmation(*, arxiv_id: str | None = "2601.00002") -> IndependentConfirmation:
    return IndependentConfirmation(
        paper_planet_name="HIP 67522 b",
        archive_planet_name="HIP 67522 b",
        parameter=RADIUS,
        archive_url="https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20b",
        measurements=(PaperMeasurement(10.0, 0.5, 0.5, R_EARTH),),
        reference=ConfirmationReference(
            refname="Chakraborty 2026",
            arxiv_id=arxiv_id,
            value=10.2,
            err_plus=0.4,
            err_minus=0.4,
            unit=R_EARTH,
            releasedate=date(2026, 10, 1),
        ),
        sigmas=(0.3,),
        max_sigma=2.0,
        window_days=30,
        paper_published_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
