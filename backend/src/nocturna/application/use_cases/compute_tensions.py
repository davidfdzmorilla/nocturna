"""Caso de uso: evaluar la tensión de las medidas de un paper frente al catálogo (T73, T88).

Produce una `TensionEvaluation` por (lectura, planeta del Reader, parámetro)
y las omisiones; no aplica umbral para decidir candidatos (eso es de
`TensionEvaluation.is_candidate`), no persiste nada, no captura excepciones
del puerto (`ExoplanetCatalog`), no llama a Claude y no hace IO propia
(ADR 0012).

Flujo por par (`Item`, `Reading`):

1. `reading.item_id` debe ser `item.id`; si no, `InvariantViolation`.
2. `measurements` `None` o vacío: no hay nada que hacer ni que consultar.
3. Medida no `usable_for_tension`: `NOT_USABLE`, sin tocar el catálogo.
4. Las restantes se agrupan por (`planet_name` del Reader, parámetro).
5. `resolve_planet` devuelve `None`: `AWAITING_REFERENCE` sin planeta de
   archivo (no se llama a `solutions`).
6. Se pide `solutions` una vez por grupo. Las del propio paper (`arxiv_id ==
   item.external_id`) se excluyen siempre y se anota su `solution_key`.
7. Periodo con `ttv_flag` en alguna solución del planeta: `PERIOD_TTV`, sin
   evaluación.
8. Con referencia (`select_reference` sobre las previas utilizables):
   `EVALUATED`, comparado con todas las previas utilizables (ADR 0015 §2) y,
   si es un periodo, con `period_check`.
9. Sin referencia pero con cota superior `Published Confirmed`:
   `CONSISTENT_WITH_LIMIT` o `INCOMPATIBLE_WITH_LIMIT`.
10. Sin nada y con solución propia: `CLOSED_LOOP`; sin nada: `AWAITING_REFERENCE`.

`resolve_planet` lanza `PlanetResolutionFailed` (D16, T89) ante una respuesta
anómala del alias: el grupo no produce evaluación y se anota en
`TensionReport.failures`; `None` significa solo "ausente del archivo".
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from nocturna.domain.catalog import ExoplanetCatalog
from nocturna.domain.clock import Clock
from nocturna.domain.entities import (
    Item,
    MeasuredParameter,
    Measurement,
    Reading,
)
from nocturna.domain.errors import InvariantViolation, PlanetResolutionFailed
from nocturna.domain.tension import (
    EvaluationStatus,
    LimitComparison,
    LimitOutcome,
    PeriodCheck,
    PeriodRule,
    TensionEvaluation,
    TensionResult,
    check_period,
    compare,
    compare_with_limit,
    select_reference,
    select_upper_limit,
)


class SkipReason(StrEnum):
    """Por qué una medida no produjo evaluación."""

    NOT_USABLE = "not_usable"
    PERIOD_TTV = "period_ttv"


@dataclass(frozen=True, slots=True)
class SkippedMeasurement:
    item_id: UUID
    measurement: Measurement
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class ResolutionFailure:
    """Grupo (lectura, planeta, parámetro) cuyo planeta no se pudo resolver
    (D16, T89): sin evaluación; se reintenta en la próxima ejecución."""

    item_id: UUID
    planet_name: str
    parameter: MeasuredParameter
    reason: str


@dataclass(frozen=True, slots=True)
class TensionReport:
    evaluations: tuple[TensionEvaluation, ...]
    skipped: tuple[SkippedMeasurement, ...]
    failures: tuple[ResolutionFailure, ...] = ()


class ComputeTensions:
    def __init__(
        self,
        catalog: ExoplanetCatalog,
        *,
        threshold_sigma: float,
        period_rule: PeriodRule,
        clock: Clock,
    ) -> None:
        self._catalog = catalog
        self._threshold = threshold_sigma
        self._period_rule = period_rule
        self._clock = clock

    async def __call__(self, pairs: Sequence[tuple[Item, Reading]]) -> TensionReport:
        evaluations: list[TensionEvaluation] = []
        skipped: list[SkippedMeasurement] = []
        failures: list[ResolutionFailure] = []
        for item, reading in pairs:
            if reading.item_id != item.id:
                raise InvariantViolation("el Reading no corresponde al Item indicado")
            await self._process(item, reading, evaluations, skipped, failures)
        return TensionReport(
            evaluations=tuple(evaluations),
            skipped=tuple(skipped),
            failures=tuple(failures),
        )

    async def _process(
        self,
        item: Item,
        reading: Reading,
        evaluations: list[TensionEvaluation],
        skipped: list[SkippedMeasurement],
        failures: list[ResolutionFailure],
    ) -> None:
        if not reading.measurements:
            return
        groups: dict[tuple[str, MeasuredParameter], list[Measurement]] = {}
        for measurement in reading.measurements:
            if not measurement.usable_for_tension:
                skipped.append(SkippedMeasurement(item.id, measurement, SkipReason.NOT_USABLE))
                continue
            groups.setdefault((measurement.planet_name, measurement.parameter), []).append(
                measurement
            )

        for (planet_name, parameter), measurements in groups.items():
            try:
                evaluation = await self._evaluate_group(
                    item, reading, planet_name, parameter, tuple(measurements), skipped
                )
            except PlanetResolutionFailed as exc:
                failures.append(ResolutionFailure(item.id, planet_name, parameter, str(exc)))
                continue
            if evaluation is not None:
                evaluations.append(evaluation)

    async def _evaluate_group(
        self,
        item: Item,
        reading: Reading,
        planet_name: str,
        parameter: MeasuredParameter,
        measurements: tuple[Measurement, ...],
        skipped: list[SkippedMeasurement],
    ) -> TensionEvaluation | None:
        def build(
            status: EvaluationStatus,
            *,
            archive_planet_name: str | None = None,
            result: TensionResult | None = None,
            limit: LimitComparison | None = None,
            period_check: PeriodCheck | None = None,
            own_solution_key: str | None = None,
        ) -> TensionEvaluation:
            return TensionEvaluation(
                reading_id=reading.id,
                item_id=item.id,
                planet_name=planet_name,
                parameter=parameter,
                measurements=measurements,
                status=status,
                evaluated_at=self._clock.now(),
                archive_planet_name=archive_planet_name,
                result=result,
                limit=limit,
                period_check=period_check,
                own_solution_key=own_solution_key,
            )

        canonical = await self._catalog.resolve_planet(planet_name)
        if canonical is None:
            return build(EvaluationStatus.AWAITING_REFERENCE)

        solutions = await self._catalog.solutions(canonical, parameter)
        if parameter == MeasuredParameter.PERIOD and any(s.ttv_flag for s in solutions):
            skipped.extend(
                SkippedMeasurement(item.id, m, SkipReason.PERIOD_TTV) for m in measurements
            )
            return None

        own = sorted(s.solution_key or "" for s in solutions if s.arxiv_id == item.external_id)
        own_key = own[0] if own else None
        others = [s for s in solutions if s.arxiv_id != item.external_id]
        priors = [s for s in others if s.usable_as_prior]

        result: TensionResult | None = None
        if priors:
            result = TensionResult(
                item_id=item.id,
                planet_name=canonical,
                parameter=parameter,
                comparisons=tuple(compare(m, p) for m in measurements for p in priors),
                reading_id=reading.id,
            )
        reference = select_reference(priors)
        if result is not None and reference is not None:
            period_check = (
                check_period(measurements, reference, self._period_rule)
                if parameter == MeasuredParameter.PERIOD
                else None
            )
            return build(
                EvaluationStatus.EVALUATED,
                archive_planet_name=canonical,
                result=result,
                period_check=period_check,
                own_solution_key=own_key,
            )

        limit_solution = select_upper_limit(others)
        if limit_solution is not None:
            limit = compare_with_limit(
                measurements, limit_solution, threshold_sigma=self._threshold
            )
            status = (
                EvaluationStatus.INCOMPATIBLE_WITH_LIMIT
                if limit.outcome == LimitOutcome.INCOMPATIBLE
                else EvaluationStatus.CONSISTENT_WITH_LIMIT
            )
            return build(
                status, archive_planet_name=canonical, limit=limit, own_solution_key=own_key
            )

        if own_key is not None:
            return build(
                EvaluationStatus.CLOSED_LOOP,
                archive_planet_name=canonical,
                own_solution_key=own_key,
            )
        return build(
            EvaluationStatus.AWAITING_REFERENCE, archive_planet_name=canonical, result=result
        )
