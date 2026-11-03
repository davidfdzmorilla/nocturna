"""Reglas de los findings `primera_medida` y `confirmacion_independiente` (T89).

Funciones puras sobre una `TensionEvaluation` ya calculada y guardada: sin IO,
sin red y sin catálogo. Decisiones: D2 (qué es una primera medida), D3 (solo
masa y radio), D4 (σ <= `max_sigma` frente a la referencia de T88), D5 (ventana
de recencia) y D7 (independencia = paper distinto, ya garantizado porque la
solución propia se excluye al evaluar; desde T83, ADR 0023, la referencia se
reclasifica con `classify_solution` y debe ser `INDEPENDENT`).
"""

from datetime import date, datetime
from enum import StrEnum

from nocturna.domain.entities import (
    MEASUREMENT_FINDING_PARAMETERS,
    ArchiveStatus,
    ConfirmationReference,
    FirstMeasurement,
    IndependentConfirmation,
    MeasuredParameter,
    PaperMeasurement,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.minimum_mass import states_minimum_mass
from nocturna.domain.own_solution import OwnSolutionRule, SolutionProvenance, classify_solution
from nocturna.domain.team import shares_first_author
from nocturna.domain.tension import EvaluationStatus, TensionEvaluation


def first_measurement_eligible(ev: TensionEvaluation) -> bool:
    """`awaiting_reference` de masa o radio, sin cota ni solución propia: el
    planeta está en el archivo sin solución `Published Confirmed` con error
    bilateral (`no_comparable_solution`). Desde T100 un planeta ausente del
    archivo (`absent`) no es elegible: sin ficha no hay catálogo que
    contradecir y podría ser un candidato sin designación."""
    return (
        ev.status == EvaluationStatus.AWAITING_REFERENCE
        and ev.archive_planet_name is not None
        and ev.parameter in MEASUREMENT_FINDING_PARAMETERS
        and ev.limit is None
        and ev.own_solution_key is None
        and all(m.usable_for_tension for m in ev.measurements)
    )


def minimum_mass_in(ev: TensionEvaluation, abstract: str) -> bool:
    """¿Alguna medida de masa de la evaluación es una masa mínima (`m sin i`)
    según el abstract del paper? (T100, D2)."""
    return any(states_minimum_mass(m, abstract) for m in ev.measurements)


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


def reference_within_confirmation_limits(
    ev: TensionEvaluation,
    *,
    item_published_at: datetime,
    now: datetime,
    max_sigma: float,
    window_days: int,
) -> bool:
    """Regla de `confirmacion_independiente` anterior a T83, sin mirar la
    procedencia de la referencia: `evaluated` de masa o radio con todas las
    medidas a σ <= `max_sigma` de la referencia de T88 (no tiene por qué ser
    `is_default`) y la entrada más reciente dentro de la ventana."""
    if ev.parameter not in MEASUREMENT_FINDING_PARAMETERS:
        return False
    resolved = _reference_sigmas(ev)
    if resolved is None:
        return False
    reference, sigmas = resolved
    if any(sigma > max_sigma for sigma in sigmas):
        return False
    return _in_window(item_published_at, reference.releasedate, now, window_days)


class ConfirmationTeamCheck(StrEnum):
    """Resultado de la regla de equipo (T100, D1) cuando el resto de condiciones
    de `confirmacion_independiente` se cumplen."""

    NOT_APPLICABLE = "not_applicable"  # otra condición ya la descarta
    DIFFERENT_TEAM = "different_team"  # el primer autor de la referencia no figura
    SAME_TEAM = "same_team"  # el primer autor de la referencia figura
    UNKNOWN_TEAM = "unknown_team"  # faltan autores o referencia no reconocible


def confirmation_team_check(
    ev: TensionEvaluation,
    *,
    item_published_at: datetime,
    now: datetime,
    max_sigma: float,
    window_days: int,
    item_external_id: str,
    own_rule: OwnSolutionRule,
    item_authors: str | None,
) -> ConfirmationTeamCheck:
    """Evalúa todas las condiciones de `confirmation_eligible` salvo la regla
    de equipo y, si pasan, devuelve el resultado de esta."""
    if not reference_within_confirmation_limits(
        ev,
        item_published_at=item_published_at,
        now=now,
        max_sigma=max_sigma,
        window_days=window_days,
    ):
        return ConfirmationTeamCheck.NOT_APPLICABLE
    assert ev.result is not None  # noqa: S101
    stored_reference = ev.result.reference()
    assert stored_reference is not None  # noqa: S101
    provenance = classify_solution(
        stored_reference,
        external_id=item_external_id,
        published_at=item_published_at,
        measurements=ev.measurements,
        rule=own_rule,
    )
    if provenance != SolutionProvenance.INDEPENDENT:
        return ConfirmationTeamCheck.NOT_APPLICABLE
    shared = shares_first_author(stored_reference.reference, item_authors)
    if shared is None:
        return ConfirmationTeamCheck.UNKNOWN_TEAM
    return ConfirmationTeamCheck.SAME_TEAM if shared else ConfirmationTeamCheck.DIFFERENT_TEAM


def confirmation_eligible(
    ev: TensionEvaluation,
    *,
    item_published_at: datetime,
    now: datetime,
    max_sigma: float,
    window_days: int,
    item_external_id: str,
    own_rule: OwnSolutionRule,
    item_authors: str | None,
) -> bool:
    """`reference_within_confirmation_limits` y, desde T83 (ADR 0023), referencia
    `INDEPENDENT` del paper: reclasificada al generar, de modo que también
    protege las evaluaciones guardadas antes de T83. Desde T100 (D1) además
    el apellido del primer autor de la referencia no figura entre los autores
    del paper; sin autores o con referencia no reconocible, no hay
    confirmación."""
    return (
        confirmation_team_check(
            ev,
            item_published_at=item_published_at,
            now=now,
            max_sigma=max_sigma,
            window_days=window_days,
            item_external_id=item_external_id,
            own_rule=own_rule,
            item_authors=item_authors,
        )
        is ConfirmationTeamCheck.DIFFERENT_TEAM
    )


def catalog_tension_skip_reason(
    ev: TensionEvaluation,
    *,
    threshold_sigma: float,
    item_external_id: str,
    item_published_at: datetime,
    own_rule: OwnSolutionRule,
    item_abstract: str,
) -> str | None:
    """Por qué una evaluación guardada NO se redacta como `catalog_tension` (T76).

    `None` = elegible. Motivos, en este orden: `not_evaluated` (cualquier
    estado distinto de `evaluated`; `incompatible_with_limit` queda fuera, OD
    244), `minimum_mass` (T100: alguna medida de masa es un `m sin i` según
    `item_abstract`), `below_threshold` (σ de referencia < umbral), `reference_not_default`
    (`catalog_tension_from` solo admite la solución por defecto),
    `reference_not_independent` (la referencia, reclasificada con
    `classify_solution`, no es `INDEPENDENT` del paper: no se presenta como
    tensión la discrepancia con el propio paper ni con una solución ambigua),
    `period_min_difference` y `period_alias` (periodo).
    """
    if ev.status != EvaluationStatus.EVALUATED or ev.result is None:
        return "not_evaluated"
    if minimum_mass_in(ev, item_abstract):
        return "minimum_mass"
    sigma = ev.result.reference_sigma()
    if sigma is None or sigma < threshold_sigma:
        return "below_threshold"
    reference = ev.result.reference()
    assert reference is not None  # noqa: S101
    if not reference.is_default:
        return "reference_not_default"
    provenance = classify_solution(
        reference,
        external_id=item_external_id,
        published_at=item_published_at,
        measurements=ev.measurements,
        rule=own_rule,
    )
    if provenance != SolutionProvenance.INDEPENDENT:
        return "reference_not_independent"
    if ev.parameter == MeasuredParameter.PERIOD:
        if ev.period_check is None or not ev.period_check.min_difference_met:
            return "period_min_difference"
        if ev.period_check.alias_suspected:
            return "period_alias"
    return None


def first_measurement_from(ev: TensionEvaluation, *, archive_url: str | None) -> FirstMeasurement:
    """`FirstMeasurement` de una evaluación elegible (T100: el planeta está en
    el archivo, así que normalmente `archive_url` es el de su ficha; `None`
    si no se puede construir)."""
    if not first_measurement_eligible(ev):
        raise InvariantViolation("la evaluación no es elegible como primera medida")
    return FirstMeasurement(
        paper_planet_name=ev.planet_name,
        archive_planet_name=ev.archive_planet_name,
        parameter=ev.parameter,
        archive_status=ArchiveStatus.NO_COMPARABLE_SOLUTION,
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
    item_external_id: str,
    own_rule: OwnSolutionRule,
    item_authors: str | None,
) -> IndependentConfirmation:
    """`IndependentConfirmation` de una evaluación elegible."""
    if not confirmation_eligible(
        ev,
        item_published_at=item_published_at,
        now=now,
        max_sigma=max_sigma,
        window_days=window_days,
        item_external_id=item_external_id,
        own_rule=own_rule,
        item_authors=item_authors,
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
