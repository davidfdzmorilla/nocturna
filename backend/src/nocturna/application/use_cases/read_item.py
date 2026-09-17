"""Caso de uso: el Reader lee un `Item` y produce un `Reading`.

Segundo paso del refactor de T42 (`application/agents/runner.py`, primer
paso): toda la maquinaria de gasto -- el bucle de intentos, la unidad de
`authorize` + `timeout_for_call`, la guarda `timeout_s <= 0`, los cuatro
`except` del proveedor y el registro de `AgentCall` en sus tres variantes --
vive ahora en `AgentRunner`, genérica por `role`. Este módulo solo aporta lo
que es específico de "leer un `Item`": la guarda de `ItemStatus`, el prompt,
`build` (parsea el JSON del Reader y construye el `Reading`) y las
transiciones de `Item` -- `mark_read()` en éxito, `mark_failed()` cuando se
agotan los intentos por salida no parseable, y ninguna transición (el ítem
se queda `NEW`) ante un límite de tasa, un timeout o un error del proveedor.
Ver el docstring de `runner.py` para el razonamiento completo del patrón de
gasto, incluida la decisión de que `build` corra dentro de `AgentRunner.run`.

`record_call` (dentro de `AgentRunner`) va siempre en su **propia** unidad
de trabajo, separada de la escritura de `Reading`/`Item` que hace este
módulo después de que `run()` devuelva: así lo pide el docstring de
`BudgetGuard.record_call`, y así lo confirmó la revisión de T41 -- `readings`
tiene `uq_readings_item_id`, y un `IntegrityError` (dos `run-item`
concurrentes sobre el mismo ítem, por ejemplo) en una unidad de trabajo
compartida haría rollback también de la fila de `AgentCall`, una llamada ya
cobrada por la suscripción.

`ReadOutcome` existe por una deuda con T44 (el orquestador de la noche):
sin él, T44 tendría que releer `AgentCall`/`Item` para deducir por qué un
ítem no produjo `Reading` (¿se agotó el JSON inválido?, ¿fue un límite de
tasa que debe cortar la noche entera?, ¿fue un timeout aislado?). Con
`ReadItemResult.outcome` tipado, T44 puede contar "outcomes distintos de
`READ` seguidos" y decidir si el `Run` se cierra como `partial` sin tener
que deducirlo del log de `AgentCall`. Traduce `AttemptOutcome`
(`runner.py`, vocabulario general de los tres agentes) a `ReadOutcome`
(vocabulario del Reader) con un diccionario de cinco entradas: mismos
valores, mismo significado, un nombre por rol.
"""

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from nocturna.application.agents.reader_output import parse_reader_output
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.domain.entities import Item, ItemStatus, Reading
from nocturna.domain.errors import InvalidTransition
from nocturna.domain.llm import AgentResult, AgentRole, LLMProvider


class ReadOutcome(StrEnum):
    """Qué pasó al intentar leer un `Item`. Ver el docstring del módulo."""

    READ = "read"
    INVALID_OUTPUT = "invalid_output"
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"


#: Traduce el vocabulario general de `AgentRunner` (`AttemptOutcome`) al
#: vocabulario del Reader (`ReadOutcome`): mismos cinco valores, mismo
#: significado, sin perder ningún caso.
_OUTCOME_BY_ATTEMPT: dict[AttemptOutcome, ReadOutcome] = {
    AttemptOutcome.OK: ReadOutcome.READ,
    AttemptOutcome.INVALID_OUTPUT: ReadOutcome.INVALID_OUTPUT,
    AttemptOutcome.AGENT_ERROR: ReadOutcome.AGENT_ERROR,
    AttemptOutcome.TIMEOUT: ReadOutcome.TIMEOUT,
    AttemptOutcome.RATE_LIMITED: ReadOutcome.RATE_LIMITED,
}


@dataclass(frozen=True, slots=True)
class ReadItemResult:
    """Resultado de `ReadItem.__call__` para un `Item`.

    `tokens_spent` suma el gasto de **todos** los intentos (incluidos los
    fallidos por JSON inválido), no solo el del intento que tuvo éxito: es
    la cifra que le interesa a quien orquesta la noche, coherente con
    `AgentCallRepository.tokens_used_for_run` (regla 2 de `budget.py`, que
    también suma todos los intentos). `reading.tokens_in`/`tokens_out`, en
    cambio, documentan solo el intento que produjo esa `Reading`.
    """

    outcome: ReadOutcome
    reading: Reading | None
    attempts: int
    tokens_spent: int


