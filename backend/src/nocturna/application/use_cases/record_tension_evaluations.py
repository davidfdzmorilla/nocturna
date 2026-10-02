"""Caso de uso: registrar las evaluaciones de tensión de las lecturas con medidas (T88).

Calcula con `ComputeTensions` y reconcilia con lo guardado, por clave
(reading_id, planeta del Reader, parámetro):

- falta: se crea;
- existe en `AWAITING_REFERENCE`: se reevalúa (`reevaluate_with`) y solo se
  actualiza si el resultado cambió (`same_outcome`);
- existe en cualquier otro estado: terminal, no se toca.

Una `Reading` es inmutable: si ya tiene evaluaciones y NINGUNA está en
`AWAITING_REFERENCE`, no se recalcula (no se consulta el catálogo ni el alias);
sus evaluaciones guardadas se cuentan en `kept_terminal`. El catálogo solo se
consulta para lecturas nuevas o con alguna evaluación pendiente.

Con `dry_run` calcula y cuenta pero no escribe. No hace commit (frontera
transaccional del llamador), no llama a Claude y no captura excepciones del
catálogo.
"""

from collections import Counter
from dataclasses import dataclass, field
from uuid import UUID

from nocturna.application.use_cases.compute_tensions import (
    ComputeTensions,
    SkippedMeasurement,
)
from nocturna.domain.entities import Item, Reading
from nocturna.domain.repositories import (
    ItemRepository,
    ReadingRepository,
    TensionEvaluationRepository,
)
from nocturna.domain.tension import EvaluationStatus, TensionEvaluation


@dataclass(frozen=True, slots=True)
class EvaluationRunReport:
    created: int
    reevaluated: int
    unchanged: int
    kept_terminal: int
    # Recuento por estado del resultado de esta pasada (también en dry-run).
    by_status: dict[EvaluationStatus, int] = field(default_factory=dict)
    skipped: tuple[SkippedMeasurement, ...] = ()
    evaluations: tuple[TensionEvaluation, ...] = ()


class RecordTensionEvaluations:
    def __init__(
        self,
        *,
        readings: ReadingRepository,
        items: ItemRepository,
        evaluations: TensionEvaluationRepository,
        compute: ComputeTensions,
    ) -> None:
        self._readings = readings
        self._items = items
        self._evaluations = evaluations
        self._compute = compute

    async def __call__(self, *, dry_run: bool) -> EvaluationRunReport:
        stored = self._evaluations.all()
        existing = {(e.reading_id, e.planet_name, e.parameter): e for e in stored}
        by_reading: dict[UUID, list[TensionEvaluation]] = {}
        for evaluation in stored:
            by_reading.setdefault(evaluation.reading_id, []).append(evaluation)

        pairs: list[tuple[Item, Reading]] = []
        settled: list[TensionEvaluation] = []
        for reading in self._readings.with_measurements():
            previous = by_reading.get(reading.id, [])
            if previous and all(e.status != EvaluationStatus.AWAITING_REFERENCE for e in previous):
                settled.extend(previous)
                continue
            item = self._items.get(reading.item_id)
            if item is not None:
                pairs.append((item, reading))
        report = await self._compute(pairs)

        created = reevaluated = unchanged = 0
        kept_terminal = len(settled)
        by_status: Counter[EvaluationStatus] = Counter(e.status for e in settled)
        final: list[TensionEvaluation] = list(settled)
        for new in report.evaluations:
            old = existing.get((new.reading_id, new.planet_name, new.parameter))
            if old is None:
                created += 1
                if not dry_run:
                    self._evaluations.add(new)
                resulting = new
            elif old.status != EvaluationStatus.AWAITING_REFERENCE:
                kept_terminal += 1
                resulting = old
            else:
                candidate = old.reevaluate_with(new)
                if old.same_outcome(candidate):
                    unchanged += 1
                    resulting = old
                else:
                    reevaluated += 1
                    if not dry_run:
                        self._evaluations.update(candidate)
                    resulting = candidate
            by_status[resulting.status] += 1
            final.append(resulting)
        return EvaluationRunReport(
            created=created,
            reevaluated=reevaluated,
            unchanged=unchanged,
            kept_terminal=kept_terminal,
            by_status=dict(by_status),
            skipped=report.skipped,
            evaluations=tuple(final),
        )
