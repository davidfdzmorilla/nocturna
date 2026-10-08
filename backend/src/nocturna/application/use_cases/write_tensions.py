"""Caso de uso: el redactor escribe los `Finding` `catalog_tension` (T76, ADR 0026).

Trabaja sobre las `TensionEvaluation` ya guardadas por `evaluate-tensions`
(sin red en la ventana). `SelectTensions.pending()` es solo lectura: elige, con la regla pura
`catalog_tension_skip_reason`, qué evaluaciones se redactan y por qué se
excluye el resto. `__call__` redacta UNA tensión: toda la maquinaria de gasto
(`BudgetGuard.authorize`, llamada al proveedor, reintento, `AgentCall`) vive
en `AgentRunner` con `role=WRITER`; este módulo no recibe `BudgetGuard` ni
llama a `provider.run_agent` por su cuenta. `BudgetDenied` se propaga sin
traducir: lo trata `RunNight`.

El `Finding` resultante nace sin publicar (decide el Editor) y no cambia
`Item.status`. El texto lo redacta el modelo, pero las cifras, la referencia y
el σ viajan en `Finding.catalog_tension`, calculadas en Python.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from nocturna.application.agents.prompt_text import inline, num
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.agents.writer_output import parse_writer_output
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.domain.entities import (
    AgentCallStatus,
    CatalogTension,
    Finding,
    FindingType,
    Item,
    Measurement,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.llm import AgentResult, AgentRole, LLMProvider
from nocturna.domain.measurement_findings import catalog_tension_skip_reason
from nocturna.domain.own_solution import OwnSolutionRule
from nocturna.domain.repositories import (
    AgentCallRepository,
    FindingRepository,
    ItemRepository,
    TensionEvaluationRepository,
)
from nocturna.domain.tension import TensionEvaluation, catalog_tension_from

_logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class TensionWriterWork:
    """Unidad de trabajo de solo lectura de `pending()`: sin guard ni proveedor."""

    items: ItemRepository
    findings: FindingRepository
    evaluations: TensionEvaluationRepository
    agent_calls: AgentCallRepository


TensionWriterWorkFactory = Callable[[], AbstractContextManager[TensionWriterWork]]


class WriteOutcome(StrEnum):
    WRITTEN = "written"
    INVALID_OUTPUT = "invalid_output"
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"


_OUTCOME_BY_ATTEMPT: dict[AttemptOutcome, WriteOutcome] = {
    AttemptOutcome.OK: WriteOutcome.WRITTEN,
    AttemptOutcome.INVALID_OUTPUT: WriteOutcome.INVALID_OUTPUT,
    AttemptOutcome.AGENT_ERROR: WriteOutcome.AGENT_ERROR,
    AttemptOutcome.TIMEOUT: WriteOutcome.TIMEOUT,
    AttemptOutcome.RATE_LIMITED: WriteOutcome.RATE_LIMITED,
}


@dataclass(frozen=True, slots=True)
class TensionCandidate:
    evaluation: TensionEvaluation
    item: Item
    tension: CatalogTension


@dataclass(frozen=True, slots=True)
class PendingTensions:
    """`candidates` en el orden en que se redactan; `skipped` son pares
    `(evaluation_id, motivo)` de las evaluaciones guardadas que no se redactan."""

    candidates: tuple[TensionCandidate, ...]
    skipped: tuple[tuple[UUID, str], ...]


@dataclass(frozen=True, slots=True)
class WriteTensionResult:
    outcome: WriteOutcome
    finding: Finding | None
    attempts: int
    tokens_spent: int


class SelectTensions:
    """Elige, solo con lectura, qué tensiones guardadas se redactan (T76).

    Sin proveedor, sin `AgentRunner` y sin `BudgetGuard`: no puede gastar
    tokens. `max_attempts` es `limits.max_calls_per_item`: con ese número de
    `invalid_output` del redactor sobre un ítem (en todas las noches) no se
    vuelve a redactar (D6). `planet_overview_url` convierte el nombre del
    planeta en el archivo en el enlace a su ficha (como en T89).
    """

    def __init__(
        self,
        *,
        candidates_work: TensionWriterWorkFactory,
        own_solution_rule: OwnSolutionRule,
        threshold_sigma: float,
        planet_overview_url: Callable[[str], str],
        max_attempts: int,
    ) -> None:
        self._candidates_work = candidates_work
        self._own_rule = own_solution_rule
        self._threshold_sigma = threshold_sigma
        self._planet_overview_url = planet_overview_url
        self._max_attempts = max_attempts

    def pending(self) -> PendingTensions:
        """Tensiones por redactar, en orden `reference_sigma` desc, `published_at`, id.
        Solo lectura: no escribe nada ni autoriza gasto."""
        candidates: list[TensionCandidate] = []
        skipped: list[tuple[UUID, str]] = []
        with self._candidates_work() as w:
            written = w.findings.evaluation_ids_with_finding(FindingType.CATALOG_TENSION)
            for evaluation in w.evaluations.all():
                item = w.items.get(evaluation.item_id)
                if item is None:
                    raise InvariantViolation(
                        "ItemRepository.get(evaluation.item_id) no debería devolver None: "
                        f"toda evaluación proviene de un Item persistido ({evaluation.item_id})"
                    )
                reason = catalog_tension_skip_reason(
                    evaluation,
                    threshold_sigma=self._threshold_sigma,
                    item_external_id=item.external_id,
                    item_published_at=item.published_at,
                    own_rule=self._own_rule,
                )
                if reason is None and evaluation.id in written:
                    reason = "already_written"
                if reason is None and (
                    w.agent_calls.count_for_item(
                        item.id, AgentRole.WRITER, AgentCallStatus.INVALID_OUTPUT
                    )
                    >= self._max_attempts
                ):
                    reason = "writer_failed"
                if reason is not None:
                    skipped.append((evaluation.id, reason))
                    continue
                try:
                    tension = self._tension(evaluation)
                except InvariantViolation:
                    skipped.append((evaluation.id, "invalid_tension"))
                    continue
                candidates.append(TensionCandidate(evaluation, item, tension))
        candidates.sort(
            key=lambda c: (-c.tension.reference_sigma, c.item.published_at, str(c.evaluation.id))
        )
        return PendingTensions(candidates=tuple(candidates), skipped=tuple(skipped))

    def _tension(self, evaluation: TensionEvaluation) -> CatalogTension:
        if evaluation.result is None or evaluation.archive_planet_name is None:
            raise InvariantViolation("la evaluación no tiene resultado ni planeta del archivo")
        return catalog_tension_from(
            evaluation.result,
            threshold_sigma=self._threshold_sigma,
            archive_url=self._planet_overview_url(evaluation.archive_planet_name),
        )


class WriteTensions:
    """Redacta tensiones con el redactor: una conversación nueva por tensión.

    Toda la configuración llega por constructor desde `cli.py`.
    `max_attempts` es `limits.max_calls_per_item`: tope de llamadas por
    tensión. La selección vive en `SelectTensions`.
    """

    def __init__(
        self,
        *,
        work: AgentWorkFactory,
        provider: LLMProvider,
        system_prompt: str,
        prompt_version: str,
        model: str,
        max_turns: int,
        max_attempts: int,
        estimated_tokens: int,
    ) -> None:
        self._work = work
        self._runner = AgentRunner(
            work=work,
            provider=provider,
            role=AgentRole.WRITER,
            model=model,
            system_prompt=system_prompt,
            prompt_version=prompt_version,
            estimated_tokens=estimated_tokens,
            max_turns=max_turns,
            max_attempts=max_attempts,
        )

    async def __call__(self, candidate: TensionCandidate) -> WriteTensionResult:
        """Redacta `candidate` (hasta `max_attempts` intentos si el JSON no valida).
        `BudgetDenied` sale sin traducir."""
        evaluation, item = candidate.evaluation, candidate.item

        def _build_finding(result: AgentResult, run_id: UUID) -> Finding:
            parsed = parse_writer_output(result.output_text)
            return Finding(
                item_id=item.id,
                run_id=run_id,
                type=FindingType.CATALOG_TENSION,
                title=parsed.title,
                level_curious=parsed.level_curious,
                level_amateur=parsed.level_amateur,
                level_technical=parsed.level_technical,
                catalog_tension=candidate.tension,
                tension_evaluation_id=evaluation.id,
            )

        runner_result = await self._runner.run(
            prompt=self._build_prompt(candidate), item_id=item.id, build=_build_finding
        )
        finding = runner_result.value
        if runner_result.outcome is AttemptOutcome.OK and finding is not None:
            with self._work() as w:
                w.findings.add(finding)
        return WriteTensionResult(
            outcome=_OUTCOME_BY_ATTEMPT[runner_result.outcome],
            finding=finding,
            attempts=runner_result.attempts,
            tokens_spent=runner_result.tokens_spent,
        )

    @staticmethod
    def _build_prompt(candidate: TensionCandidate) -> str:
        """Contenido de usuario: los datos de la tensión, sin abstract.

        Todo valor de texto libre (título del ítem, nombres, referencias,
        `evidence`) pasa por `inline`: una sola línea y sin `<` ni `>`, para
        que ningún dato pueda cerrar `</tension>` ni abrir líneas nuevas.
        """
        tension = candidate.tension
        papers: list[Measurement] = []
        for comparison in tension.comparisons:
            if comparison.paper not in papers:
                papers.append(comparison.paper)
        reference = tension.default_prior()

        lines = [
            "<tension>",
            f"item_title: {inline(candidate.item.title)}",
            f"planet: {inline(tension.planet_name)}",
            f"parameter: {tension.parameter.value}",
            "paper_measurements:",
        ]
        for m in papers:
            errors = f"(+{num(m.err_plus)}/-{num(m.err_minus)})" if m.err_plus is not None else ""
            lines.append(
                inline(f"- {num(m.value)} {errors} {m.unit.value}; evidence: {m.evidence}")
            )
        lines.append(
            inline(
                f"reference: {reference.reference}; {num(reference.value)} "
                f"(+{num(reference.err_plus or 0)}/-{num(reference.err_minus or 0)}) "
                f"{reference.unit.value}; default"
            )
        )
        lines.append(
            f"reference_sigma: {tension.reference_sigma:.2f} (threshold {tension.threshold_sigma})"
        )
        lines.append("sigma_against_each_prior:")
        for c in tension.comparisons:
            prior = c.prior
            mark = " [default]" if prior.is_default else ""
            lines.append(
                inline(
                    f"- paper {num(c.paper.value)} {c.paper.unit.value} vs {prior.reference}"
                    f"{mark} {num(prior.value)} "
                    f"(+{num(prior.err_plus or 0)}/-{num(prior.err_minus or 0)}) "
                    f"{prior.unit.value}: sigma {c.sigma:.2f}"
                )
            )
        lines.append("</tension>")
        return "\n".join(lines)