class ReadItem:
    """Lee un `Item` con el Reader: una conversación nueva, sin contexto previo.

    Toda la configuración (modelo, turnos, prompt, intentos, estimación de
    coste) llega por constructor desde `cli.py` -- el composition root --,
    nunca leída de `config/pipeline.toml` directamente por esta clase
    (`ddd-conventions`: "sin acceso a configuración global"). Se pasa tal
    cual a `AgentRunner`, que es quien de verdad habla con `LLMProvider`
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
    ) -> None:
        self._work = work
        self._runner = AgentRunner(
            work=work,
            provider=provider,
            role=AgentRole.READER,
            model=model,
            system_prompt=system_prompt,
            prompt_version=prompt_version,
            estimated_tokens=estimated_tokens,
            max_turns=max_turns,
            max_attempts=max_attempts,
        )

    async def __call__(self, item: Item) -> ReadItemResult:
        """Lee `item`, hasta `max_attempts` intentos si el JSON no valida.

        Guarda de estado primero (antes de autorizar nada): si `item` no
        está `NEW`, no hay nada que hacer -- ya se leyó, se descartó, se
        publicó o falló antes. Esto también hace innecesario resolver la
        unicidad de `Reading` por `Item` (`OPEN_DECISIONS.md`): el índice
        único nunca se alcanza porque esta guarda dispara primero. Se
        modela como `InvalidTransition` (la misma excepción que lanzaría
        `item.mark_read()` si se le diera la oportunidad) en vez de un
        `ReadOutcome` nuevo: ninguno de los cinco valores fijados describe
        "no se intentó nada", y añadir un sexto valor para un caso que solo
        debería ocurrir por un error de quien orquesta (T44 selecciona con
        `ItemRepository.next_unread`, que ya filtra por `NEW`) sale del
        alcance de esta tarea.

        El resto -- autorizar, llamar al proveedor, reintentar la salida
        inválida, registrar cada intento -- lo hace `AgentRunner.run`
        (docstring de `runner.py`). `_build_reading` es el `build` que le
        pasa: parsea el JSON del Reader y construye el `Reading`, dentro del
        `try` que `AgentRunner` ya envuelve alrededor de `build` para que
        una llamada ya cobrada nunca se pierda entre `run_agent` y
        `record_call`. Solo cuando `run()` devuelve se persiste el
        `Reading`/`Item`, en la tercera unidad de trabajo del camino feliz
        (autorizar, `record_call`, persistir), nunca compartida con la
        contabilidad.
        """
        if item.status is not ItemStatus.NEW:
            raise InvalidTransition(item.status.value, ItemStatus.READ.value, entity="Item")

        def _build_reading(result: AgentResult, run_id: UUID) -> Reading:
            parsed = parse_reader_output(result.output_text)
            return Reading(
                item_id=item.id,
                summary=parsed.summary,
                objects=tuple(parsed.objects),
                claims=tuple(parsed.claims),
                interest_score=parsed.interest_score,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                model=result.model,
            )

        runner_result = await self._runner.run(
            prompt=self._build_prompt(item), item_id=item.id, build=_build_reading
        )
        outcome = _OUTCOME_BY_ATTEMPT[runner_result.outcome]

        if runner_result.outcome is AttemptOutcome.OK:
            # --- éxito: `build` produjo un `Reading` válido ---------------
            reading = runner_result.value
            item.mark_read()
            with self._work() as w:
                w.readings.add(reading)
                w.items.save(item)
            return ReadItemResult(
                outcome=outcome,
                reading=reading,
                attempts=runner_result.attempts,
                tokens_spent=runner_result.tokens_spent,
            )

        if runner_result.outcome is AttemptOutcome.INVALID_OUTPUT:
            # Se agotaron los intentos sin producir un JSON válido: fallo
            # terminal, el ítem no vuelve a ofrecerse (CLAUDE.md, "Agentes").
            item.mark_failed()
            with self._work() as w:
                w.items.save(item)
            return ReadItemResult(
                outcome=outcome,
                reading=None,
                attempts=runner_result.attempts,
                tokens_spent=runner_result.tokens_spent,
            )

        # RATE_LIMITED / TIMEOUT / AGENT_ERROR: ninguno reintenta ni marca el
        # `Item` como `FAILED` -- se queda `NEW`, disponible para que una
        # noche futura vuelva a intentarlo (`mark_failed()` es solo para el
        # agotamiento de reintentos por JSON inválido, no para un fallo
        # transitorio del proveedor).
        return ReadItemResult(
            outcome=outcome,
            reading=None,
            attempts=runner_result.attempts,
            tokens_spent=runner_result.tokens_spent,
        )

    @staticmethod
    def _build_prompt(item: Item) -> str:
        """Contenido de usuario de la petición: los datos del ítem, no instrucciones.

        `AgentRequest.system_prompt` lleva las instrucciones fijas del
        Reader (`prompts/reader.md`); este método solo empaqueta el título y
        el abstract, que es contenido de un tercero no confiable
        (`domain/llm.py`, docstring de `AgentRequest`). Ninguna instrucción
        propia se mezcla aquí con el dato. El abstract viaja envuelto en las
        marcas `<abstract>`/`</abstract>` -- las mismas que `prompts/reader.md`
        instruye tratar como dato puro, nunca como instrucción -- para que un
        abstract que incluya su propio encabezado falso ("Abstract:", u
        otro) no pueda simular una estructura que no le corresponde.
        """
        return f"Título: {item.title}\n\n<abstract>\n{item.abstract}\n</abstract>"
