"""Tests puros de la ventana horaria y las funciones auxiliares de `budget.py`.

Sin base de datos: repositorios falsos en memoria y `FakeClock`. Cubre
`is_within_window`, `terminal_status_for`, `effective_nightly_tokens`,
`BudgetDecision.__bool__` y el cálculo de `timeout_for_call`/
`seconds_until_hard_stop`, incluido el cambio de hora en `Europe/Madrid`
— el caso donde un `hard_stop` mal calculado se iría una hora entera.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from fakes.clock import FakeClock

from nocturna.application.budget import (
    BudgetDecision,
    BudgetGuard,
    BudgetPolicy,
    DenyReason,
    effective_nightly_tokens,
    is_within_window,
    seconds_until_hard_stop,
    terminal_status_for,
)
from nocturna.domain.entities import AgentCall, Run, RunStatus
from nocturna.domain.llm import AgentRole

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _make_run(**overrides: object) -> Run:
    defaults: dict[str, object] = {"started_at": _NOW, "budget_tokens": 300_000}
    defaults.update(overrides)
    return Run(**defaults)


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 300_000,
        "editor_reserve_tokens": 60_000,
        "max_items_per_night": 40,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 2,
        "item_timeout_s": 180,
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
    """Doble mínimo de `RunRepository`, sin base de datos."""

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


class _InMemoryAgentCallRepository:
    """Doble mínimo de `AgentCallRepository`, sin base de datos."""

    def __init__(self) -> None:
        self._calls: list[AgentCall] = []

    def add(self, call: AgentCall) -> None:
        self._calls.append(call)

    def tokens_used_for_run(self, run_id: UUID) -> int:
        return sum(call.total_tokens for call in self._calls if call.run_id == run_id)

    def count_for_run(self, run_id: UUID, agent: AgentRole) -> int:
        return sum(1 for call in self._calls if call.run_id == run_id and call.agent is agent)


def _make_guard(*, run: Run, policy: BudgetPolicy, clock: FakeClock) -> BudgetGuard:
    return BudgetGuard(
        run_id=run.id,
        policy=policy,
        runs=_InMemoryRunRepository(run),
        agent_calls=_InMemoryAgentCallRepository(),
        clock=clock,
    )


# --- is_within_window: ventana normal (no cruza medianoche) ----------------


def test_is_within_window_en_start_exacto_esta_dentro():
    assert is_within_window(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC), time(0, 0), time(4, 45))


def test_is_within_window_justo_antes_de_hard_stop_esta_dentro():
    assert is_within_window(datetime(2026, 1, 1, 4, 44, 59, tzinfo=UTC), time(0, 0), time(4, 45))


def test_is_within_window_a_las_04_45_00_exactas_deniega():
    assert not is_within_window(datetime(2026, 1, 1, 4, 45, 0, tzinfo=UTC), time(0, 0), time(4, 45))


def test_is_within_window_justo_despues_de_hard_stop_esta_fuera():
    assert not is_within_window(datetime(2026, 1, 1, 4, 45, 1, tzinfo=UTC), time(0, 0), time(4, 45))


def test_is_within_window_antes_de_start_esta_fuera():
    assert not is_within_window(
        datetime(2026, 1, 1, 23, 59, 59, tzinfo=UTC), time(0, 0), time(4, 45)
    )


# --- is_within_window: ventana que cruza medianoche -------------------------


def test_is_within_window_cruzando_medianoche_tramo_anterior():
    # Ventana 22:00-02:00: las 23:00 caen en el tramo previo a medianoche.
    assert is_within_window(datetime(2026, 1, 1, 23, 0, 0, tzinfo=UTC), time(22, 0), time(2, 0))


def test_is_within_window_cruzando_medianoche_tramo_posterior():
    assert is_within_window(datetime(2026, 1, 2, 1, 0, 0, tzinfo=UTC), time(22, 0), time(2, 0))


def test_is_within_window_cruzando_medianoche_en_start_exacto():
    assert is_within_window(datetime(2026, 1, 1, 22, 0, 0, tzinfo=UTC), time(22, 0), time(2, 0))


def test_is_within_window_cruzando_medianoche_en_hard_stop_exacto_deniega():
    assert not is_within_window(datetime(2026, 1, 2, 2, 0, 0, tzinfo=UTC), time(22, 0), time(2, 0))


def test_is_within_window_cruzando_medianoche_fuera_del_tramo_diurno():
    assert not is_within_window(datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC), time(22, 0), time(2, 0))


# --- BudgetDecision.__bool__ -------------------------------------------------


def test_budget_decision_permitida_es_truthy():
    decision = BudgetDecision(allowed=True, reason=None, remaining_tokens=1_000)
    assert decision
    assert bool(decision) is True


def test_budget_decision_denegada_es_falsy():
    decision = BudgetDecision(allowed=False, reason=DenyReason.BUDGET_EXHAUSTED, remaining_tokens=0)
    assert not decision
    assert bool(decision) is False


def test_budget_decision_permitida_no_admite_reason():
    with pytest.raises(ValueError):
        BudgetDecision(allowed=True, reason=DenyReason.BUDGET_EXHAUSTED, remaining_tokens=0)


def test_budget_decision_denegada_exige_reason():
    with pytest.raises(ValueError):
        BudgetDecision(allowed=False, reason=None, remaining_tokens=0)


# --- terminal_status_for -----------------------------------------------------


def test_terminal_status_for_outside_window_es_killed():
    assert terminal_status_for(DenyReason.OUTSIDE_WINDOW) is RunStatus.KILLED


def test_terminal_status_for_budget_exhausted_es_partial():
    assert terminal_status_for(DenyReason.BUDGET_EXHAUSTED) is RunStatus.PARTIAL


def test_terminal_status_for_call_limit_reached_es_partial():
    assert terminal_status_for(DenyReason.CALL_LIMIT_REACHED) is RunStatus.PARTIAL


def test_terminal_status_for_run_not_running_es_partial():
    assert terminal_status_for(DenyReason.RUN_NOT_RUNNING) is RunStatus.PARTIAL


def test_terminal_status_for_editor_already_called_no_tiene_estado_terminal():
    with pytest.raises(ValueError):
        terminal_status_for(DenyReason.EDITOR_ALREADY_CALLED)


# --- effective_nightly_tokens -------------------------------------------------


def test_effective_nightly_tokens_noche_normal_no_aplica_multiplicador():
    policy = _policy(weekly_reset_weekday=0, weekly_reset_hour=0, reset_day_multiplier=2.0)
    # Un martes: no es el día de reinicio semanal (lunes=0).
    tuesday = datetime(2026, 1, 6, 10, 0, tzinfo=UTC)
    assert tuesday.weekday() == 1

    assert effective_nightly_tokens(policy, tuesday) == policy.nightly_tokens


def test_effective_nightly_tokens_noche_de_reset_aplica_multiplicador():
    policy = _policy(
        nightly_tokens=300_000,
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=2.5,
    )
    monday_after_reset_hour = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    assert monday_after_reset_hour.weekday() == 0

    assert effective_nightly_tokens(policy, monday_after_reset_hour) == int(300_000 * 2.5)


def test_effective_nightly_tokens_dia_de_reset_pero_antes_de_la_hora_no_aplica():
    policy = _policy(weekly_reset_weekday=0, weekly_reset_hour=12, reset_day_multiplier=2.0)
    monday_before_reset_hour = datetime(2026, 1, 5, 3, 0, tzinfo=UTC)
    assert monday_before_reset_hour.weekday() == 0

    assert effective_nightly_tokens(policy, monday_before_reset_hour) == policy.nightly_tokens


# --- timeout_for_call / seconds_until_hard_stop: nunca sobrepasa hard_stop --


def test_timeout_for_call_treinta_segundos_antes_del_hard_stop():
    run = _make_run()
    policy = _policy(item_timeout_s=180)
    clock = FakeClock(datetime(2026, 1, 1, 4, 44, 30, tzinfo=UTC))
    guard = _make_guard(run=run, policy=policy, clock=clock)

    assert guard.timeout_for_call() == 30


def test_timeout_for_call_en_el_hard_stop_exacto_es_cero():
    run = _make_run()
    policy = _policy(item_timeout_s=180)
    clock = FakeClock(datetime(2026, 1, 1, 4, 45, 0, tzinfo=UTC))
    guard = _make_guard(run=run, policy=policy, clock=clock)

    assert guard.timeout_for_call() == 0


def test_timeout_for_call_respeta_item_timeout_s_cuando_sobra_ventana():
    run = _make_run()
    policy = _policy(item_timeout_s=180)
    clock = FakeClock(datetime(2026, 1, 1, 0, 0, 0, tzinfo=UTC))
    guard = _make_guard(run=run, policy=policy, clock=clock)

    assert guard.timeout_for_call() == 180


# --- Corte cerca de hard_stop: no distingue DST (30 s antes es 30 s antes) --
# Estos dos tests solo comprueban el pinzado de `timeout_for_call` cerca del
# `hard_stop`, ambos instantes ya posteriores a la transición de hora: no
# demuestran nada sobre el cálculo de `seconds_until_hard_stop` en sí (ver la
# sección siguiente, que sitúa `now` ANTES de la transición, que es donde la
# diferencia entre hora de pared e instante real aparece).


def test_timeout_for_call_treinta_segundos_antes_del_hard_stop_la_noche_de_marzo():
    madrid = ZoneInfo("Europe/Madrid")
    run = _make_run()
    policy = _policy(item_timeout_s=180)
    clock = FakeClock(datetime(2026, 3, 29, 4, 44, 30, tzinfo=madrid))
    guard = _make_guard(run=run, policy=policy, clock=clock)

    assert guard.timeout_for_call() == 30

    clock.set(datetime(2026, 3, 29, 4, 45, 0, tzinfo=madrid))
    assert guard.timeout_for_call() == 0


def test_timeout_for_call_treinta_segundos_antes_del_hard_stop_la_noche_de_octubre():
    madrid = ZoneInfo("Europe/Madrid")
    run = _make_run()
    policy = _policy(item_timeout_s=180)
    clock = FakeClock(datetime(2026, 10, 25, 4, 44, 30, tzinfo=madrid))
    guard = _make_guard(run=run, policy=policy, clock=clock)

    assert guard.timeout_for_call() == 30

    clock.set(datetime(2026, 10, 25, 4, 45, 0, tzinfo=madrid))
    assert guard.timeout_for_call() == 0


# --- Cambio de hora en Europe/Madrid: seconds_until_hard_stop ---------------
# `now` se sitúa a la 01:30 local de la noche de la transición (antes de que
# ocurra, que en España es entre las 02:00 y las 03:00), que es donde una
# resta en hora de pared en vez de en instantes UTC se desvía: a esa hora
# quedan 3h15m de reloj de pared (11700 s) hasta las 04:45, pero el tiempo
# real hasta ahí es 8100 s la noche que el reloj adelanta (se pierde una
# hora) y 15300 s la noche que el reloj atrasa (se repite una hora). Tabla
# verificada en revisión:
#
#   noche normal      2026-09-16 01:30   antes 11700   real 11700   error   +0
#   adelanto de hora   2026-03-29 01:30   antes 11700   real  8100   error +3600
#   atraso de hora     2026-10-25 01:30   antes 11700   real 15300   error -3600
#
# Los tests de la sección anterior (a las 04:44:30, ya después de la
# transición) no pueden detectar este error: hora de pared e instante real
# vuelven a coincidir en ese tramo por construcción.


def test_seconds_until_hard_stop_funcion_pura_noche_normal_sin_cambio_de_hora():
    madrid = ZoneInfo("Europe/Madrid")
    now = datetime(2026, 9, 16, 1, 30, 0, tzinfo=madrid)

    assert seconds_until_hard_stop(now, time(0, 0), time(4, 45)) == 11_700


def test_seconds_until_hard_stop_funcion_pura_noche_de_marzo_cuando_el_reloj_adelanta():
    madrid = ZoneInfo("Europe/Madrid")
    # 2026-03-29: la madrugada en la que Europe/Madrid salta de 02:00 a 03:00
    # (se pierde una hora real entre las 01:30 y las 04:45).
    now = datetime(2026, 3, 29, 1, 30, 0, tzinfo=madrid)

    assert seconds_until_hard_stop(now, time(0, 0), time(4, 45)) == 8_100


def test_seconds_until_hard_stop_funcion_pura_noche_de_octubre_cuando_el_reloj_atrasa():
    madrid = ZoneInfo("Europe/Madrid")
    # 2026-10-25: la madrugada en la que Europe/Madrid repite las 02:00-03:00
    # (se gana una hora real entre las 01:30 y las 04:45).
    now = datetime(2026, 10, 25, 1, 30, 0, tzinfo=madrid)

    assert seconds_until_hard_stop(now, time(0, 0), time(4, 45)) == 15_300


def test_seconds_until_hard_stop_del_guard_la_noche_de_marzo_cuando_el_reloj_adelanta():
    madrid = ZoneInfo("Europe/Madrid")
    run = _make_run()
    policy = _policy()
    clock = FakeClock(datetime(2026, 3, 29, 1, 30, 0, tzinfo=madrid))
    guard = _make_guard(run=run, policy=policy, clock=clock)

    assert guard.seconds_until_hard_stop() == 8_100


def test_seconds_until_hard_stop_del_guard_la_noche_de_octubre_cuando_el_reloj_atrasa():
    madrid = ZoneInfo("Europe/Madrid")
    run = _make_run()
    policy = _policy()
    clock = FakeClock(datetime(2026, 10, 25, 1, 30, 0, tzinfo=madrid))
    guard = _make_guard(run=run, policy=policy, clock=clock)

    assert guard.seconds_until_hard_stop() == 15_300


def test_is_within_window_no_se_ve_afectado_por_el_cambio_de_hora_de_octubre():
    madrid = ZoneInfo("Europe/Madrid")
    # 02:30 ocurre dos veces esa noche (hora ambigua); en ambas debe seguir
    # dentro de la ventana 00:00-04:45, porque la comparación es sobre la
    # hora local de pared, no sobre el instante UTC.
    first_occurrence = datetime(2026, 10, 25, 2, 30, 0, tzinfo=madrid, fold=0)
    second_occurrence = datetime(2026, 10, 25, 2, 30, 0, tzinfo=madrid, fold=1)

    assert is_within_window(first_occurrence, time(0, 0), time(4, 45))
    assert is_within_window(second_occurrence, time(0, 0), time(4, 45))
