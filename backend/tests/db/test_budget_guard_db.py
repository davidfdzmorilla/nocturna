"""`BudgetGuard` contra PostgreSQL real — los tests más importantes del proyecto.

De estos tests depende que un bucle a las 3 de la mañana no deje al autor
sin Claude durante días (`CLAUDE.md`, "Control de gasto"). Cubren los
cuatro puntos de "Hecho cuando" de T30 (`docs/PLAN_TAREAS.md`) más los
casos de persistencia, atomicidad y cierre de `Run` que exige la revisión
de `.claude/skills/budget-guard-review`.

Ninguna hora sale de `datetime.now()`: siempre de `FakeClock`. Ninguna
llamada a Claude: `BudgetGuard` no conoce `LLMProvider`.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
import sqlalchemy as sa
from factories import make_agent_call, make_run
from fakes.clock import FakeClock

from nocturna.application.budget import BudgetGuard, BudgetPolicy, DenyReason, terminal_status_for
from nocturna.domain.entities import AgentCallStatus, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.models import RunRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

# Mismos valores que `config/pipeline.toml`: nightly_tokens=300_000,
# editor_reserve_tokens=60_000, así que el presupuesto disponible para
# Reader/Popularizer (sin la reserva del Editor) es 240_000.
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_AT_HARD_STOP = datetime(2026, 1, 1, 4, 45, 0, tzinfo=UTC)
_JUST_BEFORE_HARD_STOP = datetime(2026, 1, 1, 4, 44, 59, tzinfo=UTC)
_JUST_AFTER_HARD_STOP = datetime(2026, 1, 1, 4, 46, 0, tzinfo=UTC)


class _Boom(Exception):
    """Excepción de prueba para forzar el `rollback` de `unit_of_work`."""


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


# --- 1. Corte al alcanzar `nightly_tokens` -------------------------------


def test_deniega_por_presupuesto_al_alcanzar_exactamente_el_disponible_de_reader(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    agent_calls.add(
        make_agent_call(run_id=run.id, agent=AgentRole.READER, tokens_in=200_000, tokens_out=40_000)
    )
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    decision = guard.check(AgentRole.READER, 1)

    assert not decision
    assert decision.reason is DenyReason.BUDGET_EXHAUSTED


def test_justo_por_debajo_del_disponible_autoriza_y_el_siguiente_ya_no(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    agent_calls.add(
        make_agent_call(run_id=run.id, agent=AgentRole.READER, tokens_in=239_000, tokens_out=0)
    )
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    assert guard.check(AgentRole.READER, 500)

    decision = guard.check(AgentRole.READER, 2_000)
    assert not decision
    assert decision.reason is DenyReason.BUDGET_EXHAUSTED


def test_el_editor_es_el_unico_rol_que_llega_al_techo_real_de_nightly_tokens(db_session):
    """Los dos tests anteriores cortan en 240_000 (el disponible de
    Reader/Popularizer con la reserva del Editor ya restada), no en los
    300_000 de `nightly_tokens`. El Editor es el único rol que ve el
    presupuesto completo (`_available_tokens`), así que es el único que
    puede llegar de verdad al techo de `nightly_tokens`: con 240_000 ya
    gastados por el Reader, al Editor solo le quedan los 60_000 de su
    reserva, y pedir uno más de esos 60_000 debe denegarse."""
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    agent_calls.add(
        make_agent_call(run_id=run.id, agent=AgentRole.READER, tokens_in=200_000, tokens_out=40_000)
    )
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    # Exactamente en el techo (300_000 gastados si se autoriza): permitido.
    assert guard.check(AgentRole.EDITOR, 60_000)

    # Un token más allá del techo real de `nightly_tokens`: denegado.
    decision = guard.check(AgentRole.EDITOR, 60_001)
    assert not decision
    assert decision.reason is DenyReason.BUDGET_EXHAUSTED


# --- 2. Rechazo pasada `hard_stop` ----------------------------------------


def test_deniega_a_las_04_45_00_aunque_el_presupuesto_este_intacto(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_AT_HARD_STOP),
    )

    decision = guard.check(AgentRole.READER, 100)

    assert not decision
    assert decision.reason is DenyReason.OUTSIDE_WINDOW


def test_deniega_a_las_04_46_aunque_el_presupuesto_este_intacto(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_JUST_AFTER_HARD_STOP),
    )

    decision = guard.check(AgentRole.READER, 100)

    assert not decision
    assert decision.reason is DenyReason.OUTSIDE_WINDOW


def test_autoriza_a_las_04_44_59(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_JUST_BEFORE_HARD_STOP),
    )

    assert guard.check(AgentRole.READER, 100)


# --- 3. Reserva del Editor intacta aunque el Reader agote lo suyo --------


def test_la_reserva_del_editor_sigue_disponible_cuando_el_reader_agota_lo_suyo(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    agent_calls.add(
        make_agent_call(run_id=run.id, agent=AgentRole.READER, tokens_in=240_000, tokens_out=0)
    )
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    reader_decision = guard.check(AgentRole.READER, 1)
    assert not reader_decision
    assert reader_decision.reason is DenyReason.BUDGET_EXHAUSTED

    assert guard.remaining_for(AgentRole.EDITOR) == 60_000
    assert guard.check(AgentRole.EDITOR, 55_000)


# --- 4. El acumulado sobrevive al reinicio --------------------------------


def test_el_acumulado_sobrevive_a_un_reinicio_leido_por_un_guard_nuevo(db_session_factory):
    with db_session_factory() as setup_session:
        runs = SqlAlchemyRunRepository(setup_session)
        agent_calls = SqlAlchemyAgentCallRepository(setup_session)
        run = make_run(budget_tokens=300_000)
        runs.add(run)
        setup_session.flush()
        agent_calls.add(
            make_agent_call(run_id=run.id, agent=AgentRole.READER, tokens_in=240_000, tokens_out=0)
        )
        setup_session.commit()
        run_id = run.id

    # "Reinicio": una instancia *nueva* de BudgetGuard, sobre una sesión
    # nueva sin relación con la que hizo el commit — como un proceso
    # arrancado de cero.
    with db_session_factory() as restarted_session:
        restarted_guard = BudgetGuard(
            run_id=run_id,
            policy=_policy(),
            runs=SqlAlchemyRunRepository(restarted_session),
            agent_calls=SqlAlchemyAgentCallRepository(restarted_session),
            clock=FakeClock(_WITHIN_WINDOW),
        )

        decision = restarted_guard.check(AgentRole.READER, 1)

    assert not decision
    assert decision.reason is DenyReason.BUDGET_EXHAUSTED


def test_el_guard_lee_agent_calls_no_run_tokens_used_manipulado_en_la_bd(db_session_factory):
    """La variante que más importa: la fuente de verdad es `agent_calls`, no `runs.tokens_used`."""
    with db_session_factory() as setup_session:
        runs = SqlAlchemyRunRepository(setup_session)
        agent_calls = SqlAlchemyAgentCallRepository(setup_session)
        run = make_run(budget_tokens=300_000)
        runs.add(run)
        setup_session.flush()
        agent_calls.add(
            make_agent_call(run_id=run.id, agent=AgentRole.READER, tokens_in=240_000, tokens_out=0)
        )
        setup_session.commit()
        run_id = run.id

    # Manipulación directa en base de datos, sin pasar por `record_agent_call`
    # ni `BudgetGuard.record_call`: simula un `runs.tokens_used` desincronizado.
    with db_session_factory() as tamper_session:
        tamper_session.execute(sa.update(RunRow).where(RunRow.id == run_id).values(tokens_used=0))
        tamper_session.commit()

    with db_session_factory() as check_session:
        tampered_run = SqlAlchemyRunRepository(check_session).get(run_id)
        assert tampered_run.tokens_used == 0  # confirma que la manipulación surtió efecto

        guard = BudgetGuard(
            run_id=run_id,
            policy=_policy(),
            runs=SqlAlchemyRunRepository(check_session),
            agent_calls=SqlAlchemyAgentCallRepository(check_session),
            clock=FakeClock(_WITHIN_WINDOW),
        )
        decision = guard.check(AgentRole.READER, 1)

    assert not decision
    assert decision.reason is DenyReason.BUDGET_EXHAUSTED


# --- 5. Autoflush: cuenta sin `commit()` ----------------------------------


def test_una_llamada_anadida_sin_commit_ya_cuenta_para_el_guard(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    agent_calls.add(
        make_agent_call(run_id=run.id, agent=AgentRole.READER, tokens_in=239_900, tokens_out=0)
    )
    # Ni `db_session.flush()` ni `db_session.commit()`: el `SELECT` que hace
    # `guard.check` debe disparar el autoflush por sí solo.

    assert guard.spent() == 239_900
    decision = guard.check(AgentRole.READER, 200)
    assert not decision
    assert decision.reason is DenyReason.BUDGET_EXHAUSTED


# --- 6. `record_call` atómico ---------------------------------------------


def test_record_call_escribe_agent_call_y_tokens_used_en_la_misma_transaccion(db_session_factory):
    with db_session_factory() as setup_session:
        runs = SqlAlchemyRunRepository(setup_session)
        run = make_run(budget_tokens=300_000)
        runs.add(run)
        setup_session.commit()
        run_id = run.id

    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        agent_calls = SqlAlchemyAgentCallRepository(session)
        guard = BudgetGuard(
            run_id=run_id,
            policy=_policy(),
            runs=runs,
            agent_calls=agent_calls,
            clock=FakeClock(_WITHIN_WINDOW),
        )
        current_run = runs.get(run_id)
        call = make_agent_call(run_id=run_id, agent=AgentRole.READER, tokens_in=70, tokens_out=30)
        guard.record_call(call, current_run)

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        runs = SqlAlchemyRunRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == 100
        assert runs.get(run_id).tokens_used == 100


def test_record_call_un_rollback_no_deja_ni_la_fila_ni_el_contador(db_session_factory):
    with db_session_factory() as setup_session:
        runs = SqlAlchemyRunRepository(setup_session)
        run = make_run(budget_tokens=300_000)
        runs.add(run)
        setup_session.commit()
        run_id = run.id

    with pytest.raises(_Boom):
        with unit_of_work(db_session_factory) as session:
            runs = SqlAlchemyRunRepository(session)
            agent_calls = SqlAlchemyAgentCallRepository(session)
            guard = BudgetGuard(
                run_id=run_id,
                policy=_policy(),
                runs=runs,
                agent_calls=agent_calls,
                clock=FakeClock(_WITHIN_WINDOW),
            )
            current_run = runs.get(run_id)
            call = make_agent_call(
                run_id=run_id, agent=AgentRole.READER, tokens_in=70, tokens_out=30
            )
            guard.record_call(call, current_run)
            raise _Boom("fallo simulado antes de salir de la unidad de trabajo")

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        runs = SqlAlchemyRunRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == 0
        assert runs.get(run_id).tokens_used == 0


def test_record_call_sobre_un_run_ya_cerrado_persiste_el_agent_call_de_verdad(db_session_factory):
    """Secuencia real de T44 en el corte de las 04:45: una llamada sigue en
    vuelo cuando el Run ya se cerró como `killed`. Los tokens de esa
    llamada se gastaron de verdad contra la suscripción, así que
    `record_call` debe dejar el `AgentCall` persistido en base de datos
    (comprobado tras el commit, con una sesión nueva) aunque
    `Run.tokens_used` se quede sin actualizar. Un guard construido después,
    sobre esa misma base, debe seguir decidiendo con el acumulado real de
    `agent_calls`."""
    with db_session_factory() as setup_session:
        runs = SqlAlchemyRunRepository(setup_session)
        run = make_run(budget_tokens=300_000)
        run.finish(RunStatus.KILLED, at=_JUST_AFTER_HARD_STOP)
        runs.add(run)
        setup_session.commit()
        run_id = run.id

    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        agent_calls = SqlAlchemyAgentCallRepository(session)
        guard = BudgetGuard(
            run_id=run_id,
            policy=_policy(),
            runs=runs,
            agent_calls=agent_calls,
            clock=FakeClock(_JUST_AFTER_HARD_STOP),
        )
        current_run = runs.get(run_id)
        call = make_agent_call(
            run_id=run_id, agent=AgentRole.READER, tokens_in=1_000, tokens_out=500
        )
        guard.record_call(call, current_run)  # no debe lanzar RunAlreadyFinished

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        runs = SqlAlchemyRunRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == 1_500
        assert runs.get(run_id).tokens_used == 0  # se queda corto: no es la fuente de verdad

        fresh_guard = BudgetGuard(
            run_id=run_id,
            policy=_policy(),
            runs=runs,
            agent_calls=agent_calls,
            clock=FakeClock(_WITHIN_WINDOW),
        )
        assert fresh_guard.spent() == 1_500


# --- 7. `RUN_NOT_RUNNING` ---------------------------------------------------


def test_un_run_cerrado_deniega_toda_llamada(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    run.finish(RunStatus.COMPLETED, at=run.started_at)
    runs.add(run)
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    decision = guard.check(AgentRole.READER, 100)

    assert not decision
    assert decision.reason is DenyReason.RUN_NOT_RUNNING
    # Coherente con `check`: un Run cerrado no tiene presupuesto disponible.
    assert guard.remaining_for(AgentRole.READER) == 0


def test_un_run_inexistente_deniega_toda_llamada(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    guard = BudgetGuard(
        run_id=uuid4(),
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    decision = guard.check(AgentRole.READER, 100)

    assert not decision
    assert decision.reason is DenyReason.RUN_NOT_RUNNING


# --- 8. Cierre del Run según el motivo de denegación ----------------------


def test_presupuesto_agotado_cierra_el_run_como_partial(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    agent_calls.add(
        make_agent_call(run_id=run.id, agent=AgentRole.READER, tokens_in=240_000, tokens_out=0)
    )
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    decision = guard.check(AgentRole.READER, 1)
    assert not decision
    status = terminal_status_for(decision.reason)
    assert status is RunStatus.PARTIAL

    current_run = runs.get(run.id)
    current_run.finish(status, at=_WITHIN_WINDOW)
    runs.save(current_run)
    db_session.flush()

    assert runs.get(run.id).status is RunStatus.PARTIAL


def test_fuera_de_ventana_cierra_el_run_como_killed(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_JUST_AFTER_HARD_STOP),
    )

    decision = guard.check(AgentRole.READER, 1)
    assert not decision
    status = terminal_status_for(decision.reason)
    assert status is RunStatus.KILLED

    current_run = runs.get(run.id)
    current_run.finish(status, at=_JUST_AFTER_HARD_STOP)
    runs.save(current_run)
    db_session.flush()

    assert runs.get(run.id).status is RunStatus.KILLED


# --- 9. Tope de llamadas del Editor ----------------------------------------


def test_tope_de_llamadas_del_editor_deniega_a_la_tercera_de_cualquier_estado(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run(budget_tokens=300_000)
    runs.add(run)
    db_session.flush()
    guard = BudgetGuard(
        run_id=run.id,
        policy=_policy(),
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )

    # Primer intento del Editor: JSON inválido. `CLAUDE.md` concede un
    # reintento, así que este intento fallido NO debe consumir el tope.
    agent_calls.add(
        make_agent_call(
            run_id=run.id, agent=AgentRole.EDITOR, status=AgentCallStatus.INVALID_OUTPUT
        )
    )
    db_session.flush()

    retry_decision = guard.check(AgentRole.EDITOR, 8_000)
    assert retry_decision

    # El reintento sale bien.
    agent_calls.add(
        make_agent_call(run_id=run.id, agent=AgentRole.EDITOR, status=AgentCallStatus.OK)
    )
    db_session.flush()

    # `max_editor_calls_per_night = 2` ya está agotado (dos intentos,
    # cualquiera que fuera su estado).
    third_decision = guard.check(AgentRole.EDITOR, 8_000)
    assert not third_decision
    assert third_decision.reason is DenyReason.EDITOR_ALREADY_CALLED
