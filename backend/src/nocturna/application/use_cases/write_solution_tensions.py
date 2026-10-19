"""Caso de uso: el redactor escribe los `Finding` `solution_tension` (T85.e, ADR 0030).

Misma forma que `write_tensions.py` (T76, ADR 0026) para la vía del archivo:
trabaja sobre las `SolutionTension` ya calculadas por T85 (sin red ni LLM en
ese cálculo). `SelectSolutionTensions.pending()` es solo lectura: elige qué
filas se redactan y por qué se excluye el resto. `WriteSolutionTensions.__call__`
redacta UNA fila: toda la maquinaria de gasto (`BudgetGuard.authorize`,
llamada al proveedor, reintento, `AgentCall`) vive en `AgentRunner` con
`role=WRITER`; este módulo no recibe `BudgetGuard` ni llama a
`provider.run_agent` por su cuenta. `BudgetDenied` se propaga sin traducir: lo
trata `RunNight`.

El `Finding` nace sin publicar (decide el Editor) y no cambia `Item.status`.
Las cifras, la referencia y el σ viajan en `Finding.solution_tension`,
calculadas en Python; el modelo solo redacta título y tres niveles. Una alta
con dos parámetros elegibles da dos hallazgos con el mismo `Item` del archivo;
la marca de redactor fallido es por ítem, como en T76.
"""

import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from uuid import UUID

