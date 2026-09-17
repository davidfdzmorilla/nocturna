"""Control de gasto: `BudgetGuard`, la pieza que `CLAUDE.md` llama "no negociable".

Ningún camino de código llama a `LLMProvider.run_agent` sin pasar antes por
`BudgetGuard.check`/`authorize`. El objetivo no es optimizar el gasto, es
impedir que una noche mala deje al autor sin su suscripción Claude Max
durante el resto de la semana (`CLAUDE.md`, "Control de gasto").

Reglas de este módulo, cada una explicada donde se aplica en el código:

1. El guard no tiene estado mutable: cada `check` relee `RunRepository` y
   `AgentCallRepository`. Dos instancias contra la misma base deciden lo
   mismo, y el acumulado sobrevive a un reinicio del proceso.
2. El acumulado de gasto sale siempre de
   `AgentCallRepository.tokens_used_for_run`, nunca de `Run.tokens_used`
   (que `ARCHITECTURE.md` y ADR 0003 documentan como caché desnormalizada,
   escrita tal cual por `RunRepository.save` sin recalcular ni validar).
3. Orden de comprobación en `check`: `Run.status == RUNNING`, luego la
   ventana horaria, luego el tope de llamadas, luego el presupuesto. La
   ventana va antes que el presupuesto porque el motivo de denegación
   determina si T44 cierra la noche como `killed` o como `partial`
   (`terminal_status_for`), y esos dos casos no pueden confundirse.
4. La reserva del Editor se resta del presupuesto disponible para Reader y
   Popularizer desde la primera llamada de la noche, no "cuando se acerque
   el final".
5. `estimated_tokens <= 0` lanza `ValueError` de inmediato: aceptar `0`
   convertiría el guard en un pasapuertas hasta agotar el límite exacto.
6. `timeout_for_call` nunca deja sobrevivir una llamada al corte de
   `hard_stop`.
7. `is_within_window` es pura, no depende del guard, y cubre tanto la
   ventana que no cruza medianoche (la real de fase 1, ver `window` en
   `pipeline.toml`) como la que sí cruza, sin asumir cuál es la configurada.
8. `terminal_status_for` traduce el motivo de denegación al estado terminal
   del Run que le corresponde, salvo `EDITOR_ALREADY_CALLED`, que no cierra
   la noche.
9. El tope de llamadas por rol es un cortacircuitos independiente de los
   tokens: rompe un bucle aunque las estimaciones de coste sean mínimas.
10. `record_call` es el único camino de contabilización, y no abre ni
    cierra transacción: `unit_of_work` (ADR 0003) es la única frontera.
11. `BudgetDenied` no hereda de `DomainError`, para que un `except
    DomainError` amplio en los casos de uso de agentes (T41–T44) no pueda
    tragarse silenciosamente una denegación de presupuesto.
12. Ninguna hora sale del reloj del sistema leído directamente: siempre del
    `Clock` inyectado. Ninguna constante de gasto está escrita a mano en
    este módulo: todo viene de `BudgetPolicy` (traducida desde
    `pipeline.toml`) o de la base de datos.
13. Este módulo no importa `infrastructure/`. `BudgetPolicy` es una
    `dataclass` de la biblioteca estándar, no Pydantic, precisamente por
    eso: la validación del TOML ya ocurrió en `infrastructure/config.py`
    antes de llegar aquí; traducir `PipelineConfig` a `BudgetPolicy` es
    responsabilidad de `cli.py` (el composition root), no de este módulo.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from enum import StrEnum
from uuid import UUID

from nocturna.domain.clock import Clock
from nocturna.domain.entities import AgentCall, Run, RunStatus
from nocturna.domain.errors import InvariantViolation, RunAlreadyFinished
from nocturna.domain.llm import AgentRole
from nocturna.domain.repositories import AgentCallRepository, RunRepository


@dataclass(frozen=True, slots=True)
class BudgetPolicy:
    """Política de gasto de una noche, ya traducida desde `pipeline.toml`.

    `dataclass` de la biblioteca estándar a propósito (regla 13 del
    docstring del módulo): la validación de tipos y rangos ya la hizo
    Pydantic en `infrastructure/config.py` (`BudgetConfig`, `LimitsConfig`,
    `WindowConfig`). `pydantic` está vetado en `application/` por
    `FORBIDDEN_APPLICATION_MODULES` (`test_domain_purity.py`), pero esa
    prohibición no es la razón de usar `dataclass` aquí: la razón real es no
    repetir una validación que ya ocurrió una vez, en
    `infrastructure/config.py`, contra el TOML tal cual llega del disco;
    `BudgetPolicy` recibe valores que ya pasaron ese filtro (la traducción
    la hace `cli.py`, el composition root), así que revalidarlos con un
    segundo `BaseModel` sería trabajo redundante aunque `pydantic` fuera
    importable desde aquí.

    `weekly_reset_weekday` sigue la convención de `datetime.weekday()`
    (lunes=0 ... domingo=6): la traducción desde el nombre de día en texto
    de `pipeline.toml` (`"monday"`, ...) es cosa de quien construye esta
    política, no de este módulo.
    """

    nightly_tokens: int
    editor_reserve_tokens: int
    max_items_per_night: int
    max_turns_per_agent: int
    max_editor_calls_per_night: int
    max_calls_per_item: int
    item_timeout_s: int
    editor_timeout_s: int
    run_timeout_s: int
    window_start: time
    window_hard_stop: time
    weekly_reset_weekday: int
    weekly_reset_hour: int
    reset_day_multiplier: float


class DenyReason(StrEnum):
    """Motivo de una denegación de `BudgetGuard`.

    Se usa tanto para construir `BudgetDecision` como para elegir la
    excepción concreta que lanza `authorize` y el estado terminal que le
    corresponde al Run (`terminal_status_for`). Nunca se distingue por
    texto de mensaje, solo por este enum.
    """

    OUTSIDE_WINDOW = "outside_window"
    BUDGET_EXHAUSTED = "budget_exhausted"
    CALL_LIMIT_REACHED = "call_limit_reached"
    EDITOR_ALREADY_CALLED = "editor_already_called"
    RUN_NOT_RUNNING = "run_not_running"


@dataclass(frozen=True, slots=True)
class BudgetDecision:
    """Resultado de `BudgetGuard.check`: qué se decidió y por qué.

    `reason` es obligatorio si y solo si `allowed` es `False`; el
    `__post_init__` lo exige para que una denegación nunca se propague sin
    motivo (perdiendo la distinción que necesita `terminal_status_for`) ni
    una autorización lleve un motivo que no pintaría nada.
    """

    allowed: bool
    reason: DenyReason | None
    remaining_tokens: int

    def __post_init__(self) -> None:
        if self.allowed and self.reason is not None:
            raise ValueError("una decisión permitida no debe llevar 'reason'")
        if not self.allowed and self.reason is None:
            raise ValueError("una decisión denegada debe llevar 'reason'")

    def __bool__(self) -> bool:
        return self.allowed


class BudgetDenied(Exception):
    """Raíz de las denegaciones de `BudgetGuard.authorize`.

    Deliberadamente **no hereda de `DomainError`** (`domain/errors.py`): un
    `except DomainError` amplio en un caso de uso de agente (T41–T43) o en
    el orquestador (T44) no debe poder tragarse una denegación de
    presupuesto junto con un error de negocio genérico. Quien quiera
    capturar denegaciones de gasto tiene que nombrarlas explícitamente.
    """

    def __init__(self, reason: DenyReason, role: AgentRole, remaining_tokens: int) -> None:
        super().__init__(
            f"BudgetGuard denegó la llamada a '{role.value}': {reason.value} "
            f"(remaining_tokens={remaining_tokens})"
        )
        self.reason = reason
        self.role = role
        self.remaining_tokens = remaining_tokens


class OutsideExecutionWindow(BudgetDenied):
    """La hora actual (según el `Clock` inyectado) está fuera de la ventana de ejecución."""

    def __init__(self, role: AgentRole, remaining_tokens: int) -> None:
        super().__init__(DenyReason.OUTSIDE_WINDOW, role, remaining_tokens)


class BudgetExceeded(BudgetDenied):
    """El presupuesto disponible para el rol (con la reserva del Editor ya restada) no alcanza."""

    def __init__(self, role: AgentRole, remaining_tokens: int) -> None:
        super().__init__(DenyReason.BUDGET_EXHAUSTED, role, remaining_tokens)


class CallLimitReached(BudgetDenied):
    """Se alcanzó el tope de llamadas de Reader/Popularizer de esta noche."""

    def __init__(self, role: AgentRole, remaining_tokens: int) -> None:
        super().__init__(DenyReason.CALL_LIMIT_REACHED, role, remaining_tokens)


class EditorAlreadyCalled(BudgetDenied):
    """El Editor agotó sus intentos permitidos esta noche (`max_editor_calls_per_night`).

    No es un fallo del pipeline: es "ya está hecho" (`terminal_status_for`
    no la traduce a un cierre de Run). `max_editor_calls_per_night` cuenta
    intentos, no aciertos (ver `AgentCallRepository.count_for_run`), así
    que este motivo también cubre el caso "el reintento por JSON inválido
    ya se gastó".
    """

    def __init__(self, role: AgentRole, remaining_tokens: int) -> None:
        super().__init__(DenyReason.EDITOR_ALREADY_CALLED, role, remaining_tokens)


class RunNotRunning(BudgetDenied):
    """No hay un `Run` en estado `RUNNING` con el `run_id` de este guard."""

    def __init__(self, role: AgentRole, remaining_tokens: int) -> None:
        super().__init__(DenyReason.RUN_NOT_RUNNING, role, remaining_tokens)


_EXCEPTION_BY_REASON: dict[DenyReason, type[BudgetDenied]] = {
    DenyReason.OUTSIDE_WINDOW: OutsideExecutionWindow,
    DenyReason.BUDGET_EXHAUSTED: BudgetExceeded,
    DenyReason.CALL_LIMIT_REACHED: CallLimitReached,
    DenyReason.EDITOR_ALREADY_CALLED: EditorAlreadyCalled,
    DenyReason.RUN_NOT_RUNNING: RunNotRunning,
}


def is_within_window(now: datetime, start: time, hard_stop: time) -> bool:
    """¿Cae `now` dentro de `[start, hard_stop)`?

    Pura: no lee el reloj ni ningún repositorio. Cubre los dos casos
    posibles sin asumir cuál está configurado:

    - **Ventana normal** (`start <= hard_stop`, el caso real de fase 1,
      ver `window` en `pipeline.toml`): dentro si `start <= t < hard_stop`.
    - **Ventana que cruza medianoche** (`start > hard_stop`, p. ej.
      22:00–02:00): dentro si `t >= start` (tramo de la noche anterior a
      medianoche) o `t < hard_stop` (tramo de después de medianoche).

    Cerrado por abajo, abierto por arriba en ambos casos: a `hard_stop`
    exacto, `t < hard_stop` es `False`, así que la ventana ya está cerrada.
    """
    t = now.time()
    if start <= hard_stop:
        return start <= t < hard_stop
    return t >= start or t < hard_stop


def seconds_until_hard_stop(now: datetime, start: time, hard_stop: time) -> int:
    """Segundos hasta `hard_stop`, nunca negativo.

    Pura, como `is_within_window`: no lee el reloj ni ningún repositorio.
    Única implementación de este cálculo del proyecto —
    `BudgetGuard.seconds_until_hard_stop` y `cli.py::_print_budget_plan` la
    llaman a ella, ninguno de los dos tiene su propia copia—, precisamente
    porque las dos copias que existían antes de esta corrección compartían
    el mismo bug (ver el párrafo siguiente): mantener dos implementaciones
    separadas es la forma más segura de que una corrección arregle una y
    deje la otra rota.

    Si `now` ya está fuera de la ventana (`is_within_window` es `False`),
    el resultado es `0` directamente, sin "adelantar" al `hard_stop` de la
    noche siguiente: esta función describe cuánto le queda a la ejecución
    en curso, no cuándo será el próximo corte.

    La resta final se hace **convertida a UTC en ambos operandos**, nunca
    en la hora de pared de `now.tzinfo`. `hard_stop_dt` se construye con
    `datetime.combine(..., tzinfo=now.tzinfo)`, así que comparte
    literalmente el mismo objeto `tzinfo` que `now`; cuando Python resta
    dos `datetime` con el mismo `tzinfo`, lo hace en hora de pared y no
    corrige por `utcoffset()`, así que una noche de cambio de hora entre
    `now` y `hard_stop_dt` desplazaría el resultado una hora entera (visto
    en revisión: +3600 s el domingo de adelanto de hora, -3600 s el de
    atraso). `.astimezone(UTC)` en los dos operandos antes de restar hace
    que la resta sea sobre instantes absolutos, inmune a cualquier cambio
    de `utcoffset()` entre medias.
    """
    if not is_within_window(now, start, hard_stop):
        return 0

    if start <= hard_stop:
        hard_stop_dt = datetime.combine(now.date(), hard_stop, tzinfo=now.tzinfo)
    elif now.time() >= start:
        # Tramo de la ventana anterior a medianoche: el hard_stop cae al día
        # siguiente.
        hard_stop_dt = datetime.combine(
            now.date() + timedelta(days=1), hard_stop, tzinfo=now.tzinfo
        )
    else:
        # Tramo de la ventana posterior a medianoche: el hard_stop cae el
        # mismo día que `now`.
        hard_stop_dt = datetime.combine(now.date(), hard_stop, tzinfo=now.tzinfo)

    remaining = (hard_stop_dt.astimezone(UTC) - now.astimezone(UTC)).total_seconds()
    return max(int(remaining), 0)


def terminal_status_for(reason: DenyReason) -> RunStatus:
    """Estado terminal del Run que le corresponde a una denegación de `BudgetGuard`.

    - `OUTSIDE_WINDOW` → `KILLED`: se cruzó `hard_stop`, `CLAUDE.md` exige
      un corte incondicional, no una noche "parcial".
    - `BUDGET_EXHAUSTED`, `CALL_LIMIT_REACHED`, `RUN_NOT_RUNNING` →
      `PARTIAL`: la noche se queda corta pero no por haber pasado de hora.
    - `EDITOR_ALREADY_CALLED` **no cierra la noche**: significa "el Editor
      ya agotó sus intentos", que es un estado normal de fin de flujo, no
      una anomalía que deba forzar el cierre del Run desde aquí. Llamar a
      esta función con ese motivo es un error de quien orquesta (T44), que
      debe cerrar el Run por su propio criterio (completed/partial) en ese
      caso, no a través de esta traducción.
    """
    if reason is DenyReason.OUTSIDE_WINDOW:
        return RunStatus.KILLED
    if reason is DenyReason.EDITOR_ALREADY_CALLED:
        raise ValueError(
            "'EDITOR_ALREADY_CALLED' no cierra la noche; no tiene un RunStatus terminal asociado"
        )
    return RunStatus.PARTIAL


def effective_nightly_tokens(policy: BudgetPolicy, now: datetime) -> int:
    """Presupuesto nocturno efectivo para la noche que arranca en `now`.

    Aplica `reset_day_multiplier` únicamente la noche del reinicio semanal
    de la suscripción: cuando el día de la semana de `now` coincide con
    `policy.weekly_reset_weekday` (convención `datetime.weekday()`) y ya se
    ha alcanzado `policy.weekly_reset_hour` ese mismo día. El resto de
    noches usa `policy.nightly_tokens` sin modificar.

    Pensada para que la use quien crea el `Run` (T44) al calcular
    `Run.budget_tokens`, que `ARCHITECTURE.md` documenta como "presupuesto
    efectivo, ya con el multiplicador del día": el multiplicador se aplica
    una sola vez, aquí, no en cada comprobación de `BudgetGuard`.
    """
    if now.weekday() == policy.weekly_reset_weekday and now.hour >= policy.weekly_reset_hour:
        return int(policy.nightly_tokens * policy.reset_day_multiplier)
    return policy.nightly_tokens


class BudgetGuard:
    """Guarda de gasto: la única puerta hacia `LLMProvider.run_agent`.

    Sin caché ni contador en memoria (regla 1 del docstring del módulo):
    cada método relee `RunRepository`/`AgentCallRepository`. Es deliberado
    incluso a costa de un `SELECT` por llamada — la alternativa (memoizar
    el gasto) es exactamente el camino por el que un contador desincronizado
    autorizaría gasto que la base de datos no respalda, y es lo que hace
    posible que el acumulado sobreviva a un reinicio del proceso a media
    noche.
    """

    def __init__(
        self,
        *,
        run_id: UUID,
        policy: BudgetPolicy,
        runs: RunRepository,
        agent_calls: AgentCallRepository,
        clock: Clock,
    ) -> None:
        self._run_id = run_id
        self._policy = policy
        self._runs = runs
        self._agent_calls = agent_calls
        self._clock = clock

    def check(self, role: AgentRole, estimated_tokens: int) -> BudgetDecision:
        """Decide si se puede llamar a `role` gastando `estimated_tokens`, sin lanzar.

        Orden de comprobación (regla 3): `Run.status == RUNNING`, ventana
        horaria, tope de llamadas, presupuesto. La ventana va antes que el
        presupuesto porque `terminal_status_for` necesita distinguir sin
        ambigüedad "se cruzó `hard_stop`" (`killed`) de "se acabó el
        presupuesto" (`partial`).
        """
        if estimated_tokens <= 0:
            # Regla 5: `0` autorizaría "gratis" hasta el límite exacto sin
            # que ninguna resta lo notara. Se rechaza antes de tocar la
            # base de datos.
            raise ValueError(
                f"estimated_tokens debe ser mayor que cero, recibido {estimated_tokens}"
            )

        run = self._runs.get(self._run_id)
        if run is None or run.status is not RunStatus.RUNNING:
            return BudgetDecision(
                allowed=False, reason=DenyReason.RUN_NOT_RUNNING, remaining_tokens=0
            )

        if not is_within_window(
            self._clock.now(), self._policy.window_start, self._policy.window_hard_stop
        ):
            return BudgetDecision(
                allowed=False,
                reason=DenyReason.OUTSIDE_WINDOW,
                remaining_tokens=self._remaining(run, role),
            )

        call_limit_reason = self._call_limit_reason(role)
        if call_limit_reason is not None:
            return BudgetDecision(
                allowed=False, reason=call_limit_reason, remaining_tokens=self._remaining(run, role)
            )

        # Regla 2: el gasto acumulado sale de `tokens_used_for_run`, nunca
        # de `run.tokens_used` (caché desnormalizada, ver ADR 0003).
        available = self._available_tokens(run, role)
        spent = self._agent_calls.tokens_used_for_run(self._run_id)
        remaining = max(available - spent, 0)

        # Regla 4: con `remaining == 0` (spent >= available) se deniega
        # aunque `estimated_tokens` fuera absurdamente pequeño; por eso la
        # primera condición (`spent < available`) es necesaria además de la
        # segunda.
        if spent < available and spent + estimated_tokens <= available:
            return BudgetDecision(allowed=True, reason=None, remaining_tokens=remaining)
        return BudgetDecision(
            allowed=False, reason=DenyReason.BUDGET_EXHAUSTED, remaining_tokens=remaining
        )

    def authorize(self, role: AgentRole, estimated_tokens: int) -> None:
        """Como `check`, pero lanza la excepción correspondiente si se deniega."""
        decision = self.check(role, estimated_tokens)
        if decision:
            return
        assert decision.reason is not None  # garantizado por BudgetDecision.__post_init__
        raise _EXCEPTION_BY_REASON[decision.reason](role, decision.remaining_tokens)

    def record_call(self, call: AgentCall, run: Run) -> None:
        """Único camino para contabilizar una llamada ya hecha.

        Recibe `run` por argumento en vez de releerlo (regla 10): si este
        método volviera a pedir el Run al repositorio, coexistirían dos
        instancias de la misma fila en la misma unidad de trabajo, y la
        segunda pisaría el `tokens_used` que acaba de incrementar la
        primera al guardarse. No abre ni cierra transacción: `unit_of_work`
        (`infrastructure/db/session.py`, ADR 0003) es la única frontera
        transaccional del proyecto.

        Dos comprobaciones/decisiones añadidas en la revisión de T30, en
        este orden:

        1. **`call`/`run` deben pertenecer al Run que vigila este guard.**
           `Run.record_agent_call` ya valida `call.run_id == run.id`, pero
           eso no impide que alguien pase el `run` equivocado: un guard
           construido para `run_id=A` llamado con `record_call(call_de_C,
           run_C)` pasaba antes esa validación (`call_de_C.run_id ==
           run_C.id`) sin que nada comprobara que `run_C` es el Run que
           `self._run_id` vigila, y el gasto de C se contabilizaba contra
           C aunque este guard sea el de A. Se rechaza con
           `InvariantViolation` (excepción de dominio, nunca
           `AssertionError`) antes de tocar ningún repositorio.
        2. **Los tokens ya gastados nunca se pierden por culpa de
           `RunAlreadyFinished` en concreto, aunque el Run ya esté
           cerrado.** `call` representa una llamada al LLM que ya ocurrió:
           la suscripción ya pagó esos tokens en el momento en que se
           invoca este método, sin importar lo que pase después. Si entre
           que se hizo la llamada y que se registra el Run pasó a un
           estado terminal (el caso real: T44 cierra el Run como `killed`
           al cruzar `hard_stop` mientras esta llamada seguía en vuelo),
           `run.record_agent_call` lanza `RunAlreadyFinished`. Antes de
           esta corrección esa excepción se propagaba sin capturar, y
           como `unit_of_work` hace rollback ante cualquier excepción, el
           `agent_calls.add(call)` de la línea anterior —ya ejecutado—
           se deshacía con ella: los tokens realmente gastados
           desaparecían sin dejar rastro ni en `AgentCall` ni en
           `Run.tokens_used`. Ahora se captura `RunAlreadyFinished` y no se
           relanza: el `AgentCall` ya añadido a la sesión es la fuente de
           verdad del gasto (regla 2 del docstring del módulo, que ya decía
           que el acumulado sale de `AgentCallRepository`, nunca de
           `Run.tokens_used`).

           Esto **no** protege contra un rollback provocado por cualquier
           otra causa dentro de la misma `unit_of_work` —solo se captura
           `RunAlreadyFinished`, ninguna otra excepción—: el `AgentCall`
           sobrevive si y solo si la unidad de trabajo que envuelve esta
           llamada llega a hacer `commit`. El camino de cancelación de las
           04:45 (`hard_stop`) es el candidato natural a lanzar algo
           distinto de `RunAlreadyFinished` a mitad de un `record_call` (p.
           ej. al cerrar la sesión o el Run de otra forma), y en ese caso
           el rollback sí se lleva por delante este `AgentCall` como
           cualquier otra escritura de la transacción. T44 hereda de aquí
           una obligación concreta: contabilizar la llamada que estaba en
           vuelo en el momento del corte en su **propia** `unit_of_work`,
           separada de la que usa el camino de cancelación, para que un
           fallo de esta última no se lleve por delante el registro de esa
           llamada. `Run.tokens_used` se queda un incremento por detrás en
           el caso concreto de `RunAlreadyFinished`, pero eso no afecta a
           ninguna decisión de `BudgetGuard`, que nunca lee ese campo
           (regla 2). Si en el futuro algo sí llegara a depender de
           `Run.tokens_used` como fuente de verdad (no es el caso hoy),
           esta decisión habría que revisarla; queda fuera del alcance de
           T30.
        """
        if run.id != self._run_id or call.run_id != self._run_id:
            raise InvariantViolation(
                "record_call recibió un AgentCall o un Run que no pertenecen al Run "
                f"vigilado por este guard (guard.run_id={self._run_id}, "
                f"run.id={run.id}, call.run_id={call.run_id})"
            )
        self._agent_calls.add(call)
        try:
            run.record_agent_call(call)
        except RunAlreadyFinished:
            return
        self._runs.save(run)

    def spent(self) -> int:
        """Tokens consumidos por el Run hasta ahora, leídos de `agent_calls`."""
        return self._agent_calls.tokens_used_for_run(self._run_id)

    def remaining_for(self, role: AgentRole) -> int:
        """Presupuesto que le queda a `role`, con la reserva del Editor ya aplicada si toca.

        Coherente con `check`: un Run que no está `RUNNING` no tiene
        presupuesto disponible para nadie, así que un Run `KILLED` o
        `PARTIAL` devuelve `0` en vez de un resto "sano" calculado como si
        la noche siguiera en marcha. Antes de esta corrección solo se
        comprobaba `run is None`; un Run existente pero ya cerrado se
        colaba y devolvía el resto de tokens sin gastar, mientras que
        `check` lo deniega con `RUN_NOT_RUNNING` en el mismo caso. Hoy esa
        asimetría es inofensiva porque la puerta real hacia
        `LLMProvider.run_agent` es `authorize`, no este método, pero un
        `if guard.remaining_for(role) > 0:` como atajo (el candidato
        natural en T44) habría sido un falso positivo sobre un Run ya
        cerrado.

        Un `run_id` que no corresponde a ningún Run (`run is None`) sigue
        lanzando `RunNotRunning`, no devolviendo `0`: ese caso es un error
        de configuración del guard (se construyó con el `run_id`
        equivocado), no un estado legítimo de un Run existente, y merece
        fallar de forma ruidosa en vez de reportar silenciosamente "no
        queda presupuesto".
        """
        run = self._runs.get(self._run_id)
        if run is None:
            raise RunNotRunning(role, remaining_tokens=0)
        if run.status is not RunStatus.RUNNING:
            return 0
        return self._remaining(run, role)

    def seconds_until_hard_stop(self) -> int:
        """Segundos hasta `window.hard_stop`, nunca negativo.

        Delegado por entero en la función pura del módulo
        `seconds_until_hard_stop` (regla 12: ninguna hora sale del reloj del
        sistema leído directamente, siempre del `Clock` inyectado — aquí es
        este método quien lee `self._clock.now()` y se lo pasa a la función
        pura). No hay una segunda copia de ese cálculo en esta clase: antes
        de esta corrección la había, y las dos copias (esta y la de
        `cli.py::_print_budget_plan`) compartían el mismo error de resta en
        hora de pared en vez de en UTC; ver el docstring de la función a
        nivel de módulo para el detalle.
        """
        return seconds_until_hard_stop(
            self._clock.now(), self._policy.window_start, self._policy.window_hard_stop
        )

    def timeout_for_call(self, role: AgentRole | None = None) -> int:
        """Timeout a aplicar a la próxima llamada: nunca sobrevive al `hard_stop`.

        Regla 6: `min(timeout_base, seconds_until_hard_stop())`, sin
        excepción -- el `min` con `seconds_until_hard_stop()` se conserva
        tal cual para los tres roles, el Editor incluido. `timeout_base` es
        `editor_timeout_s` si `role is AgentRole.EDITOR`, `item_timeout_s`
        en cualquier otro caso (Reader, Popularizer, o `role=None`):
        `item_timeout_s` está pensado para una llamada sobre un único
        abstract, mientras que el Editor recibe, en una sola llamada a
        Opus, hasta `max_items_per_night` candidatos de la noche entera, y
        no cabe en esos 180 s por defecto. Un `LLMTimeout` no se reintenta
        en `AgentRunner.run` (es uno de sus tres desenlaces terminales), así
        que un timeout corto para el Editor no es "otro intento": es la
        noche entera sin publicar, con la reserva del Editor ya gastada.

        `role` es opcional (por defecto `None`, tratado igual que Reader o
        Popularizer) para que ningún llamador existente de este método
        -- ni de dentro del proyecto ni de la suite de tests
        (`tests/test_budget_guard.py`, `tests/test_budget_window.py`) --
        tenga que pasarlo; el comportamiento para esos roles es idéntico al
        de antes de que existiera `editor_timeout_s`. Treinta segundos antes
        de `hard_stop` son 30 s aunque `timeout_base` sea mayor; en
        `hard_stop` mismo son 0 s, así que quien reciba este valor debe
        tratarlo como "no llames, ya no hay tiempo".
        """
        timeout_base = (
            self._policy.editor_timeout_s
            if role is AgentRole.EDITOR
            else self._policy.item_timeout_s
        )
        return min(timeout_base, self.seconds_until_hard_stop())

    def _available_tokens(self, run: Run, role: AgentRole) -> int:
        """Presupuesto disponible para `role` antes de restar lo ya gastado.

        Regla 4: la reserva del Editor se resta para Reader y Popularizer
        desde el minuto cero de la noche, no solo cuando el presupuesto se
        acerca a agotarse; el Editor ve el presupuesto completo, porque él
        es quien tiene la reserva.
        """
        if role is AgentRole.EDITOR:
            return run.budget_tokens
        return run.budget_tokens - self._policy.editor_reserve_tokens

    def _remaining(self, run: Run, role: AgentRole) -> int:
        available = self._available_tokens(run, role)
        spent = self._agent_calls.tokens_used_for_run(run.id)
        return max(available - spent, 0)

    def _call_limit_reason(self, role: AgentRole) -> DenyReason | None:
        """Tope de llamadas de `role`, contando intentos de cualquier estado (regla 9).

        El Editor usa `max_editor_calls_per_night` (por defecto 2: la
        llamada más el reintento por JSON inválido que concede
        `CLAUDE.md`) y, al alcanzarlo, el motivo es `EDITOR_ALREADY_CALLED`
        — agotar los intentos del Editor es el estado normal de fin de
        noche para ese rol, no una anomalía. Reader y Popularizer usan
        `max_calls_per_item * max_items_per_night` (por defecto 2: una
        llamada más un reintento por ítem, la misma configuración de
        `pipeline.toml` que `max_editor_calls_per_night`, no un "2" escrito
        a mano aquí — regla 12 del docstring del módulo) y, al alcanzarlo,
        el motivo es `CALL_LIMIT_REACHED`: para estos roles sí es un
        cortacircuitos que indica que algo va mal, y `terminal_status_for`
        lo cierra como `partial`.

        `count_for_run` cuenta llamadas de cualquier `status` (ok, error,
        timeout, invalid_output): lo que cuesta presupuesto de la
        suscripción es el intento, no el acierto.
        """
        if role is AgentRole.EDITOR:
            limit = self._policy.max_editor_calls_per_night
            reason = DenyReason.EDITOR_ALREADY_CALLED
        else:
            limit = self._policy.max_calls_per_item * self._policy.max_items_per_night
            reason = DenyReason.CALL_LIMIT_REACHED

        count = self._agent_calls.count_for_run(self._run_id, role)
        return reason if count >= limit else None
