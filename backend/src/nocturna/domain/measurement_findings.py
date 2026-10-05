"""Reglas de los findings `primera_medida` y `confirmacion_independiente` (T89).

Funciones puras sobre una `TensionEvaluation` ya calculada y guardada: sin IO,
sin red y sin catálogo. Decisiones: D2 (qué es una primera medida), D3 (solo
masa y radio), D4 (σ <= `max_sigma` frente a la referencia de T88), D5 (ventana
de recencia) y D7 (independencia = paper distinto, ya garantizado porque la
solución propia se excluye al evaluar).
"""

from datetime import date, datetime

from nocturna.domain.entities import (
    MEASUREMENT_FINDING_PARAMETERS,
    ArchiveStatus,
    ConfirmationReference,
    FirstMeasurement,
    IndependentConfirmation,
    PaperMeasurement,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.tension import EvaluationStatus, TensionEvaluation


def first_measurement_eligible(ev: TensionEvaluation) -> bool:
    """`awaiting_reference` de masa o radio, sin cota ni solución propia: el
    planeta está ausente del archivo (rama i) o presente sin solución
    `Published Confirmed` con error bilateral (rama ii)."""
    return (
        ev.status == EvaluationStatus.AWAITING_REFERENCE
        and ev.parameter in MEASUREMENT_FINDING_PARAMETERS
        and ev.limit is None
        and ev.own_solution_key is None
        and all(m.usable_for_tension for m in ev.measurements)
    )


def _reference_sigmas(ev: TensionEvaluation) -> tuple[ConfirmationReference, list[float]] | None:
    """Referencia de T88 y un σ por medida del paper (en el orden de
    `ev.measurements`); `None` si falta algo."""
    if ev.status != EvaluationStatus.EVALUATED or ev.result is None:
        return None
    reference = ev.result.reference()
    if reference is None or reference.releasedate is None:
        return None
    assert reference.err_plus is not None and reference.err_minus is not None  # noqa: S101
    sigmas: list[float] = []
    for measurement in ev.measurements:
        found = [
            c.sigma
            for c in ev.result.comparisons
            if c.prior == reference and c.paper == measurement
        ]
        if not found:
            return None
        sigmas.append(min(found))
    ref = ConfirmationReference(
        refname=reference.reference,
        arxiv_id=reference.arxiv_id,
        value=reference.value,
        err_plus=reference.err_plus,
        err_minus=reference.err_minus,
        unit=reference.unit,
        releasedate=reference.releasedate,
    )
    return ref, sigmas


def _in_window(
    item_published_at: datetime, releasedate: date, now: datetime, window_days: int
) -> bool:
    """La más reciente de las dos entradas cae en los últimos `window_days`
    días, contados desde la fecha de `now` (ya en hora local). `now` y
    `item_published_at` son aware; la fecha del paper se toma en la zona de
    `now`."""
    published = item_published_at.astimezone(now.tzinfo).date()
    latest = max(published, releasedate)
    return (now.date() - latest).days <= window_days


def confirmation_eligible(
    ev: TensionEvaluation,
    *,
    item_published_at: datetime,
    now: datetime,
    max_sigma: float,
    window_days: int,
) -> bool:
    """`evaluated` de masa o radio con todas las medidas a σ <= `max_sigma`
    de la referencia de T88 (no tiene por qué ser `is_default`) y la entrada
    más reciente dentro de la ventana."""
    if ev.parameter not in MEASUREMENT_FINDING_PARAMETERS:
        return False
    resolved = _reference_sigmas(ev)
    if resolved is None:
        return False
    reference, sigmas = resolved
    if any(sigma > max_sigma for sigma in sigmas):
        return False
    return _in_window(item_published_at, reference.releasedate, now, window_days)


def first_measurement_from(ev: TensionEvaluation, *, archive_url: str | None) -> FirstMeasurement:
    """`FirstMeasurement` de una evaluación elegible. `archive_url` debe ser
    `None` si el planeta está ausente del archivo."""
    if not first_measurement_eligible(ev):
        raise InvariantViolation("la evaluación no es elegible como primera medida")
    return FirstMeasurement(
        paper_planet_name=ev.planet_name,
        archive_planet_name=ev.archive_planet_name,
        parameter=ev.parameter,
        archive_status=(
            ArchiveStatus.ABSENT
            if ev.archive_planet_name is None
            else ArchiveStatus.NO_COMPARABLE_SOLUTION
        ),
        measurements=tuple(PaperMeasurement.from_measurement(m) for m in ev.measurements),
        archive_url=archive_url,
    )


def independent_confirmation_from(
    ev: TensionEvaluation,
    *,
    item_published_at: datetime,
    now: datetime,
    max_sigma: float,
    window_days: int,
    archive_url: str,
) -> IndependentConfirmation:
    """`IndependentConfirmation` de una evaluación elegible."""
    if not confirmation_eligible(
        ev,
        item_published_at=item_published_at,
        now=now,
        max_sigma=max_sigma,
        window_days=window_days,
    ):
        raise InvariantViolation("la evaluación no es elegible como confirmación independiente")
    resolved = _reference_sigmas(ev)
    assert resolved is not None and ev.archive_planet_name is not None  # noqa: S101
    reference, sigmas = resolved
    return IndependentConfirmation(
        paper_planet_name=ev.planet_name,
        archive_planet_name=ev.archive_planet_name,
        parameter=ev.parameter,
        archive_url=archive_url,
        measurements=tuple(PaperMeasurement.from_measurement(m) for m in ev.measurements),
        reference=reference,
        sigmas=tuple(sigmas),
        max_sigma=max_sigma,
        window_days=window_days,
        paper_published_at=item_published_at,
    )
