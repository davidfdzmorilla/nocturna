"""Tensión entre dos soluciones del archivo (T85, ADR 0029).

Dominio puro y determinista: una alta `Published Confirmed` del NASA Exoplanet
Archive frente a su referencia (D5), para masa, radio o periodo. Sin LLM: el
texto, si lo hay, es de T85.e.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

from nocturna.domain.archive import ArchiveSolution, catalog_solution_from_archive
from nocturna.domain.entities import (
    CatalogSolution,
    MeasuredParameter,
    ReferenceRole,
    SolutionComparison,
    SolutionTensionData,
    SolutionTensionLabel,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.tension import (
    PUBLISHED_CONFIRMED,
    PeriodCheck,
    PeriodRule,
    check_period_values,
    pairwise_sigma,
    to_canonical,
)


class SkipReason(StrEnum):
    NOT_PUBLISHED_CONFIRMED = "not_published_confirmed"
    SAME_REFERENCE = "same_reference"
    OLD_PAPER = "old_paper"
    BELOW_THRESHOLD = "below_threshold"
    PERIOD_MIN_DIFFERENCE = "period_min_difference"
    PERIOD_ALIAS = "period_alias"
    PERIOD_TTV = "period_ttv"
    NO_REFERENCE = "no_reference"


@dataclass(frozen=True, slots=True)
class SolutionTension:
    """Una alta del archivo frente a su referencia, para un parámetro (foto fija)."""

    new_solution_key: str
    reference_solution_key: str
    planet_name: str
    parameter: MeasuredParameter
    new: CatalogSolution
    reference: CatalogSolution
    reference_role: ReferenceRole
    computed_at: datetime
    sigma: float
    others: tuple[SolutionComparison, ...] = ()
    period_check: PeriodCheck | None = None
    labels: tuple[SolutionTensionLabel, ...] = ()
    skip_reason: SkipReason | None = None
    item_id: UUID | None = None
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        if not (self.new.parameter == self.reference.parameter == self.parameter):
            raise InvariantViolation("'new', 'reference' y 'parameter' deben coincidir")
        if self.sigma is None or not math.isfinite(self.sigma) or self.sigma < 0:
            raise InvariantViolation("'sigma' debe ser finito y >= 0")
        if (self.period_check is not None) != (self.parameter == MeasuredParameter.PERIOD):
            raise InvariantViolation(
                "'period_check' va informado si y solo si el parámetro es periodo"
            )
        if self.item_id is not None and self.skip_reason is not None:
            raise InvariantViolation("'item_id' solo se informa si no hay 'skip_reason'")
        if self.new_solution_key == self.reference_solution_key:
            raise InvariantViolation("la alta y su referencia deben ser soluciones distintas")


@dataclass(frozen=True, slots=True)
class SolutionTensionRule:
    """Parámetros del cálculo (`[solution_tensions]`, `[tension]` y
    `[tension.period]`). `since` es la fecha de entrada mínima de las altas
    (D4); `max_pubdate_age_months`, el desfase máximo entre `pl_pubdate` y
    `releasedate` de la alta (D6)."""

    threshold_sigma: float
    since: date
    max_pubdate_age_months: int
    period_rule: PeriodRule

    def __post_init__(self) -> None:
        if not math.isfinite(self.threshold_sigma) or self.threshold_sigma <= 0:
            raise InvariantViolation("'threshold_sigma' debe ser finito y mayor que cero")
        if self.max_pubdate_age_months < 1:
            raise InvariantViolation("'max_pubdate_age_months' debe ser >= 1")


def usable_solution(
    sol: ArchiveSolution, parameter: MeasuredParameter, *, is_default: bool
) -> CatalogSolution | None:
    """`CatalogSolution` de `parameter` si la fila sirve para comparar (valor
    puntual, errores > 0; la masa solo con `pl_bmassprov = 'Mass'`); si no, `None`."""
    candidate = catalog_solution_from_archive(sol, parameter, is_default=is_default)
    return candidate if candidate is not None and candidate.usable_as_prior else None


def compare_solutions(new: CatalogSolution, reference: CatalogSolution) -> float:
    """σ de `new` frente a `reference` (ADR 0015): unidad canónica y, de cada
    error, el lado que mira al otro valor. `InvariantViolation` si el parámetro
    difiere o alguna no es utilizable como previa."""
    if new.parameter != reference.parameter:
        raise InvariantViolation("las dos soluciones deben ser del mismo parámetro")
    if not (new.usable_as_prior and reference.usable_as_prior):
        raise InvariantViolation("las dos soluciones deben ser utilizables como previa")
    assert new.err_plus is not None and new.err_minus is not None  # noqa: S101
    assert reference.err_plus is not None and reference.err_minus is not None  # noqa: S101
    _, _, sigma = pairwise_sigma(
        to_canonical(new.value, new.unit),
        to_canonical(new.err_plus, new.unit),
        to_canonical(new.err_minus, new.unit),
        to_canonical(reference.value, reference.unit),
        to_canonical(reference.err_plus, reference.unit),
        to_canonical(reference.err_minus, reference.unit),
    )
    return sigma


def _most_recent_first(solutions: Sequence[ArchiveSolution]) -> list[ArchiveSolution]:
    """`pl_pubdate` desc, `releasedate` desc, `solution_key` asc (orden de T88)."""
    ordered = sorted(solutions, key=lambda s: s.solution_key)
    ordered.sort(key=lambda s: (s.pl_pubdate or "", s.releasedate), reverse=True)
    return ordered


def select_pair_reference(
    new: ArchiveSolution,
    new_is_default: bool,
    parameter: MeasuredParameter,
    active: Sequence[tuple[ArchiveSolution, bool]],
) -> tuple[ArchiveSolution, bool, ReferenceRole] | None:
    """Referencia de una alta para un parámetro (D5). `active` son las
    soluciones activas del planeta con `is_default_current`. Las filas del
    mismo paper que la alta (`same_reference`) nunca son referencia.

    - Alta no vigente por defecto: la solución por defecto vigente (si hay
      exactamente una y sirve para comparar el parámetro; puede no ser
      `Published Confirmed`, el motivo `not_published_confirmed` lo recoge),
      salvo que sea del mismo paper: entonces, como en el caso siguiente.
    - Alta por defecto (o por defecto del mismo paper): la `Published
      Confirmed` más reciente de OTRO paper que sirva para comparar (rol
      `most_recent`).

    Devuelve `(referencia, is_default_current, rol)` o `None` (sin referencia).
    """
    if not new_is_default:
        defaults = [s for s, d in active if d and s.solution_key != new.solution_key]
        if len(defaults) != 1:
            return None
        reference = defaults[0]
        if not same_reference(new, reference):
            if usable_solution(reference, parameter, is_default=True) is None:
                return None
            return reference, True, ReferenceRole.DEFAULT
    candidates = [
        s
        for s, d in active
        if s.solution_key != new.solution_key
        and not same_reference(new, s)
        and s.soltype == PUBLISHED_CONFIRMED
        and usable_solution(s, parameter, is_default=d) is not None
    ]
    if not candidates:
        return None
    best = _most_recent_first(candidates)[0]
    is_default = any(d for s, d in active if s.solution_key == best.solution_key)
    return best, is_default, ReferenceRole.MOST_RECENT


_PUBDATE = re.compile(r"^(\d{4})-(\d{2})")


def _months_between(pl_pubdate: str | None, releasedate: date) -> int | None:
    """Meses de `pl_pubdate` (`YYYY-MM[...]`) a `releasedate`; `None` si ilegible."""
    if pl_pubdate is None:
        return None
    match = _PUBDATE.match(pl_pubdate.strip())
    if match is None:
        return None
    year, month = int(match.group(1)), int(match.group(2))
    if not 1 <= month <= 12:
        return None
    return (releasedate.year - year) * 12 + (releasedate.month - month)


def is_old_paper(new: ArchiveSolution, rule: SolutionTensionRule) -> bool:
    """Regla 3 (D6): `pl_pubdate` más de M meses anterior a `releasedate`. Un
    `pl_pubdate` nulo o ilegible cuenta como reciente."""
    months = _months_between(new.pl_pubdate, new.releasedate)
    return months is not None and months > rule.max_pubdate_age_months


def same_reference(new: ArchiveSolution, reference: ArchiveSolution) -> bool:
    """Regla 2 (D7): misma `ref_key` o, si las dos traen `arxiv_id`, el mismo."""
    if new.ref_key == reference.ref_key:
        return True
    return new.arxiv_id is not None and new.arxiv_id == reference.arxiv_id


def period_check_between(
    new: CatalogSolution, reference: CatalogSolution, rule: PeriodRule
) -> PeriodCheck:
    """Regla 5 (D8): `[tension.period]` aplicada al periodo de la alta frente al
    de la referencia."""
    return check_period_values(
        [to_canonical(new.value, new.unit)], to_canonical(reference.value, reference.unit), rule
    )


def solution_tension_skip_reason(
    new: ArchiveSolution,
    reference: ArchiveSolution,
    parameter: MeasuredParameter,
    *,
    sigma: float,
    period_check: PeriodCheck | None,
    rule: SolutionTensionRule,
) -> SkipReason | None:
    """Primer motivo por el que el par no pasa, o `None` si es elegible.

    Orden: 1 `Published Confirmed` en los dos lados, 2 referencia distinta,
    3 `old_paper` (solo la alta), `period_ttv` (`ttv_flag` en cualquiera, solo
    periodo), umbral, y en periodo diferencia mínima y alias (regla 5).
    `period_check` es obligatorio para el periodo.
    """
    if new.soltype != PUBLISHED_CONFIRMED or reference.soltype != PUBLISHED_CONFIRMED:
        return SkipReason.NOT_PUBLISHED_CONFIRMED
    if same_reference(new, reference):
        return SkipReason.SAME_REFERENCE
    if is_old_paper(new, rule):
        return SkipReason.OLD_PAPER
    is_period = parameter == MeasuredParameter.PERIOD
    if is_period and (new.ttv_flag or reference.ttv_flag):
        return SkipReason.PERIOD_TTV
    if sigma < rule.threshold_sigma:
        return SkipReason.BELOW_THRESHOLD
    if is_period:
        if period_check is None:
            raise InvariantViolation("un periodo requiere 'period_check'")
        if not period_check.min_difference_met:
            return SkipReason.PERIOD_MIN_DIFFERENCE
        if period_check.alias_suspected:
            return SkipReason.PERIOD_ALIAS
    return None


def labels_for(
    new: ArchiveSolution,
    reference: ArchiveSolution,
    planet_solutions: Sequence[ArchiveSolution],
) -> tuple[SolutionTensionLabel, ...]:
    """Etiquetas (no descartan). `MODEL_DEPENDENT` (D9): la `ref_key` de la alta
    o de la referencia aporta más de una fila activa al planeta
    (`planet_solutions`, todas sus filas activas). `CONTROVERSIAL` (D10):
    `pl_controv_flag` cierto en cualquiera de las dos."""
    labels: list[SolutionTensionLabel] = []
    for ref_key in dict.fromkeys((new.ref_key, reference.ref_key)):
        if sum(1 for s in planet_solutions if s.ref_key == ref_key) > 1:
            labels.append(SolutionTensionLabel.MODEL_DEPENDENT)
            break
    if new.pl_controv_flag or reference.pl_controv_flag:
        labels.append(SolutionTensionLabel.CONTROVERSIAL)
    return tuple(labels)


def build_solution_tension(
    new: ArchiveSolution,
    new_is_default: bool,
    reference: ArchiveSolution,
    reference_is_default: bool,
    role: ReferenceRole,
    parameter: MeasuredParameter,
    active: Sequence[tuple[ArchiveSolution, bool]],
    rule: SolutionTensionRule,
    computed_at: datetime,
) -> SolutionTension | None:
    """`SolutionTension` del par para un parámetro, con σ, `others`, etiquetas y
    motivo de descarte. `None` si alguna de las dos no sirve para comparar el
    parámetro. `others`: σ de la alta frente al resto de `Published Confirmed`
    activas del planeta utilizables (contexto, no decide)."""
    new_cs = usable_solution(new, parameter, is_default=new_is_default)
    reference_cs = usable_solution(reference, parameter, is_default=reference_is_default)
    if new_cs is None or reference_cs is None:
        return None
    sigma = compare_solutions(new_cs, reference_cs)
    period_check = (
        period_check_between(new_cs, reference_cs, rule.period_rule)
        if parameter == MeasuredParameter.PERIOD
        else None
    )
    default_by_key = {s.solution_key: d for s, d in active}
    others: list[SolutionComparison] = []
    for other in _most_recent_first(
        [
            s
            for s, _ in active
            if s.soltype == PUBLISHED_CONFIRMED
            and s.solution_key not in (new.solution_key, reference.solution_key)
        ]
    ):
        other_cs = usable_solution(other, parameter, is_default=default_by_key[other.solution_key])
        if other_cs is not None:
            others.append(SolutionComparison(other_cs, compare_solutions(new_cs, other_cs)))
    return SolutionTension(
        new_solution_key=new.solution_key,
        reference_solution_key=reference.solution_key,
        planet_name=new.pl_name,
        parameter=parameter,
        new=new_cs,
        reference=reference_cs,
        reference_role=role,
        computed_at=computed_at,
        sigma=sigma,
        others=tuple(others),
        period_check=period_check,
        labels=labels_for(new, reference, [s for s, _ in active]),
        skip_reason=solution_tension_skip_reason(
            new,
            reference,
            parameter,
            sigma=sigma,
            period_check=period_check,
            rule=rule,
        ),
    )


#: Motivo con el que el redactor del archivo (T85.e) no toma una tensión.
NOT_ELIGIBLE = "not_eligible"

#: Motivo con el que el redactor del archivo no toma una tensión porque el paper
#: de la alta ya cuenta ese planeta y parámetro por la vía arXiv (T95, ADR 0034).
#: No es un `SkipReason`: ese enum es columna con CHECK y este motivo no se persiste.
SAME_PAPER_ARXIV = "same_paper_arxiv"

#: Clave `(arxiv_id del paper, planeta del archivo, parámetro)` de un hallazgo arXiv.
ArxivFindingKey = tuple[str, str, MeasuredParameter]

#: Marca derivada (no persistida) de una σ extrema; ver `is_extreme_sigma`.
EXTREME_SIGMA_LABEL = "extreme_sigma"


def solution_writer_skip_reason(tension: SolutionTension) -> str | None:
    """Motivo por el que el redactor no toma la tensión, o `None` (T85.e).

    Solo se redacta lo elegible: sin `skip_reason` y con `Item` del archivo
    enlazado (T85.d). Es el único motivo de este nivel; los demás (ya
    redactada, redactor fallido) dependen de la base y viven en el caso de uso."""
    if tension.skip_reason is not None or tension.item_id is None:
        return NOT_ELIGIBLE
    return None


def is_extreme_sigma(sigma: float, extreme_sigma: float) -> bool:
    """Marca derivada de T85.e (D5): la σ supera `[solution_tensions] extreme_sigma`.
    No excluye nada ni se guarda: informa al redactor y al Editor de que la cifra
    es enorme porque los errores son diminutos frente al cambio."""
    return sigma > extreme_sigma


def value_change(new: CatalogSolution, reference: CatalogSolution) -> tuple[float, float]:
    """`(|v_new - v_ref| / v_ref, v_new / v_ref)` en la unidad canónica (T85.e, D3).

    Cualquier parámetro, con ambas soluciones del mismo parámetro; la razón es
    adimensional, así que las unidades de origen no importan."""
    if new.parameter != reference.parameter:
        raise InvariantViolation("'value_change' compara soluciones del mismo parámetro")
    v_new = to_canonical(new.value, new.unit)
    v_ref = to_canonical(reference.value, reference.unit)
    if v_ref == 0:
        raise InvariantViolation("'value_change' no admite una referencia de valor cero")
    return abs(v_new - v_ref) / v_ref, v_new / v_ref


def solution_tension_data_from(
    tension: SolutionTension, *, threshold_sigma: float, archive_url: str
) -> SolutionTensionData:
    """`SolutionTensionData` publicable de una `SolutionTension` elegible (T85.e).

    `InvariantViolation` si no es elegible (`solution_writer_skip_reason`) o si su σ
    no alcanza el umbral vigente (el umbral pudo subir tras calcularla)."""
    if solution_writer_skip_reason(tension) is not None:
        raise InvariantViolation("la tensión no es elegible para el redactor")
    if tension.sigma < threshold_sigma:
        raise InvariantViolation("la σ de la tensión no alcanza el umbral vigente")
    return SolutionTensionData(
        planet_name=tension.planet_name,
        parameter=tension.parameter,
        archive_url=archive_url,
        threshold_sigma=threshold_sigma,
        sigma=tension.sigma,
        new=tension.new,
        reference=tension.reference,
        others=tension.others,
        labels=tension.labels,
        reference_role=tension.reference_role,
    )


def told_by_arxiv(tension: SolutionTension, arxiv_keys: frozenset[ArxivFindingKey]) -> bool:
    """Cierto si el paper de la alta ya tiene hallazgo arXiv para este planeta y
    parámetro (T95, ADR 0034). Sin `arxiv_id` en la alta no hay cruce posible."""
    arxiv_id = tension.new.arxiv_id
    if arxiv_id is None:
        return False
    return (arxiv_id, tension.planet_name, tension.parameter) in arxiv_keys
