"""Caso de uso: `RunNight`, el orquestador de una noche completa.

Compone, sin llamar nunca a `LLMProvider.run_agent` directamente, los tres
casos de uso ya existentes -- `ReadItem` (T41), `PopularizeReading` (T42),
`EditNight` (T43) -- en el orden que fija `CLAUDE.md` § "Control de gasto":
primero el Reader sobre todos los ítems nuevos (barato), después el
Popularizer sobre los leídos, y el Editor al final, con la reserva de
presupuesto intacta por construcción (`BudgetGuard._available_tokens`,
`application/budget.py`, regla 4). No conoce `BudgetPolicy` -- recibe
`deadline_s` ya calculado por `cli.py`, el composition root -- ni calcula
ninguna estimación de tokens: ese conocimiento vive exclusivamente en
`BudgetGuard` y en los tres casos de uso que ya lo usan. No cierra el `Run`
ni toca `Run.status`: devuelve el estado propuesto en `RunNightResult`, y el
único cerrador sigue siendo `_finish_run` de `cli.py` (paso 3, fuera del
alcance de este módulo).

## El modelo de "degradación monótona" que decide `RunNightResult.status`

La noche empieza optimista (`RunStatus.COMPLETED`) y solo empeora: cada
suceso que la aparta de un cierre limpio llama a `_degrade(status, reason)`,
que solo sustituye el estado actual si el nuevo es más severo
(`COMPLETED < PARTIAL < KILLED`, `_STATUS_RANK`). Con esto, un evento leve
(un ítem que no se pudo leer, una divulgación con JSON inválido agotados
los reintentos) nunca sobreescribe un `KILLED` ya fijado por cruzar
`hard_stop`, y una ingesta fallida seguida de una noche por lo demás
perfecta cierra `PARTIAL` igual (`CLAUDE.md`, mapa de desenlaces del plan de
esta tarea) sin que nadie tenga que recordar comprobarlo al final. Los
sucesos que degradan, y a qué:

- Ingesta fallida (`except Exception` alrededor de `ingest()`, el único
  punto de la ingesta que ve este módulo: no conoce `ArxivUnavailable` ni
  ningún otro tipo de `infrastructure/`) -> `PARTIAL`, motivo
  `ingest_error`. El resto de la noche sigue con lo que ya hubiera en base
  (`ItemRepository.next_unread` no depende de que la ingesta de esta noche
  haya añadido nada).
- `BudgetDenied` en la fase A o B, motivo `BUDGET_EXHAUSTED`,
  `CALL_LIMIT_REACHED` o `RUN_NOT_RUNNING` -> termina esa fase (no el
  ítem), degrada a `terminal_status_for(reason)` (siempre `PARTIAL` para
  estos tres motivos) y la noche **sigue** hacia la fase siguiente: la
  reserva del Editor nunca se toca por Reader/Popularizer
  (`BudgetGuard._available_tokens`), así que la noche puede publicar lo que
  ya tenga.
- `BudgetDenied` en cualquier fase, motivo `OUTSIDE_WINDOW` ->
  `terminal_status_for` ya devuelve `KILLED` para este motivo; además marca
  `self._skip_editor = True`: si la ventana se cerró, no tiene sentido
  seguir camino hacia la llamada más cara de la noche.
- `ReadOutcome.RATE_LIMITED` / `PopularizeOutcome.RATE_LIMITED` (desenlace
  normal de `ReadItem`/`PopularizeReading`, no una excepción: ambos casos
  de uso ya capturan `LLMRateLimited` dentro de `AgentRunner.run` y lo
  traducen) -> aborta esa fase, `PARTIAL`, y **también** `self._skip_editor
  = True`: si la suscripción está limitando peticiones, la llamada más
  cara de la noche es la última que conviene lanzar (motivo explícito del
  plan de esta tarea).
- El cortacircuitos de `max_consecutive_failures` (ítems consecutivos, en
  una misma fase, cuyo desenlace no es el "bueno" de esa fase --
  `ReadOutcome.READ` en A, `PopularizeOutcome.POPULARIZED` en B -- ni un
  desenlace neutro que tampoco toca el contador en ningún sentido
  -- `InvalidTransition` en ambas fases, `PopularizeOutcome.SKIPPED_LOW_SCORE`
  en B, revisión de T44 punto 4: ese desenlace no implica ninguna llamada
  al proveedor, así que no es evidencia de que esté sano ni de que esté
  fallando) -> aborta esa fase, `PARTIAL`, pero **no** toca
  `self._skip_editor`: el Editor todavía se llama, con los candidatos que
  haya.
- El Editor con `EditOutcome` distinto de `EDITED`/`NO_CANDIDATES`
  (`INVALID_OUTPUT`, `TIMEOUT`, `AGENT_ERROR`, `RATE_LIMITED`) -> `PARTIAL`.
  `BudgetDenied` al llamar al Editor: `EDITOR_ALERADY_CALLED` (no debería
  ocurrir nunca -- este módulo llama al Editor como máximo una vez -- pero
  se trata igual que `cli.py::_edit_one_night`, sin pasar por
  `terminal_status_for`, que lanza `ValueError` a propósito para ese
  motivo) degrada a `PARTIAL`; cualquier otro motivo usa
  `terminal_status_for` normalmente.
- Ningún suceso arriba -> la noche cierra `COMPLETED` con motivo `edited` o
  `no_candidates`, el valor de `EditOutcome` que produjo el Editor.

## `items_failed` es una métrica de la fase A, no de toda la noche

`RunNightResult.items_failed` cuenta únicamente ítems de la fase del
Reader que no terminaron en `ReadOutcome.READ` -- por excepción inesperada
(`except Exception`, un bug real que nunca debe tumbar la noche) o por
cualquier desenlace normal distinto de `READ` (`INVALID_OUTPUT`,
`AGENT_ERROR`, `TIMEOUT`, `RATE_LIMITED`) --, nunca fallos del Popularizer:
`RunNightResult` no tiene un campo para eso (la interfaz de esta tarea fija
seis campos exactos), y el formato de `notes` ("read=N(failed=M)") es,
literalmente, sobre la fase de lectura. Los fallos del Popularizer solo
quedan en los logs `night.item`/`night.phase_end`, no en ningún contador
agregado. `InvalidTransition` (una carrera con `run-item` sobre el mismo
`Item`) tampoco cuenta como fallo: es un `warning`, la noche sigue, y no es
evidencia de ningún problema real del ítem.

## Cancelación por `hard_stop`: un vigía, una `asyncio.Task`, un límite claro

`__call__` no ejecuta el cuerpo de la noche directamente: lo lanza como
`asyncio.Task` (`_run_body`) y, si `deadline_s > 0`, arranca un segundo
`asyncio.Task` vigía (`_watch_hard_stop`) que duerme `deadline_s` segundos
y, si se despierta, marca `self._hard_stop_fired = True` y cancela
`_run_body` -- una sola vez, porque `asyncio.sleep` solo se completa una
vez. `await night_task` desde `__call__` devuelve el `RunNightResult`
normal si `_run_body` termina antes, o levanta `asyncio.CancelledError` si
el vigía lo cortó primero (o si algo externo -- un Ctrl-C, una cancelación
del proceso padre -- cancela `night_task` sin que el vigía haya disparado).
Al capturar esa excepción: si `self._hard_stop_fired` es `False`, se
**relanza tal cual** -- no es asunto de `RunNight` disfrazar una
cancelación ajena de `KILLED`; si es `True`, `_build_killed_result` hace
**solo trabajo síncrono** (leer los contadores ya acumulados en atributos
de instancia, abrir una `AgentWorkFactory` -- gestor de contexto síncrono,
sin `await` -- para la lectura de cierre de `tokens_used_for_run`, montar
`notes` y loguear `night.end`) y devuelve `RunNightResult(status=KILLED,
...)`. Ningún `await` en ese camino: una segunda entrega de cancelación
mientras se está construyendo el resultado de la primera sería exactamente
el tipo de bug que T41 ya pagó una vez con `AgentRunner._record_cancelled_spend`
(ver su docstring). El vigía se cancela siempre, en el `finally`, tanto si
`_run_body` terminó normal como si fue él quien la cortó.

`_build_killed_result` fuerza tanto `self._status` como `self._end_reason`
sin ninguna condición -- antes de la revisión de T44 (punto 1) el motivo
solo se forzaba `if not self._end_reason`, así que una ingesta fallida que
ya hubiera dejado `_end_reason = "ingest_error"` cerraba `KILLED` con
`notes` diciendo `end=ingest_error`: el estado degradaba de forma
monótona pero el motivo no lo seguía, y es justo el campo que se lee para
diagnosticar una noche a la mañana siguiente. El valor que se fuerza es
`self._deadline_reason`, no el literal `"hard_stop"` a secas: `cli.py` es
quien calcula si lo que cortó la noche fue de verdad la ventana de
ejecución o `limits.run_timeout_s` (ambos producen el mismo vigía, el
mismo `asyncio.CancelledError`, pero motivos distintos que el autor
necesita distinguir en `notes`, revisión de T44 punto 2) y lo pasa por
constructor; `RunNight` no ve `BudgetPolicy` y no puede calcularlo por su
cuenta.

Los contadores (`self._items_fetched`, `self._items_read`, etc.) viven como
atributos de instancia, actualizados incrementalmente durante `_run_body`,
precisamente para que sigan siendo legibles después de que la tarea que los
escribía haya sido cancelada a mitad de camino: no hay ningún valor de
retorno de `_run_body` disponible en ese caso, solo lo que ya se escribió
en `self`.

## Por qué `deadline_s <= 0` no arranca ningún vigía

Si `cli.py` calcula un `deadline_s` que ya es `<= 0` (la ventana ya está
cerrada en el instante de construir `RunNight`), lanzar un vigía que
duerme un número no positivo de segundos no aporta nada: el primer
`authorize()` que intente cualquiera de los tres casos de uso -- en cuanto
haya algo que ofrecerles -- ya deniega con `OutsideExecutionWindow`
(`BudgetGuard.check`, orden de comprobación: ventana antes que
presupuesto), y esa denegación se trata igual que cualquier otro
`OUTSIDE_WINDOW`: `KILLED`, `self._skip_editor = True`. Una noche sin
ningún candidato (ni ítems nuevos que leer ni `Finding` pendiente) no
llega a autorizar nada y podría cerrar `COMPLETED` incluso con
`deadline_s <= 0` -- caso extremo (arrancar el pipeline ya pasado
`hard_stop` sin trabajo pendiente) fuera del alcance de esta tarea; queda
anotado como decisión abierta, no resuelto por inventiva.

## Lo que este módulo NO hace, a propósito

No llama nunca a `LLMProvider.run_agent` ni construye ningún
`AgentRequest`: eso es exclusivo de `AgentRunner`
(`application/agents/runner.py`), al que llega indirectamente a través de
`ReadItem`/`PopularizeReading`/`EditNight`. No importa `infrastructure/` ni
lee `config/pipeline.toml`: toda la configuración (`max_items`,
`max_consecutive_failures`, `deadline_s`, y los propios casos de uso ya
construidos con su modelo/prompt/versión) llega por constructor desde
`cli.py`. No calcula ninguna estimación de tokens ni ve `BudgetPolicy`: solo
conoce el número de segundos que le quedan a la noche (`deadline_s`), nunca
las horas de pared de la ventana. No cierra el `Run` (`Run.finish()`) ni
escribe `Run.status`/`Run.items_fetched`/etc.: eso es de `cli.py`, que lee
`RunNightResult` y decide.

## Divergencia documentada respecto al campo `window` de `night.start`

El plan de esta tarea pide que `night.start` incluya `window` junto a
`budget_tokens`/`deadline_s`/`max_items`. Como este módulo no ve
`BudgetPolicy` (restricción explícita del plan), no puede loguear las horas
de pared de la ventana (`window.start`/`window.hard_stop`); `window` se
loguea como `f"{deadline_s}s_remaining"`, una aproximación derivada del
único dato temporal que este módulo sí recibe. Decisión del implementador,
no del plan; señalada aquí y en el informe de la tarea para que el
orquestador la confirme o la corrija.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from uuid import UUID

from nocturna.application.budget import BudgetDenied, DenyReason, terminal_status_for
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.edit_night import EditNight, EditOutcome
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.popularize_reading import PopularizeOutcome, PopularizeReading
from nocturna.application.use_cases.read_item import ReadItem, ReadOutcome
from nocturna.domain.clock import Clock
from nocturna.domain.entities import Item, Reading, RunStatus
from nocturna.domain.errors import InvalidTransition

_logger = logging.getLogger(__name__)

#: Orden de severidad de `RunStatus.status` para `RunNight._degrade`: solo
#: `COMPLETED`, `PARTIAL` y `KILLED` participan (los tres estados terminales
#: que este módulo puede proponer; `FAILED` lo decide `cli.py` para
#: excepciones que escapan de aquí, `RUNNING` no es un desenlace).
_STATUS_RANK: dict[RunStatus, int] = {
    RunStatus.COMPLETED: 0,
    RunStatus.PARTIAL: 1,
    RunStatus.KILLED: 2,
}


@dataclass(frozen=True, slots=True)
class RunNightResult:
    """Resultado de `RunNight.__call__` para una noche completa.

    `tokens_used` se lee de `AgentCallRepository.tokens_used_for_run`, no de
    `Run.tokens_used` (regla 2 de `application/budget.py`: esa columna es
    una caché desnormalizada). `status` es el estado terminal propuesto --
    `cli.py` es quien de verdad cierra el `Run` con él, vía `Run.finish()`.
    """

    status: RunStatus
    notes: str
    items_fetched: int
    items_read: int
    items_failed: int
    candidates: int
    findings_published: int
    tokens_used: int


def _duration_ms(started_at: float) -> int:
    return int((monotonic() - started_at) * 1000)


class _ReadItemContractViolated(RuntimeError):
    """`ReadItem.__call__` devolvió `ReadOutcome.READ` sin una `Reading`.

    Mismo patrón que `cli.py::_ReadItemContractViolated` (T41, paso 8): un
    bug de `ReadItem`, nunca una entrada de usuario ni un desenlace
    esperable de la noche, así que no se traduce a ningún motivo de
    degradación -- se deja escapar de `RunNight.__call__` tal cual, para que
    `cli.py` la cierre `FAILED` (mapa de desenlaces del plan de esta tarea:
    "excepción que escapa de RunNight -> la traduce cli.py a FAILED, no la
    enmascares"). No es una excepción de dominio (no viola ninguna regla de
    negocio, es un contrato entre dos módulos de `application`), así que no
    vive en `domain/errors.py`.
    """


class RunNight:
    """Orquesta una noche completa: ingesta ya hecha por `ingest`, Reader,
    Popularizer, Editor, en ese orden, dentro de `deadline_s` segundos.

    Toda la configuración llega por constructor desde `cli.py`, el
    composition root: `read_item`/`popularize`/`edit_night` llegan ya
    construidos (modelo, prompt, intentos, estimación de coste por rol,
    todo resuelto), `ingest` ya ligado a `since`/`categories` de esta noche.
    Este módulo no lee `config/pipeline.toml` ni ve `BudgetPolicy`.
    """

    def __init__(
        self,
        *,
        work: AgentWorkFactory,
        clock: Clock,
        ingest: Callable[[], Awaitable[IngestResult]],
        read_item: ReadItem,
        popularize: PopularizeReading,
        edit_night: EditNight,
        run_id: UUID,
        max_items: int,
        max_consecutive_failures: int,
        deadline_s: int,
        deadline_reason: str = "hard_stop",
    ) -> None:
        self._work = work
        self._clock = clock
        self._ingest = ingest
        self._read_item = read_item
        self._popularize = popularize
        self._edit_night = edit_night
        self._run_id = run_id
        self._max_items = max_items
        self._max_consecutive_failures = max_consecutive_failures
        self._deadline_s = deadline_s
        #: Motivo con el que `_build_killed_result` cierra `notes` si el
        #: vigía dispara -- `"hard_stop"` (la ventana de ejecución) o
        #: `"run_timeout"` (`limits.run_timeout_s`), decidido en `cli.py`
        #: (el único módulo que ve `BudgetPolicy` y compara los dos
        #: valores; ver el docstring del módulo, "Cancelación por
        #: hard_stop"). Fijo por instancia, no reinicializado en
        #: `__call__`: no depende de nada que ocurra durante la noche.
        self._deadline_reason = deadline_reason

        # Estado mutable de la noche en curso, reinicializado al principio
        # de cada `__call__` (ver docstring del módulo, "Cancelación por
        # hard_stop": estos atributos son lo único que sobrevive si
        # `_run_body` se cancela a mitad de camino).
        self._status: RunStatus = RunStatus.COMPLETED
        self._end_reason: str = ""
        self._skip_editor: bool = False
        self._hard_stop_fired: bool = False
        self._items_fetched: int = 0
        self._items_read: int = 0
        self._items_failed: int = 0
        self._candidates: int = 0
        self._findings_published: int = 0
        self._ingest_status: str = "ok"
        self._ingest_error: str | None = None
        self._ingest_fetched: int = 0

    async def __call__(self) -> RunNightResult:
        """Ejecuta la noche completa. Ver el docstring del módulo para el
        modelo de degradación de `status` y la arquitectura de cancelación."""
        self._status = RunStatus.COMPLETED
        self._end_reason = ""
        self._skip_editor = False
        self._hard_stop_fired = False
        self._items_fetched = 0
        self._items_read = 0
        self._items_failed = 0
        self._candidates = 0
        self._findings_published = 0
        self._ingest_status = "ok"
        self._ingest_error = None
        self._ingest_fetched = 0

        with self._work() as w:
            run = w.runs.get(self._run_id)
        budget_tokens = run.budget_tokens if run is not None else None

        _logger.info(
            "night.start",
            extra={
                "event": "night.start",
                "run_id": str(self._run_id),
                "budget_tokens": budget_tokens,
                "deadline_s": self._deadline_s,
                "window": f"{self._deadline_s}s_remaining",
                "max_items": self._max_items,
                "now": self._clock.now().isoformat(),
            },
        )

        started_at = monotonic()
        night_task: asyncio.Task[RunNightResult] = asyncio.create_task(self._run_body(started_at))
        watchdog: asyncio.Task[None] | None = None
        if self._deadline_s > 0:
            watchdog = asyncio.create_task(self._watch_hard_stop(night_task))

        try:
            result = await night_task
        except asyncio.CancelledError:
            if not self._hard_stop_fired:
                # No fue el vigía de esta noche: una cancelación externa (un
                # Ctrl-C, el proceso padre) no se disfraza de `KILLED`.
                raise
            result = self._build_killed_result(started_at)
        finally:
            if watchdog is not None:
                watchdog.cancel()

        return result

    async def _watch_hard_stop(self, task: "asyncio.Task[RunNightResult]") -> None:
        """Duerme `deadline_s` segundos y cancela `task`, una sola vez.

        `asyncio.sleep` solo se completa una vez por tarea, así que
        `task.cancel()` solo se invoca una vez desde aquí -- la garantía
        "una sola vez" que exige el plan de esta tarea sale gratis de que
        este `Task` solo tiene un cuerpo, no de ninguna comprobación
        explícita adicional.
        """
        await asyncio.sleep(self._deadline_s)
        self._hard_stop_fired = True
        task.cancel()

    def _build_killed_result(self, started_at: float) -> RunNightResult:
        """Construye el `RunNightResult` de una noche cortada por `hard_stop`.

        Solo trabajo síncrono (ver docstring del módulo, "Cancelación por
        hard_stop"): ningún `await` en este método. `self._status` y
        `self._end_reason` se fuerzan sin condición ni paso por `_degrade`
        -- `KILLED` es, por definición, el estado más severo, y el motivo
        que lo produjo (`self._deadline_reason`) debe reflejarse en `notes`
        siempre, incluso si una fase anterior ya había fijado un motivo más
        leve (`ingest_error`, un `circuit_breaker_*`...): el estado degrada
        de forma monótona y el motivo debe seguirlo (revisión de T44,
        punto 1).
        """
        self._status = RunStatus.KILLED
        self._end_reason = self._deadline_reason
        with self._work() as w:
            tokens_used = w.agent_calls.tokens_used_for_run(self._run_id)
        elapsed_s = monotonic() - started_at
        notes = self._build_notes(tokens_used)
        self._log_night_end(tokens_used=tokens_used, elapsed_s=elapsed_s, notes=notes)
        return self._result(tokens_used=tokens_used, notes=notes)

    async def _run_body(self, started_at: float) -> RunNightResult:
        """Cuerpo de la noche: ingesta, fase A, fase B, fase C, en ese orden.

        Lanzado como `asyncio.Task` por `__call__`; puede cancelarse a mitad
        de cualquier `await` de aquí abajo (el corte de `hard_stop`). Ningún
        `except BaseException`/`except Exception` de este método ni de las
        fases que invoca envuelve las llamadas a `self._read_item`/
        `self._popularize`/`self._edit_night` de forma que pueda tragarse
        `asyncio.CancelledError`: `except Exception` no la alcanza (hereda de
        `BaseException` desde Python 3.8), y los `except BudgetDenied`/
        `except InvalidTransition` son excepciones concretas no
        relacionadas.
        """
        await self._run_ingest_phase()

        read_pairs = await self._phase_reader()
        await self._phase_popularizer(read_pairs)

        if self._skip_editor:
            _logger.info(
                "night.phase_end",
                extra={
                    "event": "night.phase_end",
                    "run_id": str(self._run_id),
                    "phase": "editor",
                    "processed": 0,
                    "ok": 0,
                    "failed": 0,
                    "stop_reason": "skipped",
                },
            )
        else:
            await self._phase_editor()

        if not self._end_reason:
            # Camino feliz sin ninguna degradación: no debería alcanzarse
            # sin pasar por `_phase_editor` (que siempre fija un motivo en
            # su rama de éxito), pero se deja como red de seguridad en vez
            # de dejar `notes` con un `end=` vacío.
            self._end_reason = "edited"

        with self._work() as w:
            tokens_used = w.agent_calls.tokens_used_for_run(self._run_id)
        elapsed_s = monotonic() - started_at
        notes = self._build_notes(tokens_used)
        self._log_night_end(tokens_used=tokens_used, elapsed_s=elapsed_s, notes=notes)
        return self._result(tokens_used=tokens_used, notes=notes)

    # --- ingesta -----------------------------------------------------------

    async def _run_ingest_phase(self) -> None:
        """Ejecuta `self._ingest()`; una excepción aquí degrada `PARTIAL`
        pero no impide el resto de la noche (ver docstring del módulo).

        `except Exception`, no un tipo concreto: este módulo no importa
        `infrastructure/` y no puede nombrar `ArxivUnavailable`/
        `ArxivFeedError`. No captura `asyncio.CancelledError` (hereda de
        `BaseException`, fuera del alcance de `except Exception`).
        """
        try:
            result = await self._ingest()
        except Exception as exc:
            self._ingest_status = "error"
            self._ingest_error = type(exc).__name__
            self._degrade(RunStatus.PARTIAL, "ingest_error")
            _logger.exception(
                "night.ingest",
                extra={
                    "event": "night.ingest",
                    "run_id": str(self._run_id),
                    "fetched": 0,
                    "new": 0,
                    "duplicates": 0,
                    "skipped": 0,
                    "truncated": False,
                    "status": "error",
                    "error": self._ingest_error,
                    # str(exc): hoy el texto de `ArxivUnavailable` (código,
                    # intentos, motivo de corte -- ver
                    # `infrastructure/arxiv/client.py::_get`) solo vive en
                    # el traceback de `_logger.exception`, que no es
                    # consultable como campo estructurado. No amplía
                    # `SourceFetch` ni añade contadores de reintento a este
                    # módulo: es solo el texto de la excepción ya
                    # capturada.
                    "error_detail": str(exc),
                },
            )
            return

        self._items_fetched = result.new
        self._ingest_fetched = result.fetched
        _logger.info(
            "night.ingest",
            extra={
                "event": "night.ingest",
                "run_id": str(self._run_id),
                "fetched": result.fetched,
                "new": result.new,
                "duplicates": result.duplicates,
                "skipped": result.skipped,
                "truncated": result.truncated,
                "status": "ok",
                "error": None,
            },
        )

    # --- fase A: Reader ------------------------------------------------

    async def _phase_reader(self) -> list[tuple[Item, Reading]]:
        """Lee, con el Reader, hasta `max_items` ítems `NEW` (orden de
        `ItemRepository.next_unread`). Devuelve los pares `(Item, Reading)`
        de los ítems que sí se leyeron, para la fase B.
        """
        with self._work() as w:
            items = w.items.next_unread(self._max_items)

        read_pairs: list[tuple[Item, Reading]] = []
        processed = ok = failed = 0
        consecutive_bad = 0
        stop_reason: str | None = None

        for item in items:
            processed += 1
            start = monotonic()
            try:
                result = await self._read_item(item)
            except BudgetDenied as exc:
                stop_reason = exc.reason.value
                if exc.reason is DenyReason.OUTSIDE_WINDOW:
                    self._skip_editor = True
                self._degrade(terminal_status_for(exc.reason), exc.reason.value)
                break
            except InvalidTransition:
                _logger.warning(
                    "night.item",
                    extra=self._item_log_fields(
                        item=item,
                        phase="reader",
                        outcome="invalid_transition",
                        attempts=0,
                        tokens_spent=0,
                        duration_ms=_duration_ms(start),
                    ),
                )
                continue
            except Exception:
                _logger.exception(
                    "night.item",
                    extra=self._item_log_fields(
                        item=item,
                        phase="reader",
                        outcome="exception",
                        attempts=0,
                        tokens_spent=0,
                        duration_ms=_duration_ms(start),
                    ),
                )
                failed += 1
                self._items_failed += 1
                consecutive_bad += 1
                if consecutive_bad >= self._max_consecutive_failures:
                    stop_reason = "circuit_breaker"
                    self._degrade(RunStatus.PARTIAL, "circuit_breaker_reader")
                    break
                continue

            duration_ms = _duration_ms(start)
            log_fields = self._item_log_fields(
                item=item,
                phase="reader",
                outcome=result.outcome.value,
                attempts=result.attempts,
                tokens_spent=result.tokens_spent,
                duration_ms=duration_ms,
            )
            if result.outcome is ReadOutcome.READ and result.reading is not None:
                log_fields["interest_score"] = result.reading.interest_score
            _logger.info("night.item", extra=log_fields)

            if result.outcome is ReadOutcome.READ:
                if result.reading is None:
                    # Contrato de `ReadItemResult` (docstring de
                    # `read_item.py`): `READ` siempre trae una `Reading`.
                    # Violarlo es un bug de `ReadItem`, nunca un desenlace
                    # esperable de una noche -- se deja escapar sin
                    # traducir, igual que `cli.py::_ReadItemContractViolated`,
                    # para que `cli.py` lo cierre `FAILED` en vez de
                    # enmascararlo como un ítem fallido más.
                    raise _ReadItemContractViolated(
                        "ReadItem devolvió ReadOutcome.READ sin Reading"
                    )
                ok += 1
                consecutive_bad = 0
                self._items_read += 1
                read_pairs.append((item, result.reading))
                continue

            failed += 1
            self._items_failed += 1
            if result.outcome is ReadOutcome.RATE_LIMITED:
                stop_reason = "rate_limited"
                self._skip_editor = True
                self._degrade(RunStatus.PARTIAL, "rate_limited_reader")
                break

            consecutive_bad += 1
            if consecutive_bad >= self._max_consecutive_failures:
                stop_reason = "circuit_breaker"
                self._degrade(RunStatus.PARTIAL, "circuit_breaker_reader")
                break

        _logger.info(
            "night.phase_end",
            extra={
                "event": "night.phase_end",
                "run_id": str(self._run_id),
                "phase": "reader",
                "processed": processed,
                "ok": ok,
                "failed": failed,
                "stop_reason": stop_reason,
            },
        )
        return read_pairs

    # --- fase B: Popularizer ---------------------------------------------

    async def _phase_popularizer(self, read_pairs: list[tuple[Item, Reading]]) -> None:
        """Divulga, con el Popularizer, todos los pares `(Item, Reading)` de
        la fase A, en orden de llegada. El umbral de `interest_score` no se
        comprueba aquí: `PopularizeReading` ya lo aplica antes de cualquier
        `authorize` (ver su docstring), a coste cero.
        """
        processed = ok = failed = 0
        consecutive_bad = 0
        stop_reason: str | None = None

        for item, reading in read_pairs:
            processed += 1
            start = monotonic()
            try:
                result = await self._popularize(item=item, reading=reading)
            except BudgetDenied as exc:
                stop_reason = exc.reason.value
                if exc.reason is DenyReason.OUTSIDE_WINDOW:
                    self._skip_editor = True
                self._degrade(terminal_status_for(exc.reason), exc.reason.value)
                break
            except InvalidTransition:
                _logger.warning(
                    "night.item",
                    extra=self._item_log_fields(
                        item=item,
                        phase="popularizer",
                        outcome="invalid_transition",
                        attempts=0,
                        tokens_spent=0,
                        duration_ms=_duration_ms(start),
                    ),
                )
                continue
            except Exception:
                _logger.exception(
                    "night.item",
                    extra=self._item_log_fields(
                        item=item,
                        phase="popularizer",
                        outcome="exception",
                        attempts=0,
                        tokens_spent=0,
                        duration_ms=_duration_ms(start),
                    ),
                )
                failed += 1
                consecutive_bad += 1
                if consecutive_bad >= self._max_consecutive_failures:
                    stop_reason = "circuit_breaker"
                    self._degrade(RunStatus.PARTIAL, "circuit_breaker_popularizer")
                    break
                continue

            duration_ms = _duration_ms(start)
            log_fields = self._item_log_fields(
                item=item,
                phase="popularizer",
                outcome=result.outcome.value,
                attempts=result.attempts,
                tokens_spent=result.tokens_spent,
                duration_ms=duration_ms,
            )
            if result.outcome is PopularizeOutcome.POPULARIZED and result.finding is not None:
                log_fields["finding_id"] = str(result.finding.id)
            _logger.info("night.item", extra=log_fields)

            if result.outcome is PopularizeOutcome.POPULARIZED:
                ok += 1
                consecutive_bad = 0
                continue

            if result.outcome is PopularizeOutcome.SKIPPED_LOW_SCORE:
                # Desenlace de coste cero: `PopularizeReading` aplica el
                # umbral de `interest_score` antes de cualquier
                # `authorize` (ver su docstring), así que descartar por
                # umbral no implica ninguna llamada al proveedor y no es
                # evidencia de que esté sano. Neutro -- ni éxito ni fallo,
                # no toca `consecutive_bad` en ningún sentido -- mismo
                # tratamiento que `InvalidTransition` más arriba (revisión
                # de T44, punto 4): sin este trato, una racha de `score`
                # bajos intercalada con fallos de verdad reinicia el
                # cortacircuitos gratis y deja correr muchas más llamadas
                # sin contabilidad de las que fija
                # `max_consecutive_failures`.
                continue

            failed += 1
            if result.outcome is PopularizeOutcome.RATE_LIMITED:
                stop_reason = "rate_limited"
                self._skip_editor = True
                self._degrade(RunStatus.PARTIAL, "rate_limited_popularizer")
                break

            consecutive_bad += 1
            if consecutive_bad >= self._max_consecutive_failures:
                stop_reason = "circuit_breaker"
                self._degrade(RunStatus.PARTIAL, "circuit_breaker_popularizer")
                break

        _logger.info(
            "night.phase_end",
            extra={
                "event": "night.phase_end",
                "run_id": str(self._run_id),
                "phase": "popularizer",
                "processed": processed,
                "ok": ok,
                "failed": failed,
                "stop_reason": stop_reason,
            },
        )

    # --- fase C: Editor -----------------------------------------------------

    async def _phase_editor(self) -> None:
        """Llama al Editor exactamente una vez. El tope de "una vez por
        noche" es del guard (`max_editor_calls_per_night`), no de este
        método: aquí solo hay una llamada porque `_run_body` invoca este
        método como máximo una vez.
        """
        try:
            edit_result = await self._edit_night(run_id=self._run_id)
        except BudgetDenied as exc:
            if exc.reason is DenyReason.EDITOR_ALREADY_CALLED:
                # No pasa por `terminal_status_for` (lanzaría `ValueError` a
                # propósito, ver su docstring): "el Editor ya agotó sus
                # intentos" es un fin de noche normal, no una anomalía que
                # cierre la noche por esa función -- mismo patrón que
                # `cli.py::_edit_one_night`.
                self._degrade(RunStatus.PARTIAL, "editor_already_called")
            else:
                self._degrade(terminal_status_for(exc.reason), exc.reason.value)
            _logger.warning(
                "night.editor",
                extra={
                    "event": "night.editor",
                    "run_id": str(self._run_id),
                    "candidates": 0,
                    "published": 0,
                    "discarded": 0,
                    "attempts": 0,
                    "tokens_spent": 0,
                    "unknown_item_ids": [],
                    "deny_reason": exc.reason.value,
                },
            )
            return

        self._candidates = edit_result.candidates
        self._findings_published = len(edit_result.published)
        _logger.info(
            "night.editor",
            extra={
                "event": "night.editor",
                "run_id": str(self._run_id),
                "candidates": edit_result.candidates,
                "published": len(edit_result.published),
                "discarded": len(edit_result.discarded),
                "attempts": edit_result.attempts,
                "tokens_spent": edit_result.tokens_spent,
                "unknown_item_ids": list(edit_result.unknown_item_ids),
            },
        )

        if edit_result.outcome in (EditOutcome.EDITED, EditOutcome.NO_CANDIDATES):
            if not self._end_reason:
                self._end_reason = edit_result.outcome.value
            return

        self._degrade(RunStatus.PARTIAL, f"editor_{edit_result.outcome.value}")

    # --- helpers ---------------------------------------------------------

    def _degrade(self, status: RunStatus, reason: str) -> None:
        """Sustituye `self._status`/`self._end_reason` solo si `status` es
        más severo que el actual (ver docstring del módulo, "modelo de
        degradación monótona")."""
        if _STATUS_RANK[status] > _STATUS_RANK[self._status]:
            self._status = status
            self._end_reason = reason

    def _item_log_fields(
        self,
        *,
        item: Item,
        phase: str,
        outcome: str,
        attempts: int,
        tokens_spent: int,
        duration_ms: int,
    ) -> dict[str, object]:
        return {
            "event": "night.item",
            "run_id": str(self._run_id),
            "item_id": str(item.id),
            "external_id": item.external_id,
            "phase": phase,
            "outcome": outcome,
            "attempts": attempts,
            "tokens_spent": tokens_spent,
            "duration_ms": duration_ms,
        }

    def _build_notes(self, tokens_used: int) -> str:
        """`notes` estable y apta para grep (ver docstring del módulo /
        plan de esta tarea para el formato exacto)."""
        ingest_part = f"ingest={self._ingest_status}"
        if self._ingest_error is not None:
            ingest_part += f":{self._ingest_error}"
        ingest_part += f" fetched={self._ingest_fetched} new={self._items_fetched}"
        read_part = f"read={self._items_read}(failed={self._items_failed})"
        editor_part = f"candidates={self._candidates} published={self._findings_published}"
        tokens_part = f"tokens={tokens_used}"
        end_part = f"end={self._end_reason or 'unknown'}"
        return f"{ingest_part} | {read_part} | {editor_part} | {tokens_part} | {end_part}"

    def _log_night_end(self, *, tokens_used: int, elapsed_s: float, notes: str) -> None:
        _logger.info(
            "night.end",
            extra={
                "event": "night.end",
                "run_id": str(self._run_id),
                "status": self._status.value,
                "tokens_used": tokens_used,
                "items_fetched": self._items_fetched,
                "items_read": self._items_read,
                "items_failed": self._items_failed,
                "candidates": self._candidates,
                "findings_published": self._findings_published,
                "elapsed_s": round(elapsed_s, 3),
                "notes": notes,
            },
        )

    def _result(self, *, tokens_used: int, notes: str) -> RunNightResult:
        return RunNightResult(
            status=self._status,
            notes=notes,
            items_fetched=self._items_fetched,
            items_read=self._items_read,
            items_failed=self._items_failed,
            candidates=self._candidates,
            findings_published=self._findings_published,
            tokens_used=tokens_used,
        )
