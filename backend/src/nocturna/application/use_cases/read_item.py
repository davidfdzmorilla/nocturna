"""Caso de uso: el Reader lee un `Item` y produce un `Reading`.

Hereda literalmente el patrón de gasto de `tests/manual/test_sdk_smoke.py`
(la referencia que T40 dejó para T41-T44) y de ADR 0006 § 2: `authorize`
dentro de una unidad de trabajo, la llamada al LLM **fuera de toda
transacción**, `record_call` en una unidad de trabajo nueva y separada.
Ninguna conexión de PostgreSQL permanece abierta mientras se espera al
modelo (hasta `item_timeout_s` segundos).

`record_call` va siempre en su **propia** unidad de trabajo, separada de
cualquier otra escritura (`Reading.add`/`Item.save`): así lo pide el
docstring de `BudgetGuard.record_call`, y así lo confirmó la revisión de
T41 -- `readings` tiene `uq_readings_item_id`, y un `IntegrityError` (dos
`run-item` concurrentes sobre el mismo ítem, por ejemplo) en una unidad de
trabajo compartida haría rollback también de la fila de `AgentCall`, una
llamada ya cobrada por la suscripción.

`ReadOutcome` existe por una deuda con T44 (el orquestador de la noche):
sin él, T44 tendría que releer `AgentCall`/`Item` para deducir por qué un
ítem no produjo `Reading` (¿se agotó el JSON inválido?, ¿fue un límite de
tasa que debe cortar la noche entera?, ¿fue un timeout aislado?). Con
`ReadItemResult.outcome` tipado, T44 puede contar "outcomes distintos de
`READ` seguidos" y decidir si el `Run` se cierra como `partial` sin tener
que deducirlo del log de `AgentCall`.
"""

import asyncio
import logging
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from uuid import UUID

from nocturna.application.agents.parsing import InvalidAgentOutput
from nocturna.application.agents.reader_output import parse_reader_output
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.domain.entities import AgentCall, AgentCallStatus, Item, ItemStatus, Reading
from nocturna.domain.errors import (
    InvalidTransition,
    InvariantViolation,
    LLMError,
    LLMRateLimited,
    LLMTimeout,
)
from nocturna.domain.llm import AgentRequest, AgentResult, AgentRole, LLMProvider

_logger = logging.getLogger(__name__)


