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

## T71.c: dos variantes de Reader, elegidas por categoría

`ReadItem` recibe dos prompts (`base`/`reader-v2`, `measures`/`reader-v3`,
ambos empaquetados como `ReaderPrompt`) y construye un `AgentRunner` por
cada uno -- mismo rol `READER` en los dos, mismo `model`/`max_turns`/
`max_attempts`, solo cambian `system_prompt`/`prompt_version`/
`estimated_tokens`. `__call__` decide qué variante usar **antes** de
llamar a `AgentRunner.run` (nunca a mitad de intento): si alguna de las
categorías de `item.categories` está en `measures_categories`
(`[reader] measurement_categories` de `pipeline.toml`, ya como
`frozenset` cuando llega aquí), usa `reader-v3` y filtra las medidas
crudas con `reader_measurements.filter_measurements`; si no, usa
`reader-v2` sin tocar medidas (`Reading.measurements = None`, igual que
antes de T71.c). Una medida descartada por el filtro (forma inválida,
invariante de dominio, `evidence` no literal, anfitriona no reconocible)
nunca provoca otro intento: `filter_measurements` no lanza por una medida
individual, así que el ítem se lee en un solo intento igual que si no
tuviera medidas que descartar.
"""

import logging
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from nocturna.application.agents.reader_measurements import (
    DiscardedMeasurement,
    filter_measurements,
)
from nocturna.application.agents.reader_output import parse_reader_output, parse_reader_v3_output
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.domain.entities import Item, ItemStatus, Reading
from nocturna.domain.errors import InvalidTransition
from nocturna.domain.llm import AgentResult, AgentRole, LLMProvider

_logger = logging.getLogger(__name__)

#: Cuántos caracteres de `evidence` viajan al log de un descarte
#: (`reader.measurement_discarded`). Mismo criterio que
#: `cli.py::_ABSTRACT_PREVIEW_CHARS`: una vista previa basta para revisar
#: el descarte de un vistazo, el dato completo ya vive en `DiscardedMeasurement`
#: (que este módulo no persiste) y, si se descartó, en el abstract del propio
#: `Item`.
_EVIDENCE_LOG_PREVIEW_CHARS = 200


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
class ReaderPrompt:
    """Un prompt de sistema del Reader, con su versión y su estimación de
    coste propia (T71.c). `ReadItem` recibe dos -- `base` (`reader-v2`) y
    `measures` (`reader-v3`) -- porque cada variante tiene su propio
    `system_prompt` (fichero `.md` distinto), su propia `prompt_version`
    (para que el informe de calibración distinga una de otra) y su propio
    `estimated_tokens` (el prompt de medidas es más largo y pide más
    salida, así que subestimarlo con la cifra del Reader base infravaloraría
    el gasto real que `BudgetGuard.authorize` compara contra el presupuesto
    restante)."""

    system_prompt: str
    prompt_version: str
    estimated_tokens: int


@dataclass(frozen=True, slots=True)
class ReadItemResult:
    """Resultado de `ReadItem.__call__` para un `Item`.

    `tokens_spent` suma el gasto de **todos** los intentos (incluidos los
    fallidos por JSON inválido), no solo el del intento que tuvo éxito: es
    la cifra que le interesa a quien orquesta la noche, coherente con
    `AgentCallRepository.tokens_used_for_run` (regla 2 de `budget.py`, que
    también suma todos los intentos). `reading.tokens_in`/`tokens_out`, en
    cambio, documentan solo el intento que produjo esa `Reading`.

    `prompt_version` (T71.c) es la versión del prompt elegida para este
    `Item` -- `"reader-v2"` o `"reader-v3"` -- decidida una sola vez, antes
    del primer intento, así que es la misma para todos los intentos de esta
    llamada con independencia de `outcome`.
    """

    outcome: ReadOutcome
    reading: Reading | None
    attempts: int
    tokens_spent: int
    prompt_version: str


class ReadItem:
    """Lee un `Item` con el Reader: una conversación nueva, sin contexto previo.

    Toda la configuración (modelo, turnos, los dos prompts, intentos,
    categorías con medidas) llega por constructor desde `cli.py` -- el
    composition root --, nunca leída de `config/pipeline.toml`
    directamente por esta clase (`ddd-conventions`: "sin acceso a
    configuración global"). Se pasa tal cual a dos `AgentRunner` (uno por
    variante, ver el docstring del módulo), que son quienes de verdad
    hablan con `LLMProvider` detrás de `BudgetGuard`.
    """

    def __init__(
        self,
        *,
        work: AgentWorkFactory,
        provider: LLMProvider,
        model: str,
        max_turns: int,
        max_attempts: int,
        base: ReaderPrompt,
        measures: ReaderPrompt,
        measures_categories: frozenset[str],
    ) -> None:
        self._work = work
        self._measures_categories = measures_categories
        self._base_prompt_version = base.prompt_version
        self._measures_prompt_version = measures.prompt_version
        self._runner_base = AgentRunner(
            work=work,
            provider=provider,
            role=AgentRole.READER,
            model=model,
            system_prompt=base.system_prompt,
            prompt_version=base.prompt_version,
            estimated_tokens=base.estimated_tokens,
            max_turns=max_turns,
            max_attempts=max_attempts,
        )
        self._runner_measures = AgentRunner(
            work=work,
            provider=provider,
            role=AgentRole.READER,
            model=model,
            system_prompt=measures.system_prompt,
            prompt_version=measures.prompt_version,
            estimated_tokens=measures.estimated_tokens,
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

        La variante (`reader-v2`/`reader-v3`) se decide aquí, una sola vez,
        antes de tocar `AgentRunner.run` (T71.c, ver el docstring del
        módulo): `item.exoplanet_match` (T79, marca de la ingesta) y
        `not self._measures_categories.isdisjoint(item.categories)`.

        El resto -- autorizar, llamar al proveedor, reintentar la salida
        inválida, registrar cada intento -- lo hace `AgentRunner.run`
        (docstring de `runner.py`). `_build_reading_v2`/`_build_reading_v3`
        son el `build` que le pasa: parsean el JSON del Reader y construyen
        el `Reading`, dentro del `try` que `AgentRunner` ya envuelve
        alrededor de `build` para que una llamada ya cobrada nunca se
        pierda entre `run_agent` y `record_call`. Solo cuando `run()`
        devuelve se persiste el `Reading`/`Item`, en la tercera unidad de
        trabajo del camino feliz (autorizar, `record_call`, persistir),
        nunca compartida con la contabilidad.
        """
        if item.status is not ItemStatus.NEW:
            raise InvalidTransition(item.status.value, ItemStatus.READ.value, entity="Item")

        use_measures = item.exoplanet_match and not self._measures_categories.isdisjoint(
            item.categories
        )
        discards_by_attempt: list[DiscardedMeasurement] = []

        if use_measures:
            runner = self._runner_measures
            prompt_version = self._measures_prompt_version

            def _build_reading_v3(result: AgentResult, run_id: UUID) -> Reading:
                parsed = parse_reader_v3_output(result.output_text)
                filtered = filter_measurements(
                    parsed.measurements, abstract=item.abstract, title=item.title
                )
                discards_by_attempt.clear()
                discards_by_attempt.extend(filtered.discarded)
                return Reading(
                    item_id=item.id,
                    summary=parsed.summary,
                    objects=tuple(parsed.objects),
                    claims=tuple(parsed.claims),
                    interest_score=parsed.interest_score,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    model=result.model,
                    measurements=filtered.kept,
                )

            build = _build_reading_v3
        else:
            runner = self._runner_base
            prompt_version = self._base_prompt_version

            def _build_reading_v2(result: AgentResult, run_id: UUID) -> Reading:
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

            build = _build_reading_v2

        runner_result = await runner.run(
            prompt=self._build_prompt(item), item_id=item.id, build=build
        )
        outcome = _OUTCOME_BY_ATTEMPT[runner_result.outcome]

        if runner_result.outcome is AttemptOutcome.OK:
            # --- éxito: `build` produjo un `Reading` válido ---------------
            reading = runner_result.value
            item.mark_read()
            with self._work() as w:
                w.readings.add(reading)
                w.items.save(item)
            if use_measures:
                kept = len(reading.measurements) if reading.measurements is not None else 0
                self._log_measurements(item, prompt_version, kept, discards_by_attempt)
            return ReadItemResult(
                outcome=outcome,
                reading=reading,
                attempts=runner_result.attempts,
                tokens_spent=runner_result.tokens_spent,
                prompt_version=prompt_version,
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
                prompt_version=prompt_version,
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
            prompt_version=prompt_version,
        )

    @staticmethod
    def _log_measurements(
        item: Item,
        prompt_version: str,
        kept: int,
        discards: list[DiscardedMeasurement],
    ) -> None:
        """Logs de `reader-v3` para el intento aceptado (T71.c).

        `kept`/`discards` son los de la construcción del `Reading` que de
        verdad se persistió -- `_build_reading_v3` sobrescribe
        `discards_by_attempt` en cada intento, así que para cuando
        `AgentRunner.run` ha devuelto `OK` refleja solo el último intento,
        el aceptado, nunca uno anterior descartado por JSON inválido. Un
        `warning` por medida descartada (`reader.measurement_discarded`) y
        un `info` de resumen (`reader.measurements`) con los recuentos.
        """
        for discard in discards:
            evidence = discard.evidence
            if evidence is not None and len(evidence) > _EVIDENCE_LOG_PREVIEW_CHARS:
                evidence = evidence[:_EVIDENCE_LOG_PREVIEW_CHARS] + "…"
            _logger.warning(
                "reader.measurement_discarded",
                extra={
                    "event": "reader.measurement_discarded",
                    "item_id": str(item.id),
                    "external_id": item.external_id,
                    "reason": discard.reason.value,
                    "detail": discard.detail,
                    "planet_name": discard.planet_name,
                    "parameter": discard.parameter,
                    "value": discard.value,
                    "evidence": evidence,
                },
            )
        _logger.info(
            "reader.measurements",
            extra={
                "event": "reader.measurements",
                "item_id": str(item.id),
                "external_id": item.external_id,
                "prompt_version": prompt_version,
                "kept": kept,
                "discarded": len(discards),
            },
        )

    @staticmethod
    def _build_prompt(item: Item) -> str:
        """Contenido de usuario de la petición: los datos del ítem, no instrucciones.

        `AgentRequest.system_prompt` lleva las instrucciones fijas del
        Reader (`prompts/reader.md` o `prompts/reader-v3.md`, según la
        variante elegida en `__call__`); este método solo empaqueta el
        título y el abstract, que es contenido de un tercero no confiable
        (`domain/llm.py`, docstring de `AgentRequest`). Ninguna instrucción
        propia se mezcla aquí con el dato. El abstract viaja envuelto en las
        marcas `<abstract>`/`</abstract>` -- las mismas que ambos prompts
        instruyen tratar como dato puro, nunca como instrucción -- para que
        un abstract que incluya su propio encabezado falso ("Abstract:", u
        otro) no pueda simular una estructura que no le corresponde.
        """
        return f"Título: {item.title}\n\n<abstract>\n{item.abstract}\n</abstract>"
