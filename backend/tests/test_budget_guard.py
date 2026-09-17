"""Tests puros de `BudgetGuard.check`/`authorize`/`remaining_for`/`spent`.

Sin base de datos: repositorios falsos en memoria y `FakeClock`. Los
equivalentes contra PostgreSQL real (los cuatro del "Hecho cuando" de T30,
más persistencia y atomicidad) están en `tests/db/test_budget_guard_db.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import UUID, uuid4

import pytest
from fakes.clock import FakeClock

from nocturna.application.budget import (
    BudgetExceeded,
    BudgetGuard,
    BudgetPolicy,
    CallLimitReached,
    DenyReason,
    EditorAlreadyCalled,
    OutsideExecutionWindow,
    RunNotRunning,
)
from nocturna.domain.entities import AgentCall, AgentCallStatus, Run, RunStatus
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.llm import AgentRole

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_JUST_AFTER_HARD_STOP = datetime(2026, 1, 1, 4, 46, 0, tzinfo=UTC)


def _make_run(**overrides: object) -> Run:
    defaults: dict[str, object] = {"started_at": _NOW, "budget_tokens": 300_000}
    defaults.update(overrides)
    return Run(**defaults)


class _UnexpectedRecordingFailure(Exception):
    """Marca un fallo de contabilización distinto de `RunAlreadyFinished`.

    Solo la usa `_RunThatBoomsOnRecord`, para comprobar que
    `BudgetGuard.record_call` no la captura por accidente (ver el docstring
    de `_RunThatBoomsOnRecord`)."""


class _RunThatBoomsOnRecord(Run):
    """Doble de `Run` cuyo `record_agent_call` lanza algo que no es
    `RunAlreadyFinished`.

    Fija el mutante que sobrevivió a la segunda revisión de T30: cambiar,
    en `BudgetGuard.record_call`, `except RunAlreadyFinished` por `except
    Exception` dejaba los 390 tests en verde. Hoy ese `except` amplio no es
    explotable -- la única otra excepción que puede salir de
    `Run.record_agent_call` es `InvariantViolation` por `run_id` ajeno, y
    la comprobación de pertenencia que `record_call` hace justo antes ya la
    hace inalcanzable -- pero es el `except` de la ruta de contabilización
    de gasto, y T44 va a añadir estados al `Run`. Un `except Exception` ahí
    tragaría en silencio un fallo real de contabilización futuro, que es
    exactamente cómo se pierden tokens ya gastados sin que nadie se entere.
    Este doble simula ese fallo futuro sin esperar a que exista de verdad.
    """

    def record_agent_call(self, call: AgentCall) -> None:  # type: ignore[override]
        raise _UnexpectedRecordingFailure("fallo de contabilización que no es RunAlreadyFinished")


def _make_boom_run(**overrides: object) -> _RunThatBoomsOnRecord:
    defaults: dict[str, object] = {"started_at": _NOW, "budget_tokens": 300_000}
    defaults.update(overrides)
    return _RunThatBoomsOnRecord(**defaults)


def _make_agent_call(run_id: UUID, **overrides: object) -> AgentCall:
    defaults: dict[str, object] = {
        "run_id": run_id,
        "item_id": None,
        "agent": AgentRole.READER,
        "model": "claude-sonnet-test",
        "tokens_in": 100,
        "tokens_out": 50,
        "duration_ms": 1_200,
        "status": AgentCallStatus.OK,
    }
    defaults.update(overrides)
    return AgentCall(**defaults)


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 300_000,
        "editor_reserve_tokens": 60_000,
        "max_items_per_night": 40,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 2,
        "item_timeout_s": 180,
        "editor_timeout_s": 300,
        "run_timeout_s": 16_200,
        "window_start": time(0, 0),
        "window_hard_stop": time(4, 45),
        "weekly_reset_weekday": 0,
        "weekly_reset_hour": 0,
        "reset_day_multiplier": 1.0,
    }
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


class _InMemoryRunRepository:
    def __init__(self, run: Run) -> None:
        self._runs: dict[UUID, Run] = {run.id: run}

    def add(self, run: Run) -> None:
        self._runs[run.id] = run

    def get(self, run_id: UUID) -> Run | None:
        return self._runs.get(run_id)

    def current(self) -> Run | None:
        return next((r for r in self._runs.values() if r.status is RunStatus.RUNNING), None)

    def save(self, run: Run) -> None:
        self._runs[run.id] = run

    def remove(self, run_id: UUID) -> None:
        """Solo para tests: simula "no existe ningún Run con este id"."""
        self._runs.pop(run_id, None)


class _InMemoryAgentCallRepository:
    def __init__(self) -> None:
        self.calls: list[AgentCall] = []

    def add(self, call: AgentCall) -> None:
        self.calls.append(call)

    def tokens_used_for_run(self, run_id: UUID) -> int:
        return sum(call.total_tokens for call in self.calls if call.run_id == run_id)

    def count_for_run(self, run_id: UUID, agent: AgentRole) -> int:
        return sum(1 for call in self.calls if call.run_id == run_id and call.agent is agent)


def _make_guard(
    *,
    run: Run,
    runs: _InMemoryRunRepository,
    agent_calls: _InMemoryAgentCallRepository,
    now: datetime,
    policy: BudgetPolicy | None = None,
) -> BudgetGuard:
    return BudgetGuard(
        run_id=run.id,
        policy=policy or _policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(now),
    )


# --- La ventana se evalúa antes que el presupuesto (regla 3) ---------------


def test_fuera_de_ventana_y_sin_presupuesto_el_motivo_es_outside_window_no_budget():
    """Con presupuesto agotado *y* fuera de ventana, gana `OUTSIDE_WINDOW`.

    Importa porque `terminal_status_for` traduce el motivo al cierre del
    `Run` (`killed` frente a `partial`): confundirlos aquí confundiría ese
    cierre en el orquestador (T44).
    """
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    # Presupuesto de Reader completamente agotado (240_000 disponibles).
    agent_calls.add(
        _make_agent_call(run.id, agent=AgentRole.READER, tokens_in=240_000, tokens_out=0)
    )
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_JUST_AFTER_HARD_STOP)

    decision = guard.check(AgentRole.READER, 1)

    assert not decision
    assert decision.reason is DenyReason.OUTSIDE_WINDOW


def test_authorize_fuera_de_ventana_lanza_outside_execution_window_no_budget_exceeded():
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    agent_calls.add(
        _make_agent_call(run.id, agent=AgentRole.READER, tokens_in=240_000, tokens_out=0)
    )
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_JUST_AFTER_HARD_STOP)

    with pytest.raises(OutsideExecutionWindow):
        guard.authorize(AgentRole.READER, 1)


def test_dentro_de_ventana_con_presupuesto_agotado_el_motivo_es_budget_exhausted():
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    agent_calls.add(
        _make_agent_call(run.id, agent=AgentRole.READER, tokens_in=240_000, tokens_out=0)
    )
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)

    decision = guard.check(AgentRole.READER, 1)

    assert not decision
    assert decision.reason is DenyReason.BUDGET_EXHAUSTED


# --- estimated_tokens <= 0 -----------------------------------------------


def test_check_con_estimated_tokens_cero_lanza_value_error():
    run = _make_run()
    guard = _make_guard(
        run=run,
        runs=_InMemoryRunRepository(run),
        agent_calls=_InMemoryAgentCallRepository(),
        now=_WITHIN_WINDOW,
    )

    with pytest.raises(ValueError):
        guard.check(AgentRole.READER, 0)


def test_check_con_estimated_tokens_negativo_lanza_value_error():
    run = _make_run()
    guard = _make_guard(
        run=run,
        runs=_InMemoryRunRepository(run),
        agent_calls=_InMemoryAgentCallRepository(),
        now=_WITHIN_WINDOW,
    )

    with pytest.raises(ValueError):
        guard.check(AgentRole.READER, -1)


def test_authorize_con_estimated_tokens_invalido_tambien_lanza_value_error():
    run = _make_run()
    guard = _make_guard(
        run=run,
        runs=_InMemoryRunRepository(run),
        agent_calls=_InMemoryAgentCallRepository(),
        now=_WITHIN_WINDOW,
    )

    with pytest.raises(ValueError):
        guard.authorize(AgentRole.READER, 0)


# --- remaining_for / spent coherentes con lo que decide check --------------


def test_remaining_for_reader_descuenta_la_reserva_del_editor_desde_el_principio():
    run = _make_run(budget_tokens=300_000)
    guard = _make_guard(
        run=run,
        runs=_InMemoryRunRepository(run),
        agent_calls=_InMemoryAgentCallRepository(),
        now=_WITHIN_WINDOW,
        policy=_policy(editor_reserve_tokens=60_000),
    )

    assert guard.remaining_for(AgentRole.READER) == 240_000
    assert guard.remaining_for(AgentRole.EDITOR) == 300_000


def test_remaining_for_popularizer_descuenta_tambien_la_reserva_del_editor():
    """Mutante detectado en revisión: dar al Popularizer el presupuesto
    completo (como al Editor) hace desaparecer la reserva del Editor para
    ese rol sin que ningún test lo notara. `POPULARIZER` no es `EDITOR`:
    debe ver el mismo presupuesto restringido que `READER`."""
    run = _make_run(budget_tokens=300_000)
    guard = _make_guard(
        run=run,
        runs=_InMemoryRunRepository(run),
        agent_calls=_InMemoryAgentCallRepository(),
        now=_WITHIN_WINDOW,
        policy=_policy(editor_reserve_tokens=60_000),
    )

    assert guard.remaining_for(AgentRole.POPULARIZER) == 240_000
    assert guard.remaining_for(AgentRole.EDITOR) == 300_000


def test_popularizer_budget_exhausted_con_la_reserva_del_editor_intacta():
    """Con el disponible del Popularizer (240_000) agotado, se deniega por
    presupuesto y la reserva del Editor (60_000) sigue entera y disponible
    -- el mismo caso que ya se cubre para el Reader, pero para el rol que
    el mutante del reviewer dejó sin cubrir."""
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    agent_calls.add(
        _make_agent_call(run.id, agent=AgentRole.POPULARIZER, tokens_in=240_000, tokens_out=0)
    )
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)

    decision = guard.check(AgentRole.POPULARIZER, 1)

    assert not decision
    assert decision.reason is DenyReason.BUDGET_EXHAUSTED
    assert guard.remaining_for(AgentRole.EDITOR) == 60_000
    assert guard.check(AgentRole.EDITOR, 60_000)


def test_remaining_for_y_spent_bajan_exactamente_lo_que_registra_record_call():
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)

    assert guard.spent() == 0
    assert guard.remaining_for(AgentRole.READER) == 240_000

    call = _make_agent_call(run.id, agent=AgentRole.READER, tokens_in=1_000, tokens_out=500)
    guard.record_call(call, run)

    assert guard.spent() == 1_500
    assert guard.remaining_for(AgentRole.READER) == 240_000 - 1_500


def test_remaining_for_coincide_con_remaining_tokens_de_una_decision_denegada():
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    agent_calls.add(
        _make_agent_call(run.id, agent=AgentRole.READER, tokens_in=239_500, tokens_out=0)
    )
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)

    decision = guard.check(AgentRole.READER, 1_000)

    assert not decision
    assert decision.remaining_tokens == guard.remaining_for(AgentRole.READER)
    assert decision.remaining_tokens == 500


def test_remaining_for_de_un_run_inexistente_lanza_run_not_running():
    runs = _InMemoryRunRepository(_make_run())
    missing_run_id = uuid4()

    guard = BudgetGuard(
        run_id=missing_run_id,
        policy=_policy(),
        runs=runs,
        agent_calls=_InMemoryAgentCallRepository(),
        clock=FakeClock(_WITHIN_WINDOW),
    )

    with pytest.raises(RunNotRunning):
        guard.remaining_for(AgentRole.READER)


@pytest.mark.parametrize("status", [RunStatus.KILLED, RunStatus.PARTIAL])
def test_remaining_for_de_un_run_no_running_devuelve_cero_igual_que_check(
    status: RunStatus,
) -> None:
    """`remaining_for` debe ser coherente con `check`: un Run `KILLED` o
    `PARTIAL` deniega con `RUN_NOT_RUNNING` en `check` (presupuesto `0`), así
    que `remaining_for` sobre ese mismo Run reporta `0`, no el resto de
    tokens sin gastar calculado como si la noche siguiera en marcha."""
    run = _make_run(budget_tokens=300_000)
    run.finish(status, at=_JUST_AFTER_HARD_STOP)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)

    assert guard.remaining_for(AgentRole.READER) == 0
    assert guard.remaining_for(AgentRole.EDITOR) == 0

    decision = guard.check(AgentRole.READER, 1)
    assert not decision
    assert decision.reason is DenyReason.RUN_NOT_RUNNING
    assert decision.remaining_tokens == guard.remaining_for(AgentRole.READER)


# --- authorize traduce cada DenyReason a su excepción -----------------------


def test_authorize_run_not_running_para_un_run_inexistente():
    run = _make_run()
    runs = _InMemoryRunRepository(run)
    runs.remove(run.id)
    guard = _make_guard(
        run=run, runs=runs, agent_calls=_InMemoryAgentCallRepository(), now=_WITHIN_WINDOW
    )

    with pytest.raises(RunNotRunning):
        guard.authorize(AgentRole.READER, 100)


def test_authorize_budget_exceeded_cuando_se_agota_el_presupuesto():
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    agent_calls.add(
        _make_agent_call(run.id, agent=AgentRole.READER, tokens_in=240_000, tokens_out=0)
    )
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)

    with pytest.raises(BudgetExceeded):
        guard.authorize(AgentRole.READER, 1)


def test_authorize_call_limit_reached_para_reader_tras_dos_intentos_por_item():
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    policy = _policy(max_items_per_night=1)  # tope = 2 * 1 = 2 llamadas
    for _ in range(2):
        agent_calls.add(_make_agent_call(run.id, agent=AgentRole.READER, tokens_in=1, tokens_out=1))
    guard = _make_guard(
        run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW, policy=policy
    )

    with pytest.raises(CallLimitReached):
        guard.authorize(AgentRole.READER, 100)


def test_authorize_editor_already_called_tras_agotar_max_editor_calls_per_night():
    run = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    for _ in range(2):
        agent_calls.add(_make_agent_call(run.id, agent=AgentRole.EDITOR, tokens_in=1, tokens_out=1))
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)

    with pytest.raises(EditorAlreadyCalled):
        guard.authorize(AgentRole.EDITOR, 8_000)


def test_authorize_camino_feliz_no_lanza_nada():
    run = _make_run(budget_tokens=300_000)
    guard = _make_guard(
        run=run,
        runs=_InMemoryRunRepository(run),
        agent_calls=_InMemoryAgentCallRepository(),
        now=_WITHIN_WINDOW,
    )

    guard.authorize(AgentRole.READER, 1_000)  # no debe lanzar


# --- record_call: pertenencia al Run vigilado y llamadas ya gastadas -------


def test_record_call_rechaza_un_agent_call_de_otro_run():
    """Un guard construido para `run_id=A` no puede contabilizar un
    `AgentCall` de `run_id=C`, ni siquiera si `call` y `run` son coherentes
    entre sí: los dos deben pertenecer al Run que vigila *este* guard."""
    run_a = _make_run(budget_tokens=300_000)
    run_c = _make_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run_a)
    runs.add(run_c)
    agent_calls = _InMemoryAgentCallRepository()
    guard = _make_guard(run=run_a, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)

    call_de_c = _make_agent_call(run_c.id, agent=AgentRole.READER, tokens_in=1_000, tokens_out=500)

    with pytest.raises(InvariantViolation):
        guard.record_call(call_de_c, run_c)

    assert agent_calls.calls == []
    assert run_c.tokens_used == 0


def test_record_call_sobre_un_run_ya_cerrado_persiste_el_agent_call_y_el_guard_sigue_bien():
    """Secuencia real de T44 en el corte de las 04:45: una llamada sigue en
    vuelo cuando el Run ya se cerró (`killed`). Los tokens de esa llamada se
    gastaron de verdad, así que `record_call` los persiste en `AgentCall`
    aunque `Run.tokens_used` se quede corto (regla 2 del docstring del
    módulo: el guard nunca lee ese campo). El propio guard, tras esto, debe
    seguir viendo ese gasto real en `spent()`, que también lee de
    `agent_calls` y no del campo desactualizado -- y, coherente con `check`,
    `remaining_for` sobre un Run ya cerrado reporta `0`, no un resto "sano"
    calculado como si la noche siguiera en marcha."""
    run = _make_run(budget_tokens=300_000)
    run.finish(RunStatus.KILLED, at=_JUST_AFTER_HARD_STOP)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_JUST_AFTER_HARD_STOP)

    call = _make_agent_call(run.id, agent=AgentRole.READER, tokens_in=1_000, tokens_out=500)
    guard.record_call(call, run)  # no debe lanzar RunAlreadyFinished

    assert agent_calls.tokens_used_for_run(run.id) == 1_500
    assert run.tokens_used == 0  # el campo se queda corto: no es la fuente de verdad

    # El gasto real sigue viéndose vía `agent_calls`, nunca vía `run.tokens_used`.
    assert guard.spent() == 1_500
    # Pero el Run ya no está RUNNING: remaining_for reporta 0, igual que `check`.
    assert guard.remaining_for(AgentRole.READER) == 0


def test_record_call_propaga_una_excepcion_de_record_agent_call_que_no_es_run_already_finished():
    """Cierra el mutante superviviente: `record_call` solo puede tragarse
    `RunAlreadyFinished`, nunca `Exception` en general.

    El reviewer probó a ensanchar `except RunAlreadyFinished` a `except
    Exception` en `record_call` y los 390 tests de entonces siguieron en
    verde -- ningún test distinguía "el Run ya terminó, pero el gasto ya
    persistido es la fuente de verdad" (el único caso que `record_call`
    debe absorber, regla 2 del docstring del módulo) de "algo se rompió de
    verdad contabilizando el gasto" (que debe propagarse, para que quien
    orquesta se entere y no dé por bueno un gasto que no se registró bien).
    Hoy no hay forma de alcanzar ese segundo caso con el `Run` real -- la
    comprobación de pertenencia de `record_call` hace inalcanzable la única
    otra excepción posible (`InvariantViolation` por `run_id` ajeno) --,
    así que se usa `_RunThatBoomsOnRecord`, un doble cuyo
    `record_agent_call` lanza una excepción distinta, para demostrar que
    `record_call` la deja pasar en vez de tragarla."""
    run = _make_boom_run(budget_tokens=300_000)
    runs = _InMemoryRunRepository(run)
    agent_calls = _InMemoryAgentCallRepository()
    guard = _make_guard(run=run, runs=runs, agent_calls=agent_calls, now=_WITHIN_WINDOW)
    call = _make_agent_call(run.id, agent=AgentRole.READER, tokens_in=1_000, tokens_out=500)

    with pytest.raises(_UnexpectedRecordingFailure):
        guard.record_call(call, run)

    # El AgentCall ya se había añadido antes de que `record_agent_call`
    # lanzara (orden real de `record_call`): eso no es lo que este test
    # fija, pero confirma que la excepción no se levanta demasiado pronto
    # ni sustituye al comportamiento real de la línea anterior.
    assert agent_calls.calls == [call]
