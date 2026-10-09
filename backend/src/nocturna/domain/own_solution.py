"""Solución propia del paper en el archivo (T83, ADR 0023).

Puro: sin IO. Clasifica una solución del archivo respecto a un paper para
saber si es la del propio paper (por `arxiv_id` o por coincidencia de
valores de masa o radio), una ajena o ambigua. Las ambiguas siguen siendo
previas; solo bloquean `confirmacion_independiente`.
"""

import math
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from nocturna.domain.entities import (
    CatalogSolution,
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.tension import to_canonical

MAX_VALUE_REL_TOLERANCE = 0.1
_VALUE_MATCH_PARAMETERS = (MeasuredParameter.MASS, MeasuredParameter.RADIUS)
_PUBDATE = re.compile(r"^(\d{4})-(0[1-9]|1[0-2])$")


class SolutionProvenance(StrEnum):
    OWN_ARXIV_ID = "own_arxiv_id"
    OWN_VALUE_MATCH = "own_value_match"
    AMBIGUOUS = "ambiguous"
    INDEPENDENT = "independent"


OWN_PROVENANCES: frozenset[SolutionProvenance] = frozenset(
    {SolutionProvenance.OWN_ARXIV_ID, SolutionProvenance.OWN_VALUE_MATCH}
)


@dataclass(frozen=True, slots=True)
class OwnSolutionRule:
    """`value_rel_tolerance`: |x_paper - x_archivo| / |x_archivo| máximo para
    casar valores. `pubdate_margin_months`: meses de margen hacia atrás del
    mes de publicación del paper para que un `pl_pubdate` sea plausible."""

    value_rel_tolerance: float
    pubdate_margin_months: int

    def __post_init__(self) -> None:
        if not (
            math.isfinite(self.value_rel_tolerance)
            and 0 < self.value_rel_tolerance <= MAX_VALUE_REL_TOLERANCE
        ):
            raise InvariantViolation("'value_rel_tolerance' debe estar en (0, 0.1]")
        if self.pubdate_margin_months < 0:
            raise InvariantViolation("'pubdate_margin_months' no puede ser negativo")


def _month_index(moment: datetime) -> int:
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC)
    return moment.year * 12 + moment.month - 1


def _pubdate_index(pl_pubdate: str | None) -> int | None:
    if pl_pubdate is None:
        return None
    found = _PUBDATE.match(pl_pubdate.strip())
    if found is None:
        return None
    return int(found.group(1)) * 12 + int(found.group(2)) - 1


def _matches_a_measurement(
    solution: CatalogSolution, measurements: Sequence[Measurement], rule: OwnSolutionRule
) -> bool:
    if solution.limit != MeasurementLimit.NONE:
        return False
    archive_value = to_canonical(solution.value, solution.unit)
    return any(
        m.parameter == solution.parameter
        and abs(to_canonical(m.value, m.unit) - archive_value) / abs(archive_value)
        <= rule.value_rel_tolerance
        for m in measurements
    )


def classify_solution(
    solution: CatalogSolution,
    *,
    external_id: str,
    published_at: datetime,
    measurements: Sequence[Measurement],
    rule: OwnSolutionRule,
) -> SolutionProvenance:
    """Procedencia de `solution` respecto al paper `external_id`.

    1. Con `arxiv_id`: igual al del paper es propia; distinto, independiente.
    2. Sin `arxiv_id` y `pl_pubdate` anterior al mes del paper menos el margen:
       independiente.
    3. Masa o radio, sin cota y con valor casado con alguna medida: propia
       por valor.
    4. Resto (incluido todo periodo): ambigua. Un `pl_pubdate` nulo o ilegible
       cuenta como plausible.
    """
    if solution.arxiv_id is not None:
        if solution.arxiv_id == external_id:
            return SolutionProvenance.OWN_ARXIV_ID
        return SolutionProvenance.INDEPENDENT
    pubdate = _pubdate_index(solution.pl_pubdate)
    if pubdate is not None and pubdate < _month_index(published_at) - rule.pubdate_margin_months:
        return SolutionProvenance.INDEPENDENT
    if solution.parameter in _VALUE_MATCH_PARAMETERS and _matches_a_measurement(
        solution, measurements, rule
    ):
        return SolutionProvenance.OWN_VALUE_MATCH
    return SolutionProvenance.AMBIGUOUS


def own_solution_keys(
    classified: Iterable[tuple[CatalogSolution, SolutionProvenance]],
) -> frozenset[str]:
    """Claves de las filas del archivo propias del paper (en algún parámetro).

    Ignora ambiguas, independientes y soluciones sin `solution_key`.
    """
    return frozenset(
        solution.solution_key
        for solution, kind in classified
        if kind in OWN_PROVENANCES and solution.solution_key is not None
    )
