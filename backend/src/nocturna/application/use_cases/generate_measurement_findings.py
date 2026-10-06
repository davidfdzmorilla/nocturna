"""Caso de uso: generar los `Finding` `primera_medida` y `confirmacion_independiente` (T89).

Lee las `TensionEvaluation` ya guardadas (T88): ni red, ni catálogo, ni
evaluación nueva. Filtra las elegibles con las reglas puras de
`domain/measurement_findings.py`, descarta las que ya tienen un `Finding` de
ese tipo (publicado o no: el índice único `(tension_evaluation_id, type)` no
permite regenerarlo y un candidato rechazado por el Editor no se vuelve a
ofrecer), aplica el tope `max_candidates` (por Run: descuenta los ya generados con ese
`run_id`) con un orden determinista y
persiste los `Finding` sin publicar, con el `run_id` de la noche. El Editor
los decide después, en su única llamada (`EditNight`).

Control de gasto: este módulo NO llama a ningún agente. No construye
`AgentRunner`, no recibe `LLMProvider` ni `BudgetGuard` y su unidad de
trabajo (`MeasurementFindingsWork`) ni siquiera expone el guard: el texto sale
de plantillas deterministas (`measurement_finding_texts.py`). `Item.status`
no cambia.

Con `confirmation_enabled=False` (D6) no se crea ninguna
`confirmacion_independiente`: las elegibles se cuentan como bloqueadas en el
informe. Con `dry_run` se calcula y se informa lo mismo pero no se escribe.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from nocturna.application.measurement_finding_texts import (
    render_confirmacion_independiente,
    render_primera_medida,
)
from nocturna.domain.clock import Clock
from nocturna.domain.entities import (
    MEASUREMENT_FINDING_PARAMETERS,
    Finding,
    FindingType,
    Item,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.measurement_findings import (
    confirmation_eligible,
    first_measurement_eligible,
    first_measurement_from,
    independent_confirmation_from,
)
from nocturna.domain.repositories import (
    FindingRepository,
    ItemRepository,
    TensionEvaluationRepository,
)
from nocturna.domain.tension import EvaluationStatus, TensionEvaluation

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MeasurementFindingsWork:
    """Contenido de una unidad de trabajo del generador: solo lo que lee y
    escribe. Sin `BudgetGuard` a propósito (la generación no gasta tokens)."""

    items: ItemRepository
    findings: FindingRepository
    evaluations: TensionEvaluationRepository


MeasurementFindingsWorkFactory = Callable[[], AbstractContextManager[MeasurementFindingsWork]]


@dataclass(frozen=True, slots=True)
class BlockedConfirmation:
    """Evaluación elegible como `confirmacion_independiente` que no se genera
    porque `confirmation_enabled` es falso."""

    evaluation_id: UUID
    item_id: UUID
    external_id: str
    planet_name: str
    parameter: str


@dataclass(frozen=True, slots=True)
class MeasurementFindingsReport:
    """`created` son los `Finding` generados (en `dry_run`, los que se
    generarían: no persistidos). `deferred` son elegibles que no caben en
    `max_candidates` esta noche (se generan en las siguientes)."""

    created: tuple[Finding, ...]
    blocked_confirmations: tuple[BlockedConfirmation, ...]
    already_generated: int
    deferred: int
    dry_run: bool

    @property
    def primera_medida(self) -> int:
        return sum(1 for f in self.created if f.type is FindingType.PRIMERA_MEDIDA)

    @property
    def confirmacion_independiente(self) -> int:
        return sum(1 for f in self.created if f.type is FindingType.CONFIRMACION_INDEPENDIENTE)


@dataclass(frozen=True, slots=True)
class _Candidate:
    rank: int
    evaluation: TensionEvaluation
    item: Item

    def sort_key(self) -> tuple:
        return (
            self.rank,
            self.item.published_at,
            self.item.external_id,
            self.evaluation.planet_name,
            self.evaluation.parameter.value,
            str(self.evaluation.id),
        )


_GENERATING_STATUSES = frozenset({EvaluationStatus.AWAITING_REFERENCE, EvaluationStatus.EVALUATED})
_MEASUREMENT_TYPES = (FindingType.PRIMERA_MEDIDA, FindingType.CONFIRMACION_INDEPENDIENTE)
_FIRST_RANK = 0
_CONFIRMATION_RANK = 1


class GenerateMeasurementFindings:
    """Genera los `Finding` de medidas de las evaluaciones guardadas.

    Toda la configuración llega por constructor desde `cli.py`.
    `planet_overview_url` es la función que convierte el nombre del planeta
    en el archivo en el enlace a su ficha (D14): `application/` no conoce
    el formato de la URL.
    """

    def __init__(
        self,
        *,
        work: MeasurementFindingsWorkFactory,
        clock: Clock,
        planet_overview_url: Callable[[str], str],
        max_candidates: int,
        max_sigma: float,
        window_days: int,
        confirmation_enabled: bool,
    ) -> None:
        self._work = work
        self._clock = clock
        self._planet_overview_url = planet_overview_url
        self._max_candidates = max_candidates
        self._max_sigma = max_sigma
        self._window_days = window_days
        self._confirmation_enabled = confirmation_enabled

    def __call__(self, *, run_id: UUID, dry_run: bool) -> MeasurementFindingsReport:
        now = self._clock.now()
        with self._work() as w:
            done_first = w.findings.evaluation_ids_with_finding(FindingType.PRIMERA_MEDIDA)
            done_confirmation = w.findings.evaluation_ids_with_finding(
                FindingType.CONFIRMACION_INDEPENDIENTE
            )
            already_generated = 0
            blocked: list[BlockedConfirmation] = []
            candidates: list[_Candidate] = []
            for evaluation in w.evaluations.all():
                # Filtro barato antes de cargar el Item: solo masa/radio y
                # solo los estados de los que sale un finding.
                if (
                    evaluation.parameter not in MEASUREMENT_FINDING_PARAMETERS
                    or evaluation.status not in _GENERATING_STATUSES
                ):
                    continue
                if first_measurement_eligible(evaluation):
                    if evaluation.id in done_first:
                        already_generated += 1
                        continue
                    item = self._item(w, evaluation)
                    candidates.append(_Candidate(_FIRST_RANK, evaluation, item))
                    continue
                if evaluation.id in done_confirmation:
                    already_generated += 1
                    continue
                item = self._item(w, evaluation)
                if not confirmation_eligible(
                    evaluation,
                    item_published_at=item.published_at,
                    now=now,
                    max_sigma=self._max_sigma,
                    window_days=self._window_days,
                ):
                    continue
                if not self._confirmation_enabled:
                    blocked.append(
                        BlockedConfirmation(
                            evaluation_id=evaluation.id,
                            item_id=item.id,
                            external_id=item.external_id,
                            planet_name=evaluation.planet_name,
                            parameter=evaluation.parameter.value,
                        )
                    )
                    continue
                candidates.append(_Candidate(_CONFIRMATION_RANK, evaluation, item))

            candidates.sort(key=_Candidate.sort_key)
            # El tope es por Run, no por invocación: se restan los findings
            # de medidas que este run_id ya tenga (reanudación).
            already_in_run = w.findings.count_for_run(run_id, _MEASUREMENT_TYPES)
            capacity = max(self._max_candidates - already_in_run, 0)
            selected = candidates[:capacity]
            deferred = len(candidates) - len(selected)
            created = tuple(self._build(c, run_id=run_id, now=now) for c in selected)
            if not dry_run:
                for finding in created:
                    w.findings.add(finding)

        _logger.info(
            "night.measurement_findings",
            extra={
                "event": "night.measurement_findings",
                "run_id": str(run_id),
                "dry_run": dry_run,
                "findings_created": len(created),
                "primera_medida": sum(1 for f in created if f.type is FindingType.PRIMERA_MEDIDA),
                "confirmacion_independiente": sum(
                    1 for f in created if f.type is FindingType.CONFIRMACION_INDEPENDIENTE
                ),
                "blocked_confirmations": len(blocked),
                "already_generated": already_generated,
                "deferred": deferred,
            },
        )
        return MeasurementFindingsReport(
            created=created,
            blocked_confirmations=tuple(blocked),
            already_generated=already_generated,
            deferred=deferred,
            dry_run=dry_run,
        )

    @staticmethod
    def _item(w: MeasurementFindingsWork, evaluation: TensionEvaluation) -> Item:
        item = w.items.get(evaluation.item_id)
        if item is None:
            raise InvariantViolation(
                "ItemRepository.get(evaluation.item_id) no debería devolver None: toda "
                f"evaluación proviene de un Item persistido (item_id={evaluation.item_id})"
            )
        return item

    def _build(self, candidate: _Candidate, *, run_id: UUID, now: datetime) -> Finding:
        evaluation, item = candidate.evaluation, candidate.item
        if candidate.rank == _FIRST_RANK:
            archive_url = (
                None
                if evaluation.archive_planet_name is None
                else self._planet_overview_url(evaluation.archive_planet_name)
            )
            first = first_measurement_from(evaluation, archive_url=archive_url)
            texts = render_primera_medida(first, arxiv_id=item.external_id)
            return Finding(
                item_id=item.id,
                run_id=run_id,
                type=FindingType.PRIMERA_MEDIDA,
                title=texts.title,
                level_curious=texts.level_curious,
                level_amateur=texts.level_amateur,
                level_technical=texts.level_technical,
                first_measurement=first,
                tension_evaluation_id=evaluation.id,
            )
        assert evaluation.archive_planet_name is not None  # noqa: S101
        confirmation = independent_confirmation_from(
            evaluation,
            item_published_at=item.published_at,
            now=now,
            max_sigma=self._max_sigma,
            window_days=self._window_days,
            archive_url=self._planet_overview_url(evaluation.archive_planet_name),
        )
        texts = render_confirmacion_independiente(confirmation, arxiv_id=item.external_id)
        return Finding(
            item_id=item.id,
            run_id=run_id,
            type=FindingType.CONFIRMACION_INDEPENDIENTE,
            title=texts.title,
            level_curious=texts.level_curious,
            level_amateur=texts.level_amateur,
            level_technical=texts.level_technical,
            independent_confirmation=confirmation,
            tension_evaluation_id=evaluation.id,
        )
