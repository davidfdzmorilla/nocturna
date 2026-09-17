"""`AgentRunner`: la maquinaria de gasto compartida por Reader, Popularizer y Editor.

Refactor de T42. Extrae, generalizado por `role`, las ~170 líneas que antes
vivían en `ReadItem.__call__` y sus tres helpers de contabilización, que no
tienen nada que ver con "leer un `Item`": el bucle de intentos, la unidad de
trabajo de `authorize` + `timeout_for_call` + lectura de `run_id`, la guarda
`timeout_s <= 0`, los `except LLMRateLimited` / `LLMTimeout` / `LLMError` /
`CancelledError` en su orden actual, y el registro de `AgentCall` en sus tres
variantes (éxito, salida inválida, fallo terminal). Nada de esto sabe qué es
un `Item`, un `Reading` ni un `ItemStatus` -- ver la sección "Qué NO hace" más
abajo --, así que sirve igual para `PopularizeReading` (siguiente paso) y
`EditNight` (T43) sin que ninguno de los tres tenga que reinventar el patrón
de ADR 0006 § 2.

`use_cases/read_item.py` ya está migrado, en este mismo commit: `ReadItem`
solo aporta lo específico del Reader (la guarda de `ItemStatus`, el prompt,
`build` y las transiciones de `Item`) y delega el resto en `AgentRunner.run`.
Ver el docstring de ese módulo para el detalle de la migración.

## La decisión de diseño que da sentido a todo esto: `build` corre DENTRO del runner

`build` es "parsea el JSON y construye la entidad de dominio" (para el
Reader: `parse_reader_output` + `Reading(...)`). El runner lo invoca **después**
de `run_agent` y **antes** de contabilizar el intento, nunca fuera de su propio
`run()`. Eso es lo que hace estructuralmente imposible el bloqueante que
cerró la revisión de T41: una llamada ya cobrada que revienta entre
`run_agent` y `record_call` sin dejar ningún `AgentCall` ni reintento. Antes,
cada caso de uso tenía que acordarse de envolver la construcción de su propia
entidad en el mismo `try` que trata el JSON inválido (`ReadItem` lo hacía con
un comentario "cinturón sobre tirantes" reconociendo que dependía de que
`ReaderOutput` y `Reading.__post_init__` no divergieran). Con `build` dentro
del runner, esa garantía es del runner, una vez, para los tres agentes: si
`build` lanza `InvalidAgentOutput` (el parseo del JSON) o `InvariantViolation`
(la construcción de la entidad), el runner registra el intento como
`invalid_output` y reintenta -- exactamente el mismo tratamiento, porque para
el presupuesto ambos casos son indistinguibles: "llamada exitosa, salida no
utilizable".

## Qué NO hace este runner

No conoce configuración (todo llega por constructor, igual que `ReadItem`
hoy). No persiste entidades de negocio -- solo `AgentCall`, con
`BudgetGuard.record_call`; la entidad que construye `build` viaja en
`RunnerResult.value` para que el caso de uso la persista en una unidad de
trabajo propia, después de que `run()` devuelva. No conoce `ItemStatus` ni
ninguna transición de `Item`/`Finding`/`Run` más allá de lo que
`BudgetGuard`/`RunRepository` ya exponen. No importa `infrastructure/`.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from time import monotonic
from uuid import UUID

from nocturna.application.agents.parsing import InvalidAgentOutput
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.domain.entities import AgentCall, AgentCallStatus
from nocturna.domain.errors import InvariantViolation, LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRequest, AgentResult, AgentRole, LLMProvider

_logger = logging.getLogger(__name__)


class AttemptOutcome(StrEnum):
    """Qué pasó al intentar completar un `AgentRunner.run()`.

    Vocabulario general (no específico del Reader), a diferencia de
    `ReadOutcome` (`use_cases/read_item.py`), que el siguiente paso del
    refactor traduce a `ReadOutcome` sin perder ningún caso: mismos cinco
    valores, mismo significado.
    """

    OK = "ok"
    INVALID_OUTPUT = "invalid_output"
    AGENT_ERROR = "agent_error"
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"


@dataclass(frozen=True, slots=True)
class RunnerResult[T]:
    """Resultado de `AgentRunner.run()`.

    `value` es la entidad que construyó `build` -- solo presente si
    `outcome` es `OK`, `None` en los otros cuatro casos. `tokens_spent` suma
    el gasto de **todos** los intentos de esta llamada a `run()`, incluidos
    los fallidos por salida inválida (igual que `ReadItemResult.tokens_spent`
    hoy): es la cifra que le interesa a quien orquesta la noche, coherente
    con `AgentCallRepository.tokens_used_for_run`, que también suma todos
    los intentos.

    `run_id` es `None` únicamente si nunca se llegó a autorizar ningún
    intento (`max_attempts <= 0`, un caso de configuración que no debería
    darse en producción, no algo que `AgentRunner` valide). En cualquier
    otro caso viene informado desde el primer `authorize()` con éxito, antes
    incluso de llamar al LLM: el caso de uso lo necesita para persistir la
    entidad después (`Finding.run_id`, por ejemplo).
    """

    outcome: AttemptOutcome
    value: T | None
    attempts: int
    tokens_spent: int
    run_id: UUID | None


class AgentRunner:
    """Ejecuta un agente contra `LLMProvider` detrás de `BudgetGuard`, con reintento.

    Toda la configuración llega por constructor: modelo, prompt de sistema,
    versión de prompt, turnos máximos, coste estimado e intentos máximos.
    Ningún valor de `config/pipeline.toml` se lee desde aquí -- eso es cosa
    de `cli.py`, el composition root, igual que hoy con `ReadItem`.
    """

    def __init__(
        self,
        *,
        work: AgentWorkFactory,
        provider: LLMProvider,
        role: AgentRole,
        model: str,
        system_prompt: str,
        prompt_version: str,
        estimated_tokens: int,
        max_turns: int,
        max_attempts: int,
    ) -> None:
        self._work = work
        self._provider = provider
        self._role = role
        self._model = model
        self._system_prompt = system_prompt
        self._prompt_version = prompt_version
        self._estimated_tokens = estimated_tokens
        self._max_turns = max_turns
        self._max_attempts = max_attempts

    async def run[T](
        self,
        *,
        prompt: str,
        item_id: UUID | None,
        build: Callable[[AgentResult, UUID], T],
    ) -> RunnerResult[T]:
        """Ejecuta hasta `max_attempts` intentos, reintentando solo la salida inválida.

        `prompt` es el contenido de usuario ya compuesto por el caso de uso
        (p. ej. `ReadItem._build_prompt`); este método no sabe de dónde
        sale. `item_id` viaja tal cual a `AgentRequest.item_id` y a
        `AgentCall.item_id` -- `None` para el Editor, que no lee un ítem
        concreto. `build(result, run_id)` parsea y construye la entidad de
        dominio a partir de `result`; recibe `run_id` porque alguna entidad
        lo necesita (p. ej. `Finding.run_id`), no porque el runner lo use.

        Cada intento abre, como máximo, dos unidades de trabajo separadas
        (regla 1 de `unit_of_work.py`/ADR 0006 § 2): una para `authorize` +
        `timeout_for_call` + leer `run_id`, y otra -- ya fuera de la espera
        al LLM -- para `record_call`, nunca compartida con ninguna otra
        escritura. La persistencia de `build(...)` es responsabilidad del
        caso de uso, en una tercera unidad de trabajo, después de que este
        método devuelva.
        """
        tokens_spent = 0
        run_id: UUID | None = None

        for attempt in range(1, self._max_attempts + 1):
            # --- unidad de trabajo 1: autorizar, nunca construir el
            # AgentRequest si se deniega. `BudgetDenied` (y sus subclases)
            # no la captura nadie aquí: no hereda de `DomainError` para
            # eso (regla 6), así que sale de `run()` sin traducir.
            with self._work() as w:
                w.guard.authorize(self._role, self._estimated_tokens)
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
                # `BudgetGuard.timeout_for_call` trunca a 0 a menos de un
                # segundo de `hard_stop`. Lanzar la llamada igual solo para
                # que `asyncio.timeout(0)` la mate de inmediato factura un
                # subproceso sin ninguna posibilidad real de respuesta, y el
                # fallo resultante sería un `LLMTimeout` falso: nadie llegó
                # a preguntarle nada al modelo. Se trata como "no llames"
                # (docstring de `timeout_for_call`): sin AgentCall, coste
                # ~0, terminal.
                return RunnerResult(
                    outcome=AttemptOutcome.TIMEOUT,
                    value=None,
                    attempts=attempt,
                    tokens_spent=tokens_spent,
                    run_id=run_id,
                )

            request = AgentRequest(
                role=self._role,
                model=self._model,
                prompt=prompt,
                max_turns=self._max_turns,
                timeout_s=timeout_s,
                item_id=item_id,
                system_prompt=self._system_prompt,
            )

            # --- fuera de toda transacción: ninguna conexión de PostgreSQL
            # retenida mientras se espera al LLM ---------------------------
            started_at = monotonic()
            try:
                result = await self._provider.run_agent(request)
            except LLMRateLimited as exc:
                # Antes que LLMError (de la que es subclase): un límite de
                # tasa termina la noche, no se reintenta
                # (budget-guard-review § 6).
                return self._record_terminal_failure(
                    run_id=run_id,
                    item_id=item_id,
                    tokens_in=exc.tokens_in,
                    tokens_out=exc.tokens_out,
                    duration_ms=_duration_ms(started_at),
                    status=AgentCallStatus.ERROR,
                    outcome=AttemptOutcome.RATE_LIMITED,
                    attempt=attempt,
                    tokens_spent=tokens_spent,
                )
            except LLMTimeout as exc:
                return self._record_terminal_failure(
                    run_id=run_id,
                    item_id=item_id,
                    tokens_in=exc.tokens_in,
                    tokens_out=exc.tokens_out,
                    duration_ms=_duration_ms(started_at),
                    status=AgentCallStatus.TIMEOUT,
                    outcome=AttemptOutcome.TIMEOUT,
                    attempt=attempt,
                    tokens_spent=tokens_spent,
                )
            except LLMError as exc:
                return self._record_terminal_failure(
                    run_id=run_id,
                    item_id=item_id,
                    tokens_in=exc.tokens_in,
                    tokens_out=exc.tokens_out,
                    duration_ms=_duration_ms(started_at),
                    status=AgentCallStatus.ERROR,
                    outcome=AttemptOutcome.AGENT_ERROR,
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
                    run_id=run_id,
                    item_id=item_id,
                    exc=exc,
                    duration_ms=_duration_ms(started_at),
                )
                raise

            duration_ms = _duration_ms(started_at)

            try:
                value = build(result, run_id)
            except (InvalidAgentOutput, InvariantViolation):
                # Llamada exitosa, salida no utilizable (JSON que no parsea
                # o que no cumple la invariante de la entidad): el intento
                # cuenta en tokens y en el contador de llamadas, y sí se
                # reintenta. El reintento vive en el bucle, vía este
                # `continue` -- nunca llamando a `run_agent` de nuevo desde
                # dentro de este `except` --: T40 corrigió un bug de
                # `sys.exc_info()` que se disparaba exactamente en ese
                # patrón (`agent_sdk_provider.py`, docstring de
                # `run_agent`), porque `sys.exc_info()` refleja el estado de
                # excepción de todo el hilo, no de este frame, y seguiría
                # "viendo" esta excepción mientras la retransmisión ocurriera
                # dentro de su propio manejador. `continue` devuelve el
                # control a la cabecera del `for`; para cuando el siguiente
                # `await self._provider.run_agent(...)` se ejecuta, este
                # `except` ya se abandonó y `sys.exc_info()` está limpio.
                call = self._record_attempt(
                    run_id=run_id,
                    item_id=item_id,
                    result=result,
                    duration_ms=duration_ms,
                    status=AgentCallStatus.INVALID_OUTPUT,
                )
                tokens_spent += call.total_tokens
                continue
            except BaseException:
                # `BaseException`, no `Exception`: entre el `return` de
                # `run_agent` y aquí no hay ningún `await`, así que asyncio no
                # puede entregar una cancelación en esta ventana -- pero una
                # señal sí (`KeyboardInterrupt`, `SystemExit`), y el corte de
                # `hard_stop` a las 04:45 (T44) es precisamente cuando llegan
                # señales. Con `except Exception` esa ventana de microsegundos
                # se colaba sin contabilizar una llamada ya pagada; con
                # `except BaseException` (que ya relanza igual, sin tragarse
                # nada) queda cerrada. Aparte de eso, cualquier otra excepción
                # de `build` es un bug de programación (un `TypeError`, un
                # `KeyError`, cualquier cosa que no sea "el agente devolvió
                # algo que no vale"), no una salida mala del agente:
                # reintentarla repetiría el mismo bug gastando otra llamada,
                # así que aquí NO hay `continue`. Pero la llamada al LLM ya
                # ocurrió y ya se pagó (invariante 10: se contabiliza en los
                # seis caminos), así que se registra como `ERROR` -- el más
                # parecido de los cuatro valores de `AgentCallStatus`, ninguno
                # pensado para esto exactamente -- antes de relanzar la
                # excepción original intacta, para que quien la vea sepa que
                # hay que arreglar `build`, no el runner. Si la propia
                # contabilización fallara, se descarta con `logging` (mismo
                # patrón que `_record_cancelled_spend`): este camino nunca
                # debe sustituir la excepción real del bug por un fallo de
                # contabilidad. No captura `BudgetDenied` (se lanza en la
                # unidad de trabajo 1, fuera de este `try`) ni altera el trato
                # de `asyncio.CancelledError` de la llamada a `run_agent`, que
                # se maneja antes y en su propio `except`, aparte de este.
                self._record_build_failure(
                    run_id=run_id, item_id=item_id, result=result, duration_ms=duration_ms
                )
                raise

            # --- éxito: `build` produjo una entidad válida -----------------
            call = self._record_attempt(
                run_id=run_id,
                item_id=item_id,
                result=result,
                duration_ms=duration_ms,
                status=AgentCallStatus.OK,
            )
            tokens_spent += call.total_tokens
            return RunnerResult(
                outcome=AttemptOutcome.OK,
                value=value,
                attempts=attempt,
                tokens_spent=tokens_spent,
                run_id=run_id,
            )

        # Se agotaron los intentos sin producir una salida utilizable.
        return RunnerResult(
            outcome=AttemptOutcome.INVALID_OUTPUT,
            value=None,
            attempts=self._max_attempts,
            tokens_spent=tokens_spent,
            run_id=run_id,
        )

    def _record_attempt(
        self,
        *,
        run_id: UUID,
        item_id: UUID | None,
        result: AgentResult,
        duration_ms: int,
        status: AgentCallStatus,
    ) -> AgentCall:
        """Registra, en su propia unidad de trabajo, un intento ya resuelto (éxito o inválido).

        Única en esta unidad de trabajo (regla 2): ninguna otra escritura
        comparte transacción con `record_call`, para que un `IntegrityError`
        de una escritura posterior (que hace el caso de uso, no este
        método) no se lleve por delante, con su rollback, el `AgentCall` de
        una llamada ya cobrada.
        """
        call = AgentCall(
            run_id=run_id,
            item_id=item_id,
            agent=self._role,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
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
        return call

    def _record_cancelled_spend(
        self, *, run_id: UUID, item_id: UUID | None, exc: asyncio.CancelledError, duration_ms: int
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
            item_id=item_id,
            agent=self._role,
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
                else:
                    _logger.warning(
                        "RunRepository.get(run_id) devolvió None al intentar contabilizar "
                        "el gasto de una llamada cancelada; el gasto ya cobrado por la "
                        "suscripción se descarta sin dejar AgentCall "
                        "(run_id=%s, item_id=%s, role=%s)",
                        run_id,
                        item_id,
                        self._role.value,
                    )
        except Exception:
            _logger.warning(
                "no se pudo contabilizar el gasto de una llamada cancelada "
                "(run_id=%s, item_id=%s, role=%s); se descarta para no sustituir "
                "la CancelledError original",
                run_id,
                item_id,
                self._role.value,
                exc_info=True,
            )

    def _record_build_failure(
        self, *, run_id: UUID, item_id: UUID | None, result: AgentResult, duration_ms: int
    ) -> None:
        """Contabiliza el gasto de un intento cuyo `build(...)` reventó de forma inesperada.

        Solo se alcanza desde el `except BaseException` de `run()`, es decir,
        para cualquier excepción de `build` que NO sea `InvalidAgentOutput`
        ni `InvariantViolation` -- un bug de programación, no una salida
        mala del agente (ver el comentario de esa rama en `run()`). Mismo
        patrón defensivo que `_record_cancelled_spend`: un fallo al
        contabilizar se registra con `logging` y se descarta, para no
        sustituir la excepción original de `build` -- la única señal útil
        de qué hay que arreglar -- por un fallo de contabilidad.
        """
        call = AgentCall(
            run_id=run_id,
            item_id=item_id,
            agent=self._role,
            model=result.model,
            tokens_in=result.tokens_in,
            tokens_out=result.tokens_out,
            duration_ms=duration_ms,
            status=AgentCallStatus.ERROR,
            prompt_version=self._prompt_version,
        )
        try:
            with self._work() as w:
                current_run = w.runs.get(run_id)
                if current_run is not None:
                    w.guard.record_call(call, current_run)
                else:
                    _logger.warning(
                        "RunRepository.get(run_id) devolvió None al intentar contabilizar "
                        "el gasto de una llamada cuyo build() reventó de forma inesperada; "
                        "el gasto ya cobrado por la suscripción se descarta sin dejar "
                        "AgentCall (run_id=%s, item_id=%s, role=%s)",
                        run_id,
                        item_id,
                        self._role.value,
                    )
        except Exception:
            _logger.warning(
                "no se pudo contabilizar el gasto de una llamada cuyo build() reventó "
                "de forma inesperada (run_id=%s, item_id=%s, role=%s); se descarta para "
                "no sustituir la excepción original de build() por un fallo de contabilidad",
                run_id,
                item_id,
                self._role.value,
                exc_info=True,
            )

    def _record_terminal_failure[T](
        self,
        *,
        run_id: UUID,
        item_id: UUID | None,
        tokens_in: int,
        tokens_out: int,
        duration_ms: int,
        status: AgentCallStatus,
        outcome: AttemptOutcome,
        attempt: int,
        tokens_spent: int,
    ) -> RunnerResult[T]:
        """Registra un intento fallido (límite de tasa, timeout o error) y termina `run()`.

        Ninguno de estos tres casos reintenta: un límite de tasa o un
        timeout/error del proveedor es señal de que algo va mal a nivel de
        la llamada, no de la salida -- reintentar de inmediato contra el
        mismo problema no lo arregla (`budget-guard-review` § 6). El gasto
        ya incurrido se contabiliza igual que si la llamada hubiera tenido
        éxito (ADR 0006 § 2): la suscripción ya lo pagó. `value` siempre es
        `None` aquí -- parametrizado por `T` solo para que el tipo del
        `RunnerResult` que devuelve case con el de `run[T]`, que lo relanza
        tal cual en sus tres llamadas a este método.
        """
        call = AgentCall(
            run_id=run_id,
            item_id=item_id,
            agent=self._role,
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
        return RunnerResult(
            outcome=outcome,
            value=None,
            attempts=attempt,
            tokens_spent=tokens_spent + call.total_tokens,
            run_id=run_id,
        )


def _duration_ms(started_at: float) -> int:
    return int((monotonic() - started_at) * 1000)