class ReadOutcome(StrEnum):
    """Qué pasó al intentar leer un `Item`. Ver el docstring del módulo."""

    READ = "read"
    INVALID_OUTPUT = "invalid_output"
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"


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
    (`ddd-conventions`: "sin acceso a configuración global").
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
        self._provider = provider
        self._system_prompt = system_prompt
        self._prompt_version = prompt_version
        self._model = model
        self._max_turns = max_turns
        self._estimated_tokens = estimated_tokens
        self._max_attempts = max_attempts

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
        """
        if item.status is not ItemStatus.NEW:
            raise InvalidTransition(item.status.value, ItemStatus.READ.value, entity="Item")

        tokens_spent = 0

        for attempt in range(1, self._max_attempts + 1):
            # --- unidad de trabajo 1: autorizar, nunca construir el
            # AgentRequest si se deniega --------------------------------
            with self._work() as w:
                w.guard.authorize(AgentRole.READER, self._estimated_tokens)
                timeout_s = w.guard.timeout_for_call()
                run = w.runs.current()
                # `authorize` ya ha confirmado, en esta misma unidad de
                # trabajo, que hay un Run en RUNNING vigilado por este
                # guard; `current()` no debería devolver None a continuación.
                if run is None:
                    raise InvariantViolation(
                        "BudgetGuard.authorize acaba de confirmar un Run en RUNNING; "
                        "RunRepository.current() no debería devolver None en la misma "
                        "unidad de trabajo"
                    )
                run_id = run.id

            if timeout_s <= 0:
                # `BudgetGuard.timeout_for_call` (regla 6 de `budget.py`)
                # trunca a 0 a menos de un segundo de `hard_stop`. Lanzar la
                # llamada igual solo para que `asyncio.timeout(0)` la mate de
                # inmediato factura un subproceso sin ninguna posibilidad
                # real de respuesta, y el fallo resultante sería un
                # `LLMTimeout` falso: nadie llegó a preguntarle nada al
                # modelo. Se trata como "no llames" (docstring de
                # `timeout_for_call`): sin AgentCall, coste ~0, terminal.
                return ReadItemResult(
                    outcome=ReadOutcome.TIMEOUT,
                    reading=None,
                    attempts=attempt,
                    tokens_spent=tokens_spent,
                )

            request = AgentRequest(
                role=AgentRole.READER,
                model=self._model,
                prompt=self._build_prompt(item),
                max_turns=self._max_turns,
                timeout_s=timeout_s,
                item_id=item.id,
                system_prompt=self._system_prompt,
            )

            # --- fuera de toda transacción: ninguna conexión de
            # PostgreSQL retenida mientras se espera al LLM ---------------
            started_at = monotonic()
            try:
                result = await self._provider.run_agent(request)
            except LLMRateLimited as exc:
                # Antes que LLMError (de la que es subclase): un límite de
                # tasa termina la noche, no se reintenta
                # (budget-guard-review § 6).
                return self._record_terminal_failure(
                    run_id=run_id,
                    item=item,
                    tokens_in=exc.tokens_in,
                    tokens_out=exc.tokens_out,
                    duration_ms=_duration_ms(started_at),
                    status=AgentCallStatus.ERROR,
                    outcome=ReadOutcome.RATE_LIMITED,
                    attempt=attempt,
                    tokens_spent=tokens_spent,
                )
            except LLMTimeout as exc:
                return self._record_terminal_failure(
                    run_id=run_id,
                    item=item,
                    tokens_in=exc.tokens_in,
                    tokens_out=exc.tokens_out,
                    duration_ms=_duration_ms(started_at),
                    status=AgentCallStatus.TIMEOUT,
                    outcome=ReadOutcome.TIMEOUT,
                    attempt=attempt,
                    tokens_spent=tokens_spent,
                )
            except LLMError as exc:
                return self._record_terminal_failure(
                    run_id=run_id,
                    item=item,
                    tokens_in=exc.tokens_in,
                    tokens_out=exc.tokens_out,
                    duration_ms=_duration_ms(started_at),
                    status=AgentCallStatus.ERROR,
                    outcome=ReadOutcome.AGENT_ERROR,
                    attempt=attempt,
                    tokens_spent=tokens_spent,
                )
            except asyncio.CancelledError as exc:
                # No se traduce: el corte de `hard_stop` (T44) depende de
                # que esta excepción llegue intacta. Solo se aprovecha para
                # contabilizar, en su propia unidad de trabajo, el gasto que
                # `AgentSDKProvider` le haya podido adjuntar antes de
                # relanzarla (ver ese módulo): la suscripción ya lo pagó.
                self._record_cancelled_spend(
                    run_id=run_id, item=item, exc=exc, duration_ms=_duration_ms(started_at)
                )
                raise

            duration_ms = _duration_ms(started_at)

            try:
                parsed = parse_reader_output(result.output_text)
            except InvalidAgentOutput:
                # Llamada exitosa, JSON inválido: el intento cuenta en
                # tokens y en el contador de llamadas, y sí se reintenta.
                call = self._record_invalid_output_attempt(
                    run_id=run_id, item=item, result=result, duration_ms=duration_ms
                )
                tokens_spent += call.total_tokens
                continue

            try:
                reading = Reading(
                    item_id=item.id,
                    summary=parsed.summary,
                    objects=tuple(parsed.objects),
                    claims=tuple(parsed.claims),
                    interest_score=parsed.interest_score,
                    tokens_in=result.tokens_in,
                    tokens_out=result.tokens_out,
                    model=result.model,
                )
            except InvariantViolation:
                # Cinturón sobre tirantes: `ReaderOutput`
                # (`application/agents/reader_output.py`) ya replica las
                # invariantes de `Reading.__post_init__`, así que esta rama
                # no debería alcanzarse hoy. Se mantiene por si ambas formas
                # vuelven a divergir en el futuro (el bloqueante original de
                # T41): sin ella, una llamada ya cobrada reventaría entre
                # `run_agent` y `record_call` sin dejar `AgentCall` ni
                # reintento, exactamente el bug que se corrige aquí. Barato
                # de mantener, se trata igual que un JSON inválido.
                call = self._record_invalid_output_attempt(
                    run_id=run_id, item=item, result=result, duration_ms=duration_ms
                )
                tokens_spent += call.total_tokens
                continue

            # --- éxito: JSON válido -----------------------------------
            call = AgentCall(
                run_id=run_id,
                item_id=item.id,
                agent=AgentRole.READER,
                model=result.model,
                tokens_in=result.tokens_in,
                tokens_out=result.tokens_out,
                duration_ms=duration_ms,
                status=AgentCallStatus.OK,
                prompt_version=self._prompt_version,
            )
            # Unidad de trabajo 1 de 2: solo la contabilidad. Separada de la
            # escritura de `Reading`/`Item` para que un `IntegrityError` de
            # esta última (p. ej. `uq_readings_item_id` ante dos `run-item`
            # concurrentes sobre el mismo ítem) no deshaga, con su rollback,
            # el `AgentCall` de una llamada ya cobrada.
            with self._work() as w:
                current_run = w.runs.get(run_id)
                if current_run is None:
                    raise InvariantViolation(
                        "RunRepository.get(run_id) no debería devolver None: el "
                        "run_id proviene de un Run leído en esta misma llamada"
                    )
                w.guard.record_call(call, current_run)

            # Unidad de trabajo 2 de 2: el `Reading` y la transición de
            # `Item`, ya sin el gasto de por medio.
            item.mark_read()
            with self._work() as w:
                w.readings.add(reading)
                w.items.save(item)

            tokens_spent += call.total_tokens
            return ReadItemResult(
                outcome=ReadOutcome.READ,
                reading=reading,
                attempts=attempt,
                tokens_spent=tokens_spent,
            )

        # Se agotaron los intentos sin producir un JSON válido: fallo
        # terminal, el ítem no vuelve a ofrecerse (CLAUDE.md, "Agentes").
        item.mark_failed()
        with self._work() as w:
            w.items.save(item)
        return ReadItemResult(
            outcome=ReadOutcome.INVALID_OUTPUT,
            reading=None,
            attempts=self._max_attempts,
            tokens_spent=tokens_spent,
        )

    def _record_invalid_output_attempt(
        self, *, run_id: UUID, item: Item, result: AgentResult, duration_ms: int
    ) -> AgentCall:
        """Registra, en su propia unidad de trabajo, un intento con salida inválida.

        Compartido entre el fallo de `parse_reader_output` (JSON que no
        cumple `ReaderOutput`) y el cinturón de seguridad que captura
        `InvariantViolation` si `ReaderOutput` y `Reading.__post_init__`
        llegaran a divergir otra vez: ambos casos son "llamada exitosa,
        salida no utilizable", se contabilizan igual y se reintentan igual.
        """
        call = AgentCall(
            run_id=run_id,
            item_id=item.id,
            agent=AgentRole.READER,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            duration_ms=duration_ms,
            status=AgentCallStatus.INVALID_OUTPUT,
            prompt_version=self._prompt_version,
        )
        with self._work() as w:
            current_run = w.runs.get(run_id)
            if current_run is None:
                raise InvariantViolation(
                    "RunRepository.get(run_id) no debería devolver None: el run_id "
                    "proviene de un Run leído en esta misma llamada"
                )
            w.guard.record_call(call, current_run)
        return call

    def _record_cancelled_spend(
        self, *, run_id: UUID, item: Item, exc: asyncio.CancelledError, duration_ms: int
    ) -> None:
        """Contabiliza, si procede, el gasto ya cobrado de una llamada cancelada.

        `AgentSDKProvider.run_agent` adjunta `tokens_in`/`tokens_out` a la
        `CancelledError` que relanza cuando ya había visto un `ResultMessage`
        con `usage` antes de la cancelación (ver ese módulo); pueden no estar
        presentes si la cancelación llegó antes de cualquier resultado, así
        que se leen con `getattr(..., 0)`. Sin este registro, una
        cancelación a mitad de llamada (el corte de `hard_stop`, T44) tiraba
        el gasto ya incurrido sin dejar `AgentCall`.

        Cualquier fallo al contabilizar (por ejemplo, un problema de
        conexión a la base de datos durante el propio corte de `hard_stop`)
        se registra con `logging` y se descarta: este método nunca debe
        sustituir la `CancelledError` que motivó la llamada por una
        excepción distinta -- si lo hiciera, `raise` en el llamador
        propagaría el fallo de contabilidad en vez de la cancelación
        original, rompiendo el corte incondicional que `CLAUDE.md` exige
        para `hard_stop`. Mismo patrón que `AgentSDKProvider.run_agent` ya
        aplica a los fallos de `agen.aclose()` en su propio `finally`.
        """
        tokens_in = getattr(exc, "tokens_in", 0)
        tokens_out = getattr(exc, "tokens_out", 0)
        if not tokens_in and not tokens_out:
            return
        call = AgentCall(
            run_id=run_id,
            item_id=item.id,
            agent=AgentRole.READER,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            status=AgentCallStatus.ERROR,
            prompt_version=self._prompt_version,
        )
        try:
            with self._work() as w:
                current_run = w.runs.get(run_id)
                if current_run is not None:
                    w.guard.record_call(call, current_run)
        except Exception:
            _logger.warning(
                "no se pudo contabilizar el gasto de una llamada al Reader cancelada "
                "(run_id=%s, item_id=%s); se descarta para no sustituir la "
                "CancelledError original",
                run_id,
                item.id,
                exc_info=True,
            )

    def _record_terminal_failure(
        self,
        *,
        run_id: UUID,
        item: Item,
        tokens_in: int,
        tokens_out: int,
        duration_ms: int,
        status: AgentCallStatus,
        outcome: ReadOutcome,
        attempt: int,
        tokens_spent: int,
    ) -> ReadItemResult:
        """Registra un intento fallido (límite de tasa, timeout o error) y termina.

        Ninguno de estos tres casos reintenta ni marca el `Item` como
        `FAILED`: `item` se queda en `NEW`, disponible para que una noche
        futura vuelva a intentarlo -- `CLAUDE.md` reserva `mark_failed()`
        para el agotamiento de reintentos por JSON inválido, no para un
        fallo transitorio del proveedor. El gasto ya incurrido se
        contabiliza igual que si la llamada hubiera tenido éxito (ADR 0006
        § 2): la suscripción ya lo pagó.
        """
        call = AgentCall(
            run_id=run_id,
            item_id=item.id,
            agent=AgentRole.READER,
            model=self._model,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            duration_ms=duration_ms,
            status=status,
            prompt_version=self._prompt_version,
        )
        with self._work() as w:
            current_run = w.runs.get(run_id)
            if current_run is None:
                raise InvariantViolation(
                    "RunRepository.get(run_id) no debería devolver None: el run_id "
                    "proviene de un Run leído en esta misma llamada"
                )
            w.guard.record_call(call, current_run)
        return ReadItemResult(
            outcome=outcome,
            reading=None,
            attempts=attempt,
            tokens_spent=tokens_spent + call.total_tokens,
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


def _duration_ms(started_at: float) -> int:
    return int((monotonic() - started_at) * 1000)
