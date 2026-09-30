"""Caso de uso: calcular la tensión de las medidas de un paper frente al catálogo (T73).

Solo produce comparaciones y omisiones; no aplica umbral (eso es de
`TensionResult.is_candidate`), no captura excepciones del puerto
(`ExoplanetCatalog`), no llama a Claude y no hace IO propia (ADR 0012).

Flujo por par (`Item`, `Reading`):

1. `reading.item_id` debe ser `item.id`; si no, `InvariantViolation`.
2. `measurements` `None` o vacío: no hay nada que hacer ni que consultar.
3. Medida no `usable_for_tension`: `NOT_USABLE`, sin tocar el catálogo.
4. `resolve_planet` devuelve `None`: `UNMATCHED`, sin llamar a `solutions`.
5. Las restantes se agrupan por (nombre canónico, parámetro) dentro del
   ítem, y `solutions` se llama una vez por grupo.
6. Se excluyen las soluciones cuyo `arxiv_id` es el `external_id` del ítem
   (el propio paper) y las no `usable_as_prior`.
7. Sin previas: `NO_PRIORS` para todas las medidas del grupo. Si las hay:
   un `TensionResult` con el producto medidas × previas.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from nocturna.domain.catalog import ExoplanetCatalog
from nocturna.domain.entities import Item, MeasuredParameter, Measurement, Reading
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.tension import TensionResult, compare


class SkipReason(StrEnum):
    """Por qué una medida no produjo comparaciones."""

    NOT_USABLE = "not_usable"
    UNMATCHED = "unmatched"
    NO_PRIORS = "no_priors"


@dataclass(frozen=True, slots=True)
class SkippedMeasurement:
    item_id: UUID
    measurement: Measurement
    reason: SkipReason


@dataclass(frozen=True, slots=True)
class TensionReport:
    results: tuple[TensionResult, ...]
    skipped: tuple[SkippedMeasurement, ...]


class ComputeTensions:
    def __init__(self, catalog: ExoplanetCatalog) -> None:
        self._catalog = catalog

    async def __call__(self, pairs: Sequence[tuple[Item, Reading]]) -> TensionReport:
        results: list[TensionResult] = []
        skipped: list[SkippedMeasurement] = []
        for item, reading in pairs:
            if reading.item_id != item.id:
                raise InvariantViolation("el Reading no corresponde al Item indicado")
            await self._process(item, reading, results, skipped)
        return TensionReport(results=tuple(results), skipped=tuple(skipped))

    async def _process(
        self,
        item: Item,
        reading: Reading,
        results: list[TensionResult],
        skipped: list[SkippedMeasurement],
    ) -> None:
        if not reading.measurements:
            return
        groups: dict[tuple[str, MeasuredParameter], list[Measurement]] = {}
        for measurement in reading.measurements:
            if not measurement.usable_for_tension:
                skipped.append(SkippedMeasurement(item.id, measurement, SkipReason.NOT_USABLE))
                continue
            canonical = await self._catalog.resolve_planet(measurement.planet_name)
            if canonical is None:
                skipped.append(SkippedMeasurement(item.id, measurement, SkipReason.UNMATCHED))
                continue
            groups.setdefault((canonical, measurement.parameter), []).append(measurement)

        for (canonical, parameter), measurements in groups.items():
            solutions = await self._catalog.solutions(canonical, parameter)
            priors = [s for s in solutions if s.arxiv_id != item.external_id and s.usable_as_prior]
            if not priors:
                skipped.extend(
                    SkippedMeasurement(item.id, m, SkipReason.NO_PRIORS) for m in measurements
                )
                continue
            results.append(
                TensionResult(
                    item_id=item.id,
                    planet_name=canonical,
                    parameter=parameter,
                    comparisons=tuple(compare(m, p) for m in measurements for p in priors),
                )
            )
