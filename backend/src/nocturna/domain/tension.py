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
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.entities import (
    MeasuredParameter,
    Measurement,
    MeasurementUnit,
)
from nocturna.domain.errors import InvariantViolation

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

    def __post_init__(self) -> None:
        object.__setattr__(self, "comparisons", tuple(self.comparisons))
        if not self.comparisons:
            raise InvariantViolation("'comparisons' no puede estar vacía")
        for comparison in self.comparisons:
            if comparison.paper.parameter != self.parameter:
                raise InvariantViolation("'comparisons' debe ser homogénea en 'parameter'")

    def is_candidate(self, threshold_sigma: float) -> bool:
        """Candidato a hallazgo: la referencia es la previa marcada
        `is_default` (exactamente una; con 0 o más de 1 no hay referencia y
        devuelve `False`) y todas las medidas del paper deben superar el
        umbral frente a ella (σ mínimo >= umbral). Las demás previas no
        cuentan (OPEN_DECISIONS T73, 2026-09-30).

        La previa por defecto se identifica por igualdad de valor, no por
        identidad de objeto: un `TensionResult` rehidratado o construido con
        copias iguales de la misma previa debe dar el mismo veredicto. Dos
        filas por defecto idénticas cuentan como una; dos distintas, como
        ambigüedad (`False`)."""
        defaults = {c.prior for c in self.comparisons if c.prior.is_default}
        if len(defaults) != 1:
            return False
        (reference,) = defaults
        sigmas = [c.sigma for c in self.comparisons if c.prior == reference]
        return bool(sigmas) and min(sigmas) >= threshold_sigma
