"""Caso de uso: tensiones entre soluciones del archivo (T85, D2-D12).

Para cada alta `Published Confirmed` activa del NASA Exoplanet Archive con
`releasedate >= [solution_tensions] since`, y para cada parámetro (masa, radio,
periodo) con valor utilizable en la alta, busca su referencia (D5) y guarda una
`SolutionTension` con σ, contexto (`others`), etiquetas y, si no pasa los
filtros o el umbral, su `skip_reason`. Una foto fija por (alta, parámetro):
idempotente por `keys_computed`.

Decisión de T85: si la alta no tiene valor utilizable en un parámetro, o no hay
referencia posible, NO se guarda fila (la referencia es obligatoria en la
tabla); las segundas se cuentan en `SolutionTensionReport.no_reference`.

Solo para las altas con algún par elegible (D12) se crea (get-or-create) un
`Item` del archivo y se enlaza con `set_item`. Ese ítem nace `NEW` pero
`next_unread` solo devuelve `source = 'arxiv'`: ningún agente lo lee.

Control de gasto: este módulo NO llama a ningún agente. No recibe
`LLMProvider`, `BudgetGuard` ni `AgentWorkFactory`; su unidad de trabajo
(`SolutionTensionWork`) solo expone repositorios. Con `dry_run` calcula y
informa lo mismo pero no escribe.
"""

import logging
from collections import Counter
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime, time

from nocturna.domain.archive import ArchiveSolution
from nocturna.domain.clock import Clock
from nocturna.domain.entities import (
    ITEM_SOURCE_EXOPLANET_ARCHIVE,
    Item,
    MeasuredParameter,
)
from nocturna.domain.repositories import (
    ArchiveItemRepository,
    ArchiveRepository,
    SolutionTensionRepository,
)
from nocturna.domain.solution_tension import (
    SkipReason,
    SolutionTension,
    SolutionTensionRule,
    build_solution_tension,
    select_pair_reference,
    usable_solution,
)

_logger = logging.getLogger(__name__)

ARCHIVE_ITEM_CATEGORY = "exoplanet_archive"


@dataclass(frozen=True, slots=True)
class SolutionTensionWork:
    """Contenido de la unidad de trabajo: solo lo que lee y escribe. Sin
    `BudgetGuard` a propósito (el cálculo no gasta tokens)."""

    archive: ArchiveRepository
    solution_tensions: SolutionTensionRepository
    items: ArchiveItemRepository


SolutionTensionWorkFactory = Callable[[], AbstractContextManager[SolutionTensionWork]]


@dataclass(frozen=True, slots=True)
class NoReference:
    """Alta con valor utilizable en un parámetro pero sin referencia posible."""

    planet_name: str
    solution_key: str
    parameter: MeasuredParameter


@dataclass(frozen=True, slots=True)
class SolutionTensionReport:
    dry_run: bool
    altas: int  # altas PC activas desde `since`
    already_computed: int  # pares (alta, parámetro) ya guardados, ignorados
    computed: tuple[SolutionTension, ...] = ()
    no_reference: tuple[NoReference, ...] = ()
    # Altas con algún par elegible: ítems creados (o que se crearían en dry-run).
    items_created: int = 0
    items_reused: int = 0

    @property
    def eligible(self) -> tuple[SolutionTension, ...]:
        return tuple(t for t in self.computed if t.skip_reason is None)

    @property
    def excluded(self) -> tuple[SolutionTension, ...]:
        return tuple(t for t in self.computed if t.skip_reason is not None)

    @property
    def by_skip_reason(self) -> dict[SkipReason, int]:
        counts = Counter(t.skip_reason for t in self.computed if t.skip_reason is not None)
        return {reason: counts[reason] for reason in SkipReason if reason in counts}


def archive_item_for(alta: ArchiveSolution, reference: ArchiveSolution, *, now: datetime) -> Item:
    """`Item` del archivo de una alta (D12): plantilla determinista, sin IA."""
    assert now.tzinfo is not None  # noqa: S101 - el reloj entrega hora aware
    return Item(
        source=ITEM_SOURCE_EXOPLANET_ARCHIVE,
        external_id=alta.solution_key,
        title=f"{alta.pl_name}: {alta.ref_text}",
        abstract=(
            f"Nueva solución de {alta.pl_name} en el NASA Exoplanet Archive: "
            f"{alta.ref_text} (entrada del {alta.releasedate.isoformat()}), "
            f"comparada con {reference.ref_text}."
        ),
        categories=[ARCHIVE_ITEM_CATEGORY],
        published_at=datetime.combine(alta.releasedate, time(0, 0), tzinfo=now.tzinfo),
        fetched_at=now,
    )


class ComputeSolutionTensions:
    def __init__(
        self, work: SolutionTensionWorkFactory, rule: SolutionTensionRule, clock: Clock
    ) -> None:
        self._work = work
        self._rule = rule
        self._clock = clock

    def __call__(self, *, dry_run: bool) -> SolutionTensionReport:
        now = self._clock.now()
        with self._work() as work:
            altas = work.archive.confirmed_added_since(self._rule.since)
            done = work.solution_tensions.keys_computed()
            active_by_planet = work.archive.active_solutions_of({s.pl_name for s, _ in altas})

            computed: list[SolutionTension] = []
            no_reference: list[NoReference] = []
            already = 0
            items_created = 0
            items_reused = 0

            for alta, alta_is_default in altas:
                active = active_by_planet.get(alta.pl_name, [])
                for_alta: list[tuple[SolutionTension, ArchiveSolution]] = []
                for parameter in MeasuredParameter:
                    if (alta.solution_key, parameter) in done:
                        already += 1
                        continue
                    if usable_solution(alta, parameter, is_default=alta_is_default) is None:
                        continue
                    picked = select_pair_reference(alta, alta_is_default, parameter, active)
                    if picked is None:
                        no_reference.append(NoReference(alta.pl_name, alta.solution_key, parameter))
                        continue
                    reference, reference_is_default, role = picked
                    tension = build_solution_tension(
                        alta,
                        alta_is_default,
                        reference,
                        reference_is_default,
                        role,
                        parameter,
                        active,
                        self._rule,
                        now,
                    )
                    if tension is None:
                        continue
                    for_alta.append((tension, reference))
                computed.extend(t for t, _ in for_alta)
                if not dry_run:
                    for tension, _ in for_alta:
                        work.solution_tensions.add(tension)

                passing = [(t, r) for t, r in for_alta if t.skip_reason is None]
                if not passing:
                    continue
                item = archive_item_for(alta, passing[0][1], now=now)
                existing = work.items.get_by_external_id(item.source, item.external_id)
                if existing is not None:
                    items_reused += 1
                else:
                    items_created += 1
                if dry_run:
                    continue
                if existing is None:
                    work.items.add_many([item])
                item_id = item.id if existing is None else existing.id
                for tension, _ in passing:
                    work.solution_tensions.set_item(tension.id, item_id)

        report = SolutionTensionReport(
            dry_run=dry_run,
            altas=len(altas),
            already_computed=already,
            computed=tuple(computed),
            no_reference=tuple(no_reference),
            items_created=items_created,
            items_reused=items_reused,
        )
        _logger.info(
            "solution_tensions_computed",
            extra={
                "dry_run": dry_run,
                "altas": report.altas,
                "computed": len(report.computed),
                "eligible": len(report.eligible),
                "no_reference": len(report.no_reference),
                "already_computed": already,
            },
        )
        return report
