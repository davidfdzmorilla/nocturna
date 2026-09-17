"""Caso de uso: el Editor decide, en una sola llamada, qué publica la noche.

Cuarto caso de uso sobre `AgentRunner` (`application/agents/runner.py`),
mismo patrón que `ReadItem` (T41) y `PopularizeReading` (T42): toda la
maquinaria de gasto -- el bucle de intentos, la unidad de `authorize` +
`timeout_for_call`, los cuatro `except` del proveedor y el registro de
`AgentCall` -- vive en `AgentRunner`, genérica por `role`. Este módulo solo
aporta lo específico de "editar la noche": la lectura de candidatos, la
guarda de estado por candidato (no fatal, a diferencia de la de `ReadItem`/
`PopularizeReading`), la composición del prompt con solo tres campos por
candidato, la traducción de `EditorOutput` a las transiciones de `Item`/
`Finding` y `estimate_editor_tokens`, la estimación de coste que depende
del número de candidatos y que por eso no puede fijarse por constructor.

## Por qué `AgentRunner` se construye dentro de `__call__`, no en `__init__`

`ReadItem` y `PopularizeReading` reciben `estimated_tokens` ya resuelto por
constructor porque su coste no depende de nada que solo se sepa en tiempo
de ejecución. El Editor sí: su única llamada de la noche lleva todos los
candidatos a la vez, y `estimate_editor_tokens(candidates=..., ...)` solo
puede evaluarse una vez que la unidad de trabajo 1 ha contado cuántos
candidatos hay de verdad. Construir el `AgentRunner` aquí, con ese número ya
resuelto, es la única forma de que la estimación que ve `BudgetGuard.
authorize` sea la real y no un valor fijo por candidato pesimista o
optimista. No se añade un parámetro de `estimated_tokens` a `AgentRunner.
run()` para esto: eso abriría un segundo camino para fijar el coste
estimado justo en la pieza que guarda el presupuesto (regla dura del plan
de esta tarea), y `AgentRunner` ya es barato de construir -- no encapsula
ningún recurso, solo configuración.

## La guarda de estado, por candidato y no fatal

`ReadItem`/`PopularizeReading` exigen el estado de entrada antes de
`authorize` y lanzan `InvalidTransition` si no se cumple: tiene sentido
porque cada uno procesa un único `Item`, y quien lo orquesta debe saber que
se equivocó. El Editor procesa hasta `max_items_per_night` candidatos en una
sola llamada; si uno de ellos llegara en un estado inesperado (una carrera
con otro proceso, un `Item` que otra vía ya publicó o descartó), lanzar
`InvalidTransition` tiraría la llamada más cara de la noche por un único
candidato en mal estado. En su lugar, ese candidato se filtra con un
`warning` de log y el resto sigue su curso -- misma filosofía defensiva que
la guarda de las otras dos clases, adaptada a que aquí "un candidato mal"
no es motivo para no intentar nada.

## Validación de la respuesta del Editor: fallo cerrado

La publicación real nunca sale del JSON del Editor: sale de la lista de
candidatos leída de base de datos en la unidad de trabajo 1, cruzada con
las decisiones del Editor. Un `item_id` que el Editor aprueba pero que no
está entre los candidatos no puede publicar nada por construcción -- se
ignora, se cuenta en `unknown_item_ids` y se registra con `warning`, nunca
como `INVALID_OUTPUT`: reintentar cuesta la llamada más cara de la noche
por una alucinación que, por diseño, no tiene ningún efecto. Un `item_id`
duplicado conserva la primera aparición; las siguientes se ignoran con
`warning` -- un segundo `Finding.publish()` sobre el mismo `Finding`
lanzaría `InvalidTransition`, y no hay ninguna razón editorial para que eso
ocurra con un JSON bien formado.

## Las dos unidades de trabajo del camino con llamada

La primera (sin LLM) solo lee: `unpublished_for_run` y, por cada
`Finding`, el `Item` correspondiente. Ninguna escritura ocurre ahí. La
segunda, después de que `AgentRunner.run` devuelva, hace todas las
transiciones y persistencias de una vez -- todo o nada, igual que pide el
patrón de `unit_of_work.py`/ADR 0006 § 2 -- y nunca se comparte con
`record_call` (esa unidad de trabajo la abre y cierra `AgentRunner`, no
este módulo). Solo se abre si `outcome` es `OK`: en cualquier otro
desenlace (`INVALID_OUTPUT`, `TIMEOUT`, `AGENT_ERROR`, `RATE_LIMITED`) no
hay nada que persistir, y en `NO_CANDIDATES` ni siquiera se llega a
autorizar nada (ver más abajo).

## Lo que este módulo no hace, a propósito

No cuenta "una vez por noche": esa invariante vive en `BudgetGuard.
_call_limit_reason` (`max_editor_calls_per_night`,
`DenyReason.EDITOR_ALREADY_CALLED`) y duplicarla aquí sería exactamente el
error que persigue `budget-guard-review`. No cierra el `Run` ni toca
`Run.status`: eso es de quien orquesta la noche (T44), igual que con
`ReadItem`/`PopularizeReading`. No actualiza `Run.findings_published`: los
contadores del `Run` también son de T44. No persiste el motivo del Editor
en ningún sitio más allá de `EditNightResult.reasons` y el log estructurado
por decisión -- persistirlo exigiría una columna nueva en `findings` que
`CLAUDE.md` no contempla; queda como decisión abierta, no como olvido.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from nocturna.application.agents.editor_output import (
    EditorDecision,
    EditorOutput,
    parse_editor_output,
)
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.domain.clock import Clock
from nocturna.domain.entities import Finding, Item, ItemStatus
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.llm import AgentResult, AgentRole, LLMProvider

_logger = logging.getLogger(__name__)


class EditOutcome(StrEnum):
    """Qué pasó al intentar que el Editor decidiera la publicación de la noche.

    Ver el docstring del módulo. `NO_CANDIDATES` no tiene contrapartida en
    `AttemptOutcome` (`runner.py`) porque se resuelve antes de construir
    ningún `AgentRunner`: sin candidatos no hay nada que decidir, ni
    presupuesto que gastar.
    """

    EDITED = "edited"
    NO_CANDIDATES = "no_candidates"
    INVALID_OUTPUT = "invalid_output"
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"


#: Traduce el vocabulario general de `AgentRunner` (`AttemptOutcome`) al
#: vocabulario del Editor (`EditOutcome`): mismos cinco valores, mismo
#: significado, sin perder ningún caso. `NO_CANDIDATES` no tiene entrada
#: porque nunca llega a invocarse `AgentRunner.run` en ese caso.
_OUTCOME_BY_ATTEMPT: dict[AttemptOutcome, EditOutcome] = {
    AttemptOutcome.OK: EditOutcome.EDITED,
    AttemptOutcome.INVALID_OUTPUT: EditOutcome.INVALID_OUTPUT,
    AttemptOutcome.AGENT_ERROR: EditOutcome.AGENT_ERROR,
    AttemptOutcome.TIMEOUT: EditOutcome.TIMEOUT,
    AttemptOutcome.RATE_LIMITED: EditOutcome.RATE_LIMITED,
}


@dataclass(frozen=True, slots=True)
class EditNightResult:
    """Resultado de `EditNight.__call__` para una noche.

    `candidates` cuenta los candidatos que de verdad se ofrecieron al
    Editor -- ya filtrados por la guarda de estado (ver el docstring del
    módulo) --, la misma cifra que `estimate_editor_tokens` usa para
    calcular el coste estimado. `reasons` solo lleva entradas para los
    `Finding` aprobados: el Editor no da motivo de los que omite, y
    `published`/`discarded` ya distinguen unos de otros. `unknown_item_ids`
    son cadenas, no `UUID`: son `item_id` que el Editor devolvió y que no
    corresponden a ningún candidato real, así que no hay garantía de que
    sean UUID válidos que puedan reconstruirse (pueden venir de una
    alucinación con cualquier forma de texto que `EditorDecision.item_id`
    sí haya logrado parsear como UUID, pero que no exista entre los
    candidatos). `tokens_spent` suma el gasto de todos los intentos, igual
    que `ReadItemResult`/`PopularizeResult`; es `0` en `NO_CANDIDATES`,
    donde no se autoriza ninguna llamada.
    """

    outcome: EditOutcome
    candidates: int
    published: list[Finding]
    discarded: list[Finding]
    reasons: dict[UUID, str]
    unknown_item_ids: tuple[str, ...]
    attempts: int
    tokens_spent: int


def estimate_editor_tokens(*, candidates: int, base_tokens: int, tokens_per_candidate: int) -> int:
    """Estimación de coste de la única llamada al Editor de la noche.

    `base_tokens + candidates * tokens_per_candidate`. Pura -- sin acceso a
    `BudgetGuard` ni a ningún repositorio -- para que `PipelineConfig.
    _editor_reserve_covers_worst_case` (`infrastructure/config.py`) y este
    módulo compartan exactamente la misma fórmula sin importarse entre sí.
    """
    return base_tokens + candidates * tokens_per_candidate


class EditNight:
    """Decide la publicación de la noche con el Editor: una única llamada, sin contexto previo.

    Toda la configuración (modelo, turnos, prompt, intentos, las dos
    constantes de estimación de coste) llega por constructor desde
    `cli.py` -- el composition root --, nunca leída de
    `config/pipeline.toml` directamente por esta clase (`ddd-conventions`:
    "sin acceso a configuración global"). `clock` es el mismo puerto de
    `domain/clock.py` que usa `BudgetGuard`: `EditNight` lo necesita para
    `Finding.publish(confidence, at)`, que exige un `datetime` aware, y
    `datetime.now()` suelto en `application/` está prohibido
    (`budget-guard-review` § 2).
    """

    def __init__(
        self,
        *,
        work: AgentWorkFactory,
        provider: LLMProvider,
        clock: Clock,
        system_prompt: str,
        prompt_version: str,
        model: str,
        max_turns: int,
        max_attempts: int,
        base_tokens: int,
        tokens_per_candidate: int,
    ) -> None:
        self._work = work
        self._provider = provider
        self._clock = clock
        self._system_prompt = system_prompt
        self._prompt_version = prompt_version
        self._model = model
        self._max_turns = max_turns
        self._max_attempts = max_attempts
        self._base_tokens = base_tokens
        self._tokens_per_candidate = tokens_per_candidate

    async def __call__(self, *, run_id: UUID) -> EditNightResult:
        """Decide qué publicar de los candidatos pendientes del `run_id` dado.

        Unidad de trabajo 1 (sin LLM): lee los candidatos
        (`FindingRepository.unpublished_for_run`) y, para cada uno, su
        `Item`; filtra -- con `warning`, no fatal -- los que no estén
        `READ` (ver "La guarda de estado" en el docstring del módulo). Sin
        candidatos tras el filtro, devuelve `NO_CANDIDATES` sin autorizar
        nada ni construir ningún `AgentRunner`: es la única forma de
        garantizar `tokens_spent == 0` en ese caso. Con candidatos,
        construye el `AgentRunner` con la estimación de coste ya resuelta
        (ver "Por qué se construye dentro de `__call__`") y delega el resto
        -- autorizar, llamar al proveedor, reintentar la salida inválida,
        registrar cada intento -- en `AgentRunner.run` (docstring de
        `runner.py`). Solo si el resultado es `OK` se abre la segunda
        unidad de trabajo, que aplica las transiciones de `Item`/`Finding`
        y las persiste, todo o nada.
        """
        with self._work() as w:
            unpublished = w.findings.unpublished_for_run(run_id)
            candidates: list[tuple[Finding, Item]] = []
            for finding in unpublished:
                item = w.items.get(finding.item_id)
                if item is None:
                    raise InvariantViolation(
                        "ItemRepository.get(finding.item_id) no debería devolver None: "
                        "todo Finding candidato proviene de un Item ya persistido "
                        f"(item_id={finding.item_id})"
                    )
                if item.status is not ItemStatus.READ:
                    _logger.warning(
                        "candidato del Editor descartado: Item no está READ "
                        "(run_id=%s, item_id=%s, status=%s)",
                        run_id,
                        item.id,
                        item.status.value,
                    )
                    continue
                candidates.append((finding, item))

        if not candidates:
            return EditNightResult(
                outcome=EditOutcome.NO_CANDIDATES,
                candidates=0,
                published=[],
                discarded=[],
                reasons={},
                unknown_item_ids=(),
                attempts=0,
                tokens_spent=0,
            )

        estimated_tokens = estimate_editor_tokens(
            candidates=len(candidates),
            base_tokens=self._base_tokens,
            tokens_per_candidate=self._tokens_per_candidate,
        )
        runner = AgentRunner(
            work=self._work,
            provider=self._provider,
            role=AgentRole.EDITOR,
            model=self._model,
            system_prompt=self._system_prompt,
            prompt_version=self._prompt_version,
            estimated_tokens=estimated_tokens,
            max_turns=self._max_turns,
            max_attempts=self._max_attempts,
        )

        def _build_editor_output(result: AgentResult, _run_id: UUID) -> EditorOutput:
            return parse_editor_output(result.output_text)

        runner_result = await runner.run(
            prompt=self._build_prompt(candidates), item_id=None, build=_build_editor_output
        )
        outcome = _OUTCOME_BY_ATTEMPT[runner_result.outcome]

        if runner_result.outcome is not AttemptOutcome.OK:
            # INVALID_OUTPUT (agotados los intentos) / TIMEOUT / AGENT_ERROR /
            # RATE_LIMITED: ningún Finding ni Item cambia -- ver la tabla de
            # desenlaces del docstring del módulo.
            return EditNightResult(
                outcome=outcome,
                candidates=len(candidates),
                published=[],
                discarded=[],
                reasons={},
                unknown_item_ids=(),
                attempts=runner_result.attempts,
                tokens_spent=runner_result.tokens_spent,
            )

        editor_output = runner_result.value
        decisions, unknown_item_ids = self._resolve_decisions(
            editor_output, candidates=candidates, run_id=run_id
        )

        published: list[Finding] = []
        discarded: list[Finding] = []
        reasons: dict[UUID, str] = {}
        published_at = self._clock.now()
        with self._work() as w:
            for finding, item in candidates:
                decision = decisions.get(finding.item_id)
                if decision is not None:
                    finding.publish(decision.confidence, published_at)
                    w.findings.save(finding)
                    item.publish()
                    w.items.save(item)
                    published.append(finding)
                    reasons[finding.item_id] = decision.reason
                    _logger.info(
                        "decisión del Editor: run_id=%s item_id=%s finding_id=%s "
                        "publicado=%s confidence=%s reason=%s",
                        run_id,
                        finding.item_id,
                        finding.id,
                        True,
                        decision.confidence,
                        decision.reason,
                    )
                else:
                    item.discard()
                    w.items.save(item)
                    discarded.append(finding)
                    _logger.info(
                        "decisión del Editor: run_id=%s item_id=%s finding_id=%s "
                        "publicado=%s confidence=%s reason=%s",
                        run_id,
                        finding.item_id,
                        finding.id,
                        False,
                        None,
                        None,
                    )

        return EditNightResult(
            outcome=outcome,
            candidates=len(candidates),
            published=published,
            discarded=discarded,
            reasons=reasons,
            unknown_item_ids=unknown_item_ids,
            attempts=runner_result.attempts,
            tokens_spent=runner_result.tokens_spent,
        )

    @staticmethod
    def _resolve_decisions(
        editor_output: EditorOutput,
        *,
        candidates: list[tuple[Finding, Item]],
        run_id: UUID,
    ) -> tuple[dict[UUID, EditorDecision], tuple[str, ...]]:
        """Cruza `editor_output.publish` contra los candidatos reales.

        `item_id` que no está entre los candidatos: se ignora, se cuenta en
        `unknown_item_ids`, `warning` en log -- nunca dispara un reintento
        (ver "Validación de la respuesta del Editor" en el docstring del
        módulo). `item_id` duplicado: gana la primera aparición, el resto
        se ignora con `warning`.
        """
        candidate_ids = {finding.item_id for finding, _ in candidates}
        decisions: dict[UUID, EditorDecision] = {}
        unknown_item_ids: list[str] = []
        for decision in editor_output.publish:
            if decision.item_id not in candidate_ids:
                unknown_item_ids.append(str(decision.item_id))
                _logger.warning(
                    "el Editor aprobó un item_id que no está entre los candidatos, "
                    "ignorado (run_id=%s, item_id=%s)",
                    run_id,
                    decision.item_id,
                )
                continue
            if decision.item_id in decisions:
                _logger.warning(
                    "el Editor devolvió un item_id duplicado, se conserva la primera "
                    "aparición (run_id=%s, item_id=%s)",
                    run_id,
                    decision.item_id,
                )
                continue
            decisions[decision.item_id] = decision
        return decisions, tuple(unknown_item_ids)

    @staticmethod
    def _build_prompt(candidates: list[tuple[Finding, Item]]) -> str:
        """Contenido de usuario de la petición: los candidatos, no instrucciones.

        Solo `item_id`, `title` y `level_curious` de cada candidato --
        nunca los tres niveles completos, que son ~1.100 tokens/candidato:
        con `max_items_per_night = 40` eso serían decenas de miles de
        tokens de entrada frente a unos pocos miles con solo el titular y
        el nivel curioso (`prompts/editor.md` no necesita más para decidir
        qué merece salir). Todo el bloque viaja envuelto en las marcas
        `<candidates>`/`</candidates>` que `prompts/editor.md` instruye
        tratar como dato puro, nunca como instrucción -- mismo patrón que
        `ReadItem`/`PopularizeReading` aplican a `<abstract>`/`<reading>`.
        """
        lines = ["<candidates>"]
        for finding, _item in candidates:
            lines.append(f"- item_id: {finding.item_id}")
            lines.append(f"  title: {finding.title}")
            lines.append(f"  level_curious: {finding.level_curious}")
        lines.append("</candidates>")
        return "\n".join(lines)
