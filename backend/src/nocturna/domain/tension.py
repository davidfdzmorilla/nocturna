"""Tensión de una medida de un paper frente a las previas del catálogo (T73).

Todo el cálculo es Python puro y determinista; Claude no interviene
(ADR 0012). Decisiones citadas: OPEN_DECISIONS T73, 2026-09-30.

Fórmula aprobada, tras pasar ambos valores a la unidad canónica:

    σ = |x_p - x_i| / sqrt(e_p² + e_i²)

con el lado del error elegido por la posición relativa de los valores: si el
paper queda por encima de la previa, `e_p = err_minus` del paper y
`e_i = err_plus` de la previa; si queda por debajo, `e_p = err_plus` y
`e_i = err_minus`. Valores iguales dan σ = 0.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID, uuid4

from nocturna.domain.entities import (
    CatalogSolution,
    CatalogTension,
    CatalogTensionComparison,
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementUnit,
)
from nocturna.domain.errors import GuardedFieldAssignment, InvalidTransition, InvariantViolation

PUBLISHED_CONFIRMED = "Published Confirmed"

M_JUP_IN_M_EARTH = 317.83
R_JUP_IN_R_EARTH = 11.209

CANONICAL_UNIT: Mapping[MeasuredParameter, MeasurementUnit] = {
    MeasuredParameter.MASS: MeasurementUnit.M_EARTH,
    MeasuredParameter.RADIUS: MeasurementUnit.R_EARTH,
    MeasuredParameter.PERIOD: MeasurementUnit.DAY,
}

_FACTOR_TO_CANONICAL: Mapping[MeasurementUnit, float] = {
    MeasurementUnit.M_JUP: M_JUP_IN_M_EARTH,
    MeasurementUnit.R_JUP: R_JUP_IN_R_EARTH,
    MeasurementUnit.M_EARTH: 1.0,
    MeasurementUnit.R_EARTH: 1.0,
    MeasurementUnit.DAY: 1.0,
}


def to_canonical(value: float, unit: MeasurementUnit) -> float:
    """Convierte `value` (o un error) de `unit` a la unidad canónica de su parámetro."""
    return value * _FACTOR_TO_CANONICAL[unit]


@dataclass(frozen=True, slots=True)
class CatalogComparison:
    """Comparación de una medida del paper con una previa, en unidad común.

    `paper_err` y `prior_err` son los errores efectivamente usados en σ (el
    lado elegido según la posición relativa de los valores).
    """

    paper: Measurement
    prior: CatalogSolution
    unit: MeasurementUnit
    paper_value: float
    paper_err: float
    prior_value: float
    prior_err: float
    sigma: float


def compare(paper: Measurement, prior: CatalogSolution) -> CatalogComparison:
    """σ del paper frente a una previa. `InvariantViolation` si el parámetro
    difiere, el paper no es `usable_for_tension` o la previa no es
    `usable_as_prior`."""
    if paper.parameter != prior.parameter:
        raise InvariantViolation("paper y previa deben medir el mismo parámetro")
    if not paper.usable_for_tension:
        raise InvariantViolation("la medida del paper no es utilizable para tensión")
    if not prior.usable_as_prior:
        raise InvariantViolation("la solución del catálogo no es utilizable como previa")
    assert paper.err_plus is not None and paper.err_minus is not None  # noqa: S101
    assert prior.err_plus is not None and prior.err_minus is not None  # noqa: S101

    x_p = to_canonical(paper.value, paper.unit)
    x_i = to_canonical(prior.value, prior.unit)
    if x_p > x_i:
        e_p, e_i = paper.err_minus, prior.err_plus
    elif x_p < x_i:
        e_p, e_i = paper.err_plus, prior.err_minus
    else:
        e_p, e_i = paper.err_minus, prior.err_plus
    e_p = to_canonical(e_p, paper.unit)
    e_i = to_canonical(e_i, prior.unit)
    sigma = 0.0 if x_p == x_i else abs(x_p - x_i) / math.sqrt(e_p**2 + e_i**2)
    return CatalogComparison(
        paper=paper,
        prior=prior,
        unit=CANONICAL_UNIT[paper.parameter],
        paper_value=x_p,
        paper_err=e_p,
        prior_value=x_i,
        prior_err=e_i,
        sigma=sigma,
    )


@dataclass(frozen=True, slots=True)
class TensionResult:
    """Comparaciones de un ítem para un (planeta canónico, parámetro):
    el producto medidas del paper × previas del catálogo."""

    item_id: UUID
    planet_name: str
    parameter: MeasuredParameter
    comparisons: tuple[CatalogComparison, ...]
    # T88: Reading de origen.
    reading_id: UUID | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparisons", tuple(self.comparisons))
        if not self.comparisons:
            raise InvariantViolation("'comparisons' no puede estar vacía")
        for comparison in self.comparisons:
            if comparison.paper.parameter != self.parameter:
                raise InvariantViolation("'comparisons' debe ser homogénea en 'parameter'")

    def reference(self) -> CatalogSolution | None:
        """Referencia de la comparación (`select_reference` sobre las previas
        presentes). Se identifica por igualdad de valor, no de objeto: copias
        iguales de la misma previa cuentan como una."""
        return select_reference(list(dict.fromkeys(c.prior for c in self.comparisons)))

    def reference_sigma(self) -> float | None:
        """σ mínimo de las medidas del paper frente a `reference()`; `None`
        si no hay referencia."""
        reference = self.reference()
        if reference is None:
            return None
        sigmas = [c.sigma for c in self.comparisons if c.prior == reference]
        return min(sigmas) if sigmas else None

    def is_candidate(self, threshold_sigma: float) -> bool:
        """Candidato a hallazgo: hay referencia y todas las medidas del paper
        superan el umbral frente a ella (σ mínimo >= umbral)."""
        sigma = self.reference_sigma()
        return sigma is not None and sigma >= threshold_sigma


def catalog_tension_from(
    result: TensionResult, *, threshold_sigma: float, archive_url: str
) -> CatalogTension:
    """Construye la `CatalogTension` publicable de un `TensionResult`.
    `InvariantViolation` si el resultado no es candidato al umbral dado."""
    if not result.is_candidate(threshold_sigma):
        raise InvariantViolation("el resultado no es candidato a hallazgo con ese umbral")
    reference = result.reference()
    if reference is None or not reference.is_default:
        raise InvariantViolation(
            "solo se puede construir una CatalogTension con referencia 'is_default' (T89)"
        )
    reference_sigma = result.reference_sigma()
    if reference_sigma is None:
        raise InvariantViolation("el resultado no tiene 'reference_sigma'")
    return CatalogTension(
        planet_name=result.planet_name,
        parameter=result.parameter,
        archive_url=archive_url,
        threshold_sigma=threshold_sigma,
        reference_sigma=reference_sigma,
        comparisons=tuple(
            CatalogTensionComparison(paper=c.paper, prior=c.prior, sigma=c.sigma)
            for c in result.comparisons
        ),
    )


def _recency_order(priors: Sequence[CatalogSolution]) -> list[CatalogSolution]:
    """Más reciente primero: (pl_pubdate desc, releasedate desc, solution_key asc)."""
    ordered = sorted(priors, key=lambda p: p.solution_key or "")
    ordered.sort(key=lambda p: p.releasedate or date.min, reverse=True)
    ordered.sort(key=lambda p: p.pl_pubdate or "", reverse=True)
    return ordered


def _pick(candidates: Sequence[CatalogSolution]) -> CatalogSolution | None:
    defaults = {p for p in candidates if p.is_default}
    if len(defaults) > 1:
        return None
    if defaults:
        return next(iter(defaults))
    ordered = _recency_order(candidates)
    return ordered[0] if ordered else None


def select_reference(priors: Sequence[CatalogSolution]) -> CatalogSolution | None:
    """Referencia entre las previas: solo `Published Confirmed` utilizables
    como previa; la `is_default` si cumple (dos distintas: `None`); si no, la
    más reciente (`_recency_order`)."""
    return _pick([p for p in priors if p.soltype == PUBLISHED_CONFIRMED and p.usable_as_prior])


def select_upper_limit(priors: Sequence[CatalogSolution]) -> CatalogSolution | None:
    """Como `select_reference`, sobre previas `Published Confirmed` que son
    una cota superior."""
    return _pick(
        [
            p
            for p in priors
            if p.soltype == PUBLISHED_CONFIRMED
            and p.limit == MeasurementLimit.UPPER
            and p.value > 0
        ]
    )


class LimitOutcome(StrEnum):
    CONSISTENT = "consistent"
    INCOMPATIBLE = "incompatible"


@dataclass(frozen=True, slots=True)
class LimitComparison:
    """Medidas del paper frente a una cota superior del catálogo.

    `margins[i] = (x_p - L) / e_minus` en unidad canónica; incompatible solo
    si todas las medidas superan el umbral."""

    paper: tuple[Measurement, ...]
    limit: CatalogSolution
    margins: tuple[float, ...]
    outcome: LimitOutcome

    def __post_init__(self) -> None:
        object.__setattr__(self, "paper", tuple(self.paper))
        object.__setattr__(self, "margins", tuple(self.margins))
        if not self.paper:
            raise InvariantViolation("'paper' no puede estar vacío")
        if len(self.margins) != len(self.paper):
            raise InvariantViolation("'margins' debe tener un valor por medida")
        if self.limit.limit != MeasurementLimit.UPPER:
            raise InvariantViolation("'limit' debe ser una cota superior")


def compare_with_limit(
    measurements: Sequence[Measurement], limit: CatalogSolution, *, threshold_sigma: float
) -> LimitComparison:
    """Compara medidas del paper con una cota superior del catálogo."""
    if not measurements:
        raise InvariantViolation("se necesita al menos una medida")
    if limit.limit != MeasurementLimit.UPPER:
        raise InvariantViolation("'limit' debe ser una cota superior")
    bound = to_canonical(limit.value, limit.unit)
    margins: list[float] = []
    for m in measurements:
        if m.parameter != limit.parameter:
            raise InvariantViolation("medida y cota deben ser del mismo parámetro")
        if not m.usable_for_tension:
            raise InvariantViolation("la medida del paper no es utilizable para tensión")
        assert m.err_minus is not None  # noqa: S101
        e_minus = to_canonical(m.err_minus, m.unit)
        if e_minus <= 0:
            raise InvariantViolation("'err_minus' debe ser mayor que cero para comparar con cota")
        margins.append((to_canonical(m.value, m.unit) - bound) / e_minus)
    incompatible = all(x >= threshold_sigma for x in margins)
    return LimitComparison(
        paper=tuple(measurements),
        limit=limit,
        margins=tuple(margins),
        outcome=LimitOutcome.INCOMPATIBLE if incompatible else LimitOutcome.CONSISTENT,
    )


@dataclass(frozen=True, slots=True)
class PeriodRule:
    """Regla del periodo. Diferencia mínima: ΔP/P_ref >= `min_relative_difference`
    o ΔP >= `min_absolute_difference_days`. Alias: P1/P2 o P2/P1 a menos de
    `alias_tolerance` (absoluta) de un entero n, 2 <= n <= `alias_max_harmonic`."""

    min_relative_difference: float
    min_absolute_difference_days: float
    alias_tolerance: float
    alias_max_harmonic: int

    def __post_init__(self) -> None:
        if not self.min_relative_difference > 0:
            raise InvariantViolation("'min_relative_difference' debe ser mayor que cero")
        if not self.min_absolute_difference_days > 0:
            raise InvariantViolation("'min_absolute_difference_days' debe ser mayor que cero")
        if not 0 < self.alias_tolerance < 0.5:
            raise InvariantViolation("'alias_tolerance' debe estar en (0, 0.5)")
        if self.alias_max_harmonic < 2:
            raise InvariantViolation("'alias_max_harmonic' debe ser >= 2")


@dataclass(frozen=True, slots=True)
class PeriodCheck:
    min_difference_met: bool
    alias_suspected: bool


def check_period(
    measurements: Sequence[Measurement], reference: CatalogSolution, rule: PeriodRule
) -> PeriodCheck:
    """Diferencia mínima (todas las medidas) y sospecha de alias (alguna)."""
    if not measurements:
        raise InvariantViolation("se necesita al menos una medida")
    if reference.parameter != MeasuredParameter.PERIOD:
        raise InvariantViolation("la referencia debe ser un periodo")
    p_ref = to_canonical(reference.value, reference.unit)
    met = True
    alias = False
    for m in measurements:
        if m.parameter != MeasuredParameter.PERIOD:
            raise InvariantViolation("las medidas deben ser periodos")
        p = to_canonical(m.value, m.unit)
        delta = abs(p - p_ref)
        if not (
            delta / p_ref >= rule.min_relative_difference
            or delta >= rule.min_absolute_difference_days
        ):
            met = False
        for ratio in (p / p_ref, p_ref / p):
            nearest = round(ratio)
            if (
                2 <= nearest <= rule.alias_max_harmonic
                and abs(ratio - nearest) < rule.alias_tolerance
            ):
                alias = True
    return PeriodCheck(min_difference_met=met, alias_suspected=alias)


class EvaluationStatus(StrEnum):
    AWAITING_REFERENCE = "awaiting_reference"
    EVALUATED = "evaluated"
    CONSISTENT_WITH_LIMIT = "consistent_with_limit"
    INCOMPATIBLE_WITH_LIMIT = "incompatible_with_limit"
    CLOSED_LOOP = "closed_loop"


@dataclass(slots=True)
class TensionEvaluation:
    """Evaluación de (reading, planeta, parámetro). `status` está protegido:
    solo cambia con `reevaluate_with`, que devuelve una evaluación nueva."""

    reading_id: UUID
    item_id: UUID
    planet_name: str
    parameter: MeasuredParameter
    measurements: tuple[Measurement, ...]
    status: EvaluationStatus
    evaluated_at: datetime
    archive_planet_name: str | None = None
    result: TensionResult | None = None
    limit: LimitComparison | None = None
    period_check: PeriodCheck | None = None
    own_solution_key: str | None = None
    id: UUID = None  # type: ignore[assignment]  # se rellena en __post_init__

    _GUARDED_FIELDS: ClassVar[frozenset[str]] = frozenset({"status"})

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._GUARDED_FIELDS and hasattr(self, name):
            raise GuardedFieldAssignment(
                f"'{name}' de TensionEvaluation no se puede asignar directamente; "
                "usa reevaluate_with()"
            )
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        if self.id is None:
            object.__setattr__(self, "id", uuid4())
        object.__setattr__(self, "measurements", tuple(self.measurements))
        if not self.planet_name.strip():
            raise InvariantViolation("'planet_name' no puede estar vacío")
        if not self.measurements:
            raise InvariantViolation("'measurements' no puede estar vacía")
        if any(m.parameter != self.parameter for m in self.measurements):
            raise InvariantViolation("'measurements' debe ser homogénea en 'parameter'")
        if self.evaluated_at.utcoffset() is None:
            raise InvariantViolation("'evaluated_at' debe llevar zona horaria")
        if self.result is not None and self.result.parameter != self.parameter:
            raise InvariantViolation("'result' debe ser del mismo 'parameter'")
        has_reference = self.result is not None and self.result.reference() is not None
        status = self.status
        if (status == EvaluationStatus.EVALUATED) != has_reference:
            raise InvariantViolation("EVALUATED si y solo si 'result' tiene referencia")
        limit_statuses = {
            EvaluationStatus.CONSISTENT_WITH_LIMIT: LimitOutcome.CONSISTENT,
            EvaluationStatus.INCOMPATIBLE_WITH_LIMIT: LimitOutcome.INCOMPATIBLE,
        }
        if status in limit_statuses:
            if self.limit is None or self.limit.outcome != limit_statuses[status]:
                raise InvariantViolation("'limit' debe existir y coincidir con el estado")
        elif self.limit is not None:
            raise InvariantViolation(f"{status.value} no admite 'limit'")
        if (
            self.parameter == MeasuredParameter.PERIOD
            and status == EvaluationStatus.EVALUATED
            and self.period_check is None
        ):
            raise InvariantViolation("un periodo EVALUATED requiere 'period_check'")
        if status == EvaluationStatus.CLOSED_LOOP and not self.own_solution_key:
            raise InvariantViolation("CLOSED_LOOP requiere 'own_solution_key'")

    def reevaluate_with(self, new: "TensionEvaluation") -> "TensionEvaluation":
        """Sustituto desde AWAITING_REFERENCE; conserva `id` y la clave
        (reading_id, planet_name, parameter)."""
        if self.status != EvaluationStatus.AWAITING_REFERENCE:
            raise InvalidTransition(self.status.value, new.status.value, entity="TensionEvaluation")
        if (new.reading_id, new.planet_name, new.parameter) != (
            self.reading_id,
            self.planet_name,
            self.parameter,
        ):
            raise InvariantViolation(
                "la reevaluación debe conservar (reading_id, planeta, parámetro)"
            )
        return replace(new, id=self.id)

    def same_outcome(self, other: "TensionEvaluation") -> bool:
        """Mismo resultado ignorando `id` y `evaluated_at`."""
        return (
            self.reading_id,
            self.item_id,
            self.planet_name,
            self.parameter,
            self.measurements,
            self.status,
            self.archive_planet_name,
            self.result,
            self.limit,
            self.period_check,
            self.own_solution_key,
        ) == (
            other.reading_id,
            other.item_id,
            other.planet_name,
            other.parameter,
            other.measurements,
            other.status,
            other.archive_planet_name,
            other.result,
            other.limit,
            other.period_check,
            other.own_solution_key,
        )

    def is_candidate(self, threshold_sigma: float) -> bool:
        if self.status == EvaluationStatus.INCOMPATIBLE_WITH_LIMIT:
            return True
        if self.status != EvaluationStatus.EVALUATED:
            return False
        assert self.result is not None  # noqa: S101
        if not self.result.is_candidate(threshold_sigma):
            return False
        if self.parameter == MeasuredParameter.PERIOD:
            return self.period_check is not None and self.period_check.min_difference_met
        return True