from nocturna.application.agents.prompt_text import inline, num
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.agents.writer_output import parse_writer_output
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.write_tensions import (
    OUTCOME_BY_ATTEMPT,
    WriteTensionResult,
)
from nocturna.domain.entities import (
    ITEM_SOURCE_ARXIV,
    ITEM_SOURCE_EXOPLANET_ARCHIVE,
    AgentCallStatus,
    CatalogSolution,
    Finding,
    FindingType,
    Item,
    ReferenceRole,
    SolutionTensionData,
    SolutionTensionLabel,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.llm import AgentResult, AgentRole, LLMProvider
from nocturna.domain.repositories import (
    AgentCallRepository,
    FindingRepository,
    ItemRepository,
    SolutionTensionRepository,
    TensionEvaluationRepository,
)
from nocturna.domain.solution_tension import (
    EXTREME_SIGMA_LABEL,
    SAME_PAPER_ARXIV,
    ArxivFindingKey,
    SolutionTension,
    is_extreme_sigma,
    solution_tension_data_from,
    solution_writer_skip_reason,
    told_by_arxiv,
    value_change,
)

_logger = logging.getLogger(__name__)

#: Tipos de hallazgo de la vía arXiv que cuentan un planeta y parámetro de un paper
#: (T95, ADR 0034): si existe alguno, publicado o no, el archivo no lo repite.
ARXIV_FINDING_TYPES = (
    FindingType.CATALOG_TENSION,
    FindingType.PRIMERA_MEDIDA,
    FindingType.CONFIRMACION_INDEPENDIENTE,
)

#: Cuántas "otras soluciones" lleva como máximo el bloque del redactor (las más
#: recientes primero, orden de `SolutionTension.others`); el resto se cuenta.
MAX_OTHERS_IN_PROMPT = 8

#: Frase fija que explica cada etiqueta al redactor (D3). Texto del pipeline, no
#: dato de terceros.
LABEL_GLOSS: dict[str, str] = {
    SolutionTensionLabel.MODEL_DEPENDENT.value: (
        "El mismo trabajo publica varias soluciones de este planeta en el archivo "
        "(modelos distintos): la diferencia puede depender de la solución elegida."
    ),
    SolutionTensionLabel.CONTROVERSIAL.value: ("El archivo marca este planeta como controvertido."),
    EXTREME_SIGMA_LABEL: (
        "La σ es enorme porque los errores publicados son diminutos frente al cambio "
        "de valor: cuenta el cambio de valor y no presumas de la cifra de σ."
    ),
}


@dataclass(frozen=True, slots=True)
class SolutionTensionWriterWork:
    """Unidad de trabajo de solo lectura de `pending()`: sin guard ni proveedor."""

    items: ItemRepository
    findings: FindingRepository
    solution_tensions: SolutionTensionRepository
    agent_calls: AgentCallRepository
    evaluations: TensionEvaluationRepository


SolutionTensionWriterWorkFactory = Callable[[], AbstractContextManager[SolutionTensionWriterWork]]


@dataclass(frozen=True, slots=True)
class SolutionTensionCandidate:
    """Tensión elegible: la fila, su `Item` del archivo, el dato publicable y la
    marca derivada `extreme_sigma` (no persistida)."""

    tension: SolutionTension
    item: Item
    data: SolutionTensionData
    extreme: bool = False

    @property
    def labels(self) -> tuple[str, ...]:
        """Etiquetas persistidas más la derivada, en ese orden."""
        base = tuple(label.value for label in self.data.labels)
        return (*base, EXTREME_SIGMA_LABEL) if self.extreme else base


@dataclass(frozen=True, slots=True)
class PendingSolutionTensions:
    """`candidates` en el orden en que se redactan; `skipped` son pares
    `(solution_tension_id, motivo)` de las filas que no se redactan."""

    candidates: tuple[SolutionTensionCandidate, ...]
    skipped: tuple[tuple[UUID, str], ...]


class SelectSolutionTensions:
    """Elige, solo con lectura, qué tensiones del archivo se redactan (T85.e).

    Sin proveedor, sin `AgentRunner` y sin `BudgetGuard`: no puede gastar
    tokens. `max_attempts` es `limits.max_calls_per_item`: con ese número de
    `invalid_output` del redactor sobre el ítem del archivo (en todas las
    noches) no se vuelve a redactar. `extreme_sigma` es
    `[solution_tensions] extreme_sigma`. `planet_overview_url` convierte el
    nombre del planeta en el enlace a su ficha.
    """

    def __init__(
        self,
        *,
        candidates_work: SolutionTensionWriterWorkFactory,
        threshold_sigma: float,
        extreme_sigma: float,
        planet_overview_url: Callable[[str], str],
        max_attempts: int,
    ) -> None:
        self._candidates_work = candidates_work
        self._threshold_sigma = threshold_sigma
        self._extreme_sigma = extreme_sigma
        self._planet_overview_url = planet_overview_url
        self._max_attempts = max_attempts

    @staticmethod
    def _arxiv_finding_keys(w: SolutionTensionWriterWork) -> frozenset[ArxivFindingKey]:
        """Claves `(arxiv_id, planeta, parámetro)` de los hallazgos arXiv ya existentes
        (publicados o no), para no repetirlos por la vía del archivo (T95, ADR 0034).

        Lanza `InvariantViolation` si una evaluación con hallazgo apunta a un `Item`
        inexistente: no se filtra a medias."""
        with_finding: set[UUID] = set()
        for finding_type in ARXIV_FINDING_TYPES:
            with_finding |= w.findings.evaluation_ids_with_finding(finding_type)
        keys: set[ArxivFindingKey] = set()
        for evaluation in w.evaluations.all():
            if evaluation.id not in with_finding or evaluation.archive_planet_name is None:
                continue
            item = w.items.get(evaluation.item_id)
            if item is None:
                raise InvariantViolation(
                    "ItemRepository.get(evaluation.item_id) no debería devolver None: "
                    f"toda evaluación proviene de un Item persistido ({evaluation.item_id})"
                )
            if item.source == ITEM_SOURCE_ARXIV:
                keys.add((item.external_id, evaluation.archive_planet_name, evaluation.parameter))
        return frozenset(keys)

    def pending(self) -> PendingSolutionTensions:
        """Tensiones por redactar, en orden σ desc, `releasedate` de la alta desc, id.
        Solo lectura: no escribe nada ni autoriza gasto."""
        candidates: list[SolutionTensionCandidate] = []
        skipped: list[tuple[UUID, str]] = []
        with self._candidates_work() as w:
            written = w.findings.solution_tension_ids_with_finding()
            arxiv_keys = self._arxiv_finding_keys(w)
            for tension in w.solution_tensions.all():
                reason = solution_writer_skip_reason(tension)
                if reason is not None:
                    skipped.append((tension.id, reason))
                    continue
                assert tension.item_id is not None  # noqa: S101 - garantizado por el motivo
                item = w.items.get(tension.item_id)
                if item is None:
                    raise InvariantViolation(
                        "ItemRepository.get(tension.item_id) no debería devolver None: "
                        f"toda tensión elegible enlaza un Item persistido ({tension.item_id})"
                    )
                if (
                    item.source != ITEM_SOURCE_EXOPLANET_ARCHIVE
                    or item.external_id != tension.new_solution_key
                ):
                    raise InvariantViolation(
                        "el Item de una tensión del archivo debe ser (exoplanet_archive, "
                        f"solution_key de la alta); item={item.id} source={item.source!r}"
                    )
                if tension.id in written:
                    skipped.append((tension.id, "already_written"))
                    continue
                if told_by_arxiv(tension, arxiv_keys):
                    skipped.append((tension.id, SAME_PAPER_ARXIV))
                    continue
                if (
                    w.agent_calls.count_for_item(
                        item.id, AgentRole.WRITER, AgentCallStatus.INVALID_OUTPUT
                    )
                    >= self._max_attempts
                ):
                    skipped.append((tension.id, "writer_failed"))
                    continue
                try:
                    data = solution_tension_data_from(
                        tension,
                        threshold_sigma=self._threshold_sigma,
                        archive_url=self._planet_overview_url(tension.planet_name),
                    )
                except InvariantViolation:
                    skipped.append((tension.id, "invalid_tension"))
                    continue
                candidates.append(
                    SolutionTensionCandidate(
                        tension=tension,
                        item=item,
                        data=data,
                        extreme=is_extreme_sigma(tension.sigma, self._extreme_sigma),
                    )
                )
        candidates.sort(
            key=lambda c: (
                -c.tension.sigma,
                -(c.tension.new.releasedate or date.min).toordinal(),
                str(c.tension.id),
            )
        )
        return PendingSolutionTensions(candidates=tuple(candidates), skipped=tuple(skipped))


class WriteSolutionTensions:
    """Redacta tensiones del archivo con el redactor: una conversación nueva por fila.

    Toda la configuración llega por constructor desde `cli.py`.
    `max_attempts` es `limits.max_calls_per_item`. La estimación de coste es
    `budget.writer_estimated_tokens`, la misma que `WriteTensions` (D10).
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

    async def __call__(self, candidate: SolutionTensionCandidate) -> WriteTensionResult:
        """Redacta `candidate` (hasta `max_attempts` intentos si el JSON no valida).
        `BudgetDenied` sale sin traducir."""
        item, tension = candidate.item, candidate.tension

        def _build_finding(result: AgentResult, run_id: UUID) -> Finding:
            parsed = parse_writer_output(result.output_text)
            return Finding(
                item_id=item.id,
                run_id=run_id,
                type=FindingType.SOLUTION_TENSION,
                title=parsed.title,
                level_curious=parsed.level_curious,
                level_amateur=parsed.level_amateur,
                level_technical=parsed.level_technical,
                solution_tension=candidate.data,
                solution_tension_id=tension.id,
            )

        runner_result = await self._runner.run(
            prompt=self._build_prompt(candidate), item_id=item.id, build=_build_finding
        )
        finding = runner_result.value
        if runner_result.outcome is AttemptOutcome.OK and finding is not None:
            with self._work() as w:
                w.findings.add(finding)
        return WriteTensionResult(
            outcome=OUTCOME_BY_ATTEMPT[runner_result.outcome],
            finding=finding,
            attempts=runner_result.attempts,
            tokens_spent=runner_result.tokens_spent,
        )

    @staticmethod
    def _solution_text(solution: CatalogSolution) -> str:
        errors = (
            f"(+{num(solution.err_plus)}/-{num(solution.err_minus)})"
            if solution.err_plus is not None and solution.err_minus is not None
            else ""
        )
        return f"{solution.reference}; {num(solution.value)} {errors} {solution.unit.value}"

    @classmethod
    def _build_prompt(cls, candidate: SolutionTensionCandidate) -> str:
        """Contenido de usuario: los datos de la tensión, sin abstract ni título del ítem.

        El único texto de terceros es la referencia de cada solución (`ref_text`
        del archivo); todo valor de texto libre pasa por `inline`: una sola
        línea y sin `<` ni `>`, para que ningún dato pueda cerrar
        `</solution_tension>` ni abrir líneas nuevas. No incluye `pl_pubdate`.
        """
        data = candidate.data
        new, reference = data.new, data.reference
        role = "default" if data.reference_role is ReferenceRole.DEFAULT else "most_recent"
        lines = [
            "<solution_tension>",
            f"planet: {inline(data.planet_name)}",
            f"parameter: {data.parameter.value}",
            inline(
                f"new_solution: {cls._solution_text(new)}; "
                f"{'default' if new.is_default else 'not_default'}"
            ),
            inline(
                f"reference_solution: {cls._solution_text(reference)}; "
                f"{'default' if reference.is_default else 'not_default'}; role={role}"
            ),
            f"sigma: {data.sigma:.2f} (threshold {data.threshold_sigma})",
        ]
        rel_diff, ratio = value_change(new, reference)
        lines.append(
            f"value_change: relative_difference={rel_diff:.4f} "
            f"(|new-reference|/reference); ratio={ratio:.4f} (new/reference, "
            "same unit)"
        )
        if new.releasedate is not None:
            lines.append(f"new_solution_entered_archive: {new.releasedate:%Y-%m}")
        lines.append("other_solutions_sigma_of_new_vs_each:")
        shown = data.others[:MAX_OTHERS_IN_PROMPT]
        for other in shown:
            lines.append(inline(f"- {cls._solution_text(other.solution)}: sigma {other.sigma:.2f}"))
        if len(data.others) > len(shown):
            lines.append(f"- (and {len(data.others) - len(shown)} more)")
        if not data.others:
            lines.append("- (none)")
        labels = candidate.labels
        if labels:
            lines.append("labels:")
            for label in labels:
                lines.append(f"- {label}: {LABEL_GLOSS[label]}")
        else:
            lines.append("labels: none")
        lines.append("</solution_tension>")
        return "\n".join(lines)
