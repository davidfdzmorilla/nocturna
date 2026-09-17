"""Caso de uso: el Popularizer divulga una `Reading` con `interest_score` alto.

Tercer caso de uso sobre `AgentRunner` (`application/agents/runner.py`),
mismo patrón que `ReadItem` (`use_cases/read_item.py`, T41): toda la
maquinaria de gasto -- el bucle de intentos, la unidad de `authorize` +
`timeout_for_call`, los cuatro `except` del proveedor y el registro de
`AgentCall` -- vive en `AgentRunner`, genérica por `role`. Este módulo solo
aporta lo específico de "divulgar una `Reading`": el umbral de
`interest_score`, el prompt, `build` (parsea el JSON del Popularizer y
construye el `Finding`, sin publicar) y las transiciones de `Item`. Ver el
docstring de `runner.py` para el razonamiento completo del patrón de gasto.

## La guarda de estado, antes de cualquier otra cosa

Igual que `ReadItem` exige `item.status is ItemStatus.NEW` antes de
`authorize` nada, `PopularizeReading` exige `item.status is
ItemStatus.READ` -- el estado que le corresponde a *este* agente, no el del
Reader. Hoy nada la alcanza en mal estado porque `run-item` siempre
encadena Reader -> Popularizer y el Reader deja el ítem `READ` (o no llega
aquí). Pero T44 seleccionará candidatos directamente de base de datos y se
los entregará a este caso de uso sin pasar por `ReadItem`: sin esta guarda,
un ítem en cualquier otro estado haría que `item.discard()` -- la
transición que de verdad se invoca en `SKIPPED_LOW_SCORE` e
`INVALID_OUTPUT`, ver la tabla más abajo -- reventara con `InvalidTransition`
sin traducir (`_ITEM_TRANSITIONS` solo permite `READ -> DISCARDED`), y en
`INVALID_OUTPUT` eso ocurriría **después** de haber gastado la llamada. Se
modela con la misma excepción que lanzaría `item.discard()` si se le diera
la oportunidad -- mismo patrón que la guarda de `ReadItem`, adaptado al
único método de transición que este caso de uso realmente invoca -- en vez
de un `PopularizeOutcome` nuevo: ninguno de los seis valores fijados
describe "no se intentó nada", y esto solo debería ocurrir por un error de
quien orquesta.

## El umbral, antes de gastar nada

`interest_score < min_interest_score` se comprueba **dentro** de este caso
de uso, antes de cualquier `authorize`: un único sitio conoce el umbral, y
ningún camino de código puede llamar al Popularizer con una puntuación baja
-- ni siquiera por accidente, desde otro caso de uso que se cree con el
mismo `AgentRunner`. El ítem se descarta (`Item.discard()`, `READ ->
DISCARDED`, terminal): su propio docstring da "interest_score bajo" como
ejemplo. No se abre ninguna unidad de trabajo de gasto en este camino, solo
la que persiste el `Item`.

## El `Finding` que produce `build` nunca se publica

`build` construye un `Finding` con `confidence=None` y `published_at=None`
-- los valores por defecto de la entidad. "No publicado" es exactamente
eso, y `Finding.__post_init__` exige que ambos estén vacíos o ambos
informados a la vez, así que no hay conflicto con el Editor (T43), que los
asigna juntos vía `Finding.publish()`. `type` es siempre
`FindingType.PAPER_EXPLAINED` -- la única constante de fase 1 según
`CLAUDE.md`, no algo que el agente elija -- e `item_id`/`run_id` vienen del
contexto de la llamada, no del JSON del Popularizer (`PopularizerOutput`
solo valida los cuatro campos de texto que sí aporta el agente, ver su
docstring).

## Las transiciones de `Item`

| Desenlace | `Item` | `Finding` |
|---|---|---|
| `POPULARIZED` | sigue `READ` | sí, sin publicar |
| `SKIPPED_LOW_SCORE` | `DISCARDED` | no |
| `INVALID_OUTPUT` (agotados los intentos) | `DISCARDED` | no |
| `TIMEOUT` / `AGENT_ERROR` / `RATE_LIMITED` | sigue `READ` | no |

Un ítem con `Finding` se queda `READ` a propósito: publicar es del Editor,
y `READ -> PUBLISHED` es transición suya (T43), no de este caso de uso.

`INVALID_OUTPUT` descarta el ítem por el mismo motivo que `ReadItem.
mark_failed()` lo hace para el Reader (`ItemStatus.FAILED`, docstring de
`ItemStatus`): sin una transición terminal, un ítem `READ` cuyo Popularizer
nunca produce JSON válido quedaría `READ` para siempre, y T44 -- que
selecciona candidatos como "lecturas con `interest_score >= umbral` sin
`Finding`" -- lo reintentaría cada noche, gastando `max_calls_per_item`
llamadas contra él sin parar. Los fallos transitorios (`TIMEOUT`/
`AGENT_ERROR`/`RATE_LIMITED`), en cambio, dejan el ítem `READ` y
reintentable otra noche, igual que `ReadItem` deja `NEW` un `Item` que no
pudo leerse por un fallo del proveedor.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from nocturna.application.agents.popularizer_output import parse_popularizer_output
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.domain.entities import Finding, FindingType, Item, ItemStatus, Reading
from nocturna.domain.errors import InvalidTransition
from nocturna.domain.llm import AgentResult, AgentRole, LLMProvider


class PopularizeOutcome(StrEnum):
    """Qué pasó al intentar divulgar una `Reading`. Ver el docstring del módulo."""

    POPULARIZED = "popularized"
    SKIPPED_LOW_SCORE = "skipped_low_score"
    INVALID_OUTPUT = "invalid_output"
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"


#: Traduce el vocabulario general de `AgentRunner` (`AttemptOutcome`) al
#: vocabulario del Popularizer (`PopularizeOutcome`): mismos cinco valores,
#: mismo significado, sin perder ningún caso. `SKIPPED_LOW_SCORE` no tiene
#: contrapartida en `AttemptOutcome` porque se resuelve antes de invocar al
#: runner (ver el docstring del módulo).
_OUTCOME_BY_ATTEMPT: dict[AttemptOutcome, PopularizeOutcome] = {
    AttemptOutcome.OK: PopularizeOutcome.POPULARIZED,
    AttemptOutcome.INVALID_OUTPUT: PopularizeOutcome.INVALID_OUTPUT,
    AttemptOutcome.AGENT_ERROR: PopularizeOutcome.AGENT_ERROR,
    AttemptOutcome.TIMEOUT: PopularizeOutcome.TIMEOUT,
    AttemptOutcome.RATE_LIMITED: PopularizeOutcome.RATE_LIMITED,
}


@dataclass(frozen=True, slots=True)
class PopularizeResult:
    """Resultado de `PopularizeReading.__call__` para una `Reading`.

    `tokens_spent` suma el gasto de todos los intentos de esta llamada,
    igual que `ReadItemResult.tokens_spent` (coherente con
    `AgentCallRepository.tokens_used_for_run`, que también suma todos los
    intentos); es `0` en `SKIPPED_LOW_SCORE`, donde no se autoriza ninguna
    llamada.
    """

    outcome: PopularizeOutcome
    finding: Finding | None
    attempts: int
    tokens_spent: int


class PopularizeReading:
    """Divulga una `Reading` con el Popularizer: una conversación nueva, sin contexto previo.

    Toda la configuración (modelo, turnos, prompt, intentos, estimación de
    coste, umbral) llega por constructor desde `cli.py` -- el composition
    root --, nunca leída de `config/pipeline.toml` directamente por esta
    clase (`ddd-conventions`: "sin acceso a configuración global"). Se pasa
    tal cual a `AgentRunner`, que es quien de verdad habla con `LLMProvider`
    detrás de `BudgetGuard`.
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
        estimated_tokens: int,
        max_attempts: int,
        min_interest_score: int,
    ) -> None:
        self._work = work
        self._min_interest_score = min_interest_score
        self._runner = AgentRunner(
            work=work,
            provider=provider,
            role=AgentRole.POPULARIZER,
            model=model,
            system_prompt=system_prompt,
            prompt_version=prompt_version,
            estimated_tokens=estimated_tokens,
            max_turns=max_turns,
            max_attempts=max_attempts,
        )

    async def __call__(self, *, item: Item, reading: Reading) -> PopularizeResult:
        """Divulga `reading` (del ítem `item`), hasta `max_attempts` intentos si el JSON no valida.

        Guarda de estado primero (antes de la de umbral, antes de autorizar
        nada): si `item` no está `READ`, no hay nada que hacer -- no lo leyó
        el Reader, o ya se descartó, publicó o falló antes (ver "La guarda
        de estado" en el docstring del módulo). Luego, guarda de umbral
        (antes de cualquier gasto): si `reading.interest_score` no llega al
        mínimo configurado, el ítem se descarta y no se llama al
        Popularizer. El resto -- autorizar, llamar al proveedor, reintentar
        la salida inválida, registrar cada intento -- lo hace
        `AgentRunner.run` (docstring de `runner.py`). `_build_finding` es el
        `build` que le pasa: parsea el JSON del Popularizer y construye el
        `Finding`, sin publicar, dentro del `try` que `AgentRunner` ya
        envuelve alrededor de `build` para que una llamada ya cobrada nunca
        se pierda entre `run_agent` y `record_call`. Solo cuando `run()`
        devuelve se persiste el `Finding`/`Item`, en una unidad de trabajo
        propia, nunca compartida con la contabilidad.
        """
        if item.status is not ItemStatus.READ:
            raise InvalidTransition(item.status.value, ItemStatus.DISCARDED.value, entity="Item")

        if reading.interest_score < self._min_interest_score:
            item.discard()
            with self._work() as w:
                w.items.save(item)
            return PopularizeResult(
                outcome=PopularizeOutcome.SKIPPED_LOW_SCORE,
                finding=None,
                attempts=0,
                tokens_spent=0,
            )

        def _build_finding(result: AgentResult, run_id: UUID) -> Finding:
            parsed = parse_popularizer_output(result.output_text)
            return Finding(
                item_id=item.id,
                run_id=run_id,
                type=FindingType.PAPER_EXPLAINED,
                title=parsed.title,
                level_curious=parsed.level_curious,
                level_amateur=parsed.level_amateur,
                level_technical=parsed.level_technical,
            )

        runner_result = await self._runner.run(
            prompt=self._build_prompt(item, reading), item_id=item.id, build=_build_finding
        )
        outcome = _OUTCOME_BY_ATTEMPT[runner_result.outcome]

        if runner_result.outcome is AttemptOutcome.OK:
            # --- éxito: `build` produjo un `Finding` válido, sin publicar --
            # `runner_result.value` viene garantizado no-`None` por el
            # contrato de `RunnerResult` (docstring de `runner.py`: "solo
            # presente si outcome es OK"); `ReadItem` -- el mismo patrón
            # para el Reader -- tampoco lo revalida aquí, así que no se
            # añade una comprobación redundante sobre un contrato que ya
            # garantiza otro módulo.
            finding = runner_result.value
            with self._work() as w:
                w.findings.add(finding)
            return PopularizeResult(
                outcome=outcome,
                finding=finding,
                attempts=runner_result.attempts,
                tokens_spent=runner_result.tokens_spent,
            )

        if runner_result.outcome is AttemptOutcome.INVALID_OUTPUT:
            # Se agotaron los intentos sin producir un JSON válido: fallo
            # terminal, el ítem no vuelve a ofrecerse al Popularizer (ver el
            # docstring del módulo).
            item.discard()
            with self._work() as w:
                w.items.save(item)
            return PopularizeResult(
                outcome=outcome,
                finding=None,
                attempts=runner_result.attempts,
                tokens_spent=runner_result.tokens_spent,
            )

        # RATE_LIMITED / TIMEOUT / AGENT_ERROR: ninguno reintenta ni cambia
        # el estado del `Item` -- se queda `READ`, disponible para que una
        # noche futura vuelva a intentar divulgarlo.
        return PopularizeResult(
            outcome=outcome,
            finding=None,
            attempts=runner_result.attempts,
            tokens_spent=runner_result.tokens_spent,
        )

    @staticmethod
    def _build_prompt(item: Item, reading: Reading) -> str:
        """Contenido de usuario de la petición: la `Reading`, no instrucciones.

        `AgentRequest.system_prompt` lleva las instrucciones fijas del
        Popularizer (`prompts/popularizer.md`); este método solo empaqueta
        el título original del ítem (texto de arXiv, un tercero no
        confiable) y los datos de la `Reading` -- resumen, objetos,
        afirmaciones --, todo envuelto en las marcas `<reading>`/`</reading>`
        que `prompts/popularizer.md` instruye tratar como dato puro, nunca
        como instrucción. Ninguna instrucción propia se mezcla aquí con el
        dato.
        """
        objects = (
            "\n".join(f"- {obj}" for obj in reading.objects) if reading.objects else "(ninguno)"
        )
        claims = (
            "\n".join(f"- {claim}" for claim in reading.claims) if reading.claims else "(ninguna)"
        )
        return (
            "<reading>\n"
            f"Título: {item.title}\n\n"
            f"Resumen: {reading.summary}\n\n"
            f"Objetos:\n{objects}\n\n"
            f"Afirmaciones:\n{claims}\n"
            "</reading>"
        )
