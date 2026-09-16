"""`AgentCallRepository` como fuente de verdad del gasto — los tests que T30 necesita.

`BudgetGuard` (T30) nunca debe leer `Run.tokens_used` para decidir si
autoriza una llamada: debe leer siempre `tokens_used_for_run`, calculado en
la base de datos a partir de `agent_calls`. Estos tests fijan ese contrato:

- la suma es correcta, incluye llamadas fallidas y es específica del `Run`;
- es visible dentro de la misma sesión sin `commit()` (autoflush) y
  sobrevive a un reinicio real del proceso (sesión nueva tras `commit()`);
- la unidad de trabajo (`agent_calls.add` + `runs.save`) es atómica: o se
  ven ambos cambios, o no se ve ninguno;
- un `Run.tokens_used` manipulado en memoria no contamina el acumulado que
  lee `BudgetGuard`.
"""

from __future__ import annotations

import pytest
from factories import make_agent_call, make_run

from nocturna.domain.entities import AgentCallStatus, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work


class _Boom(Exception):
    """Excepción de prueba para forzar el `rollback` de `unit_of_work`."""


def test_tokens_used_for_run_sin_llamadas_devuelve_cero_no_none(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run()
    runs.add(run)
    db_session.flush()

    result = agent_calls.tokens_used_for_run(run.id)

    assert result == 0
    assert result is not None


def test_tokens_used_for_run_suma_tokens_in_mas_tokens_out_solo_de_ese_run(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run_a = make_run()
    run_b = make_run()
    # El índice único parcial solo permite un Run `running`; el segundo se
    # cierra para poder coexistir con el primero.
    run_b.finish(RunStatus.COMPLETED, at=run_b.started_at)
    runs.add(run_a)
    runs.add(run_b)
    db_session.flush()

    agent_calls.add(make_agent_call(run_id=run_a.id, tokens_in=100, tokens_out=50))
    agent_calls.add(make_agent_call(run_id=run_a.id, tokens_in=200, tokens_out=25))
    agent_calls.add(make_agent_call(run_id=run_b.id, tokens_in=9_000, tokens_out=9_000))
    db_session.flush()

    assert agent_calls.tokens_used_for_run(run_a.id) == 100 + 50 + 200 + 25


def test_tokens_used_for_run_cuenta_tambien_las_llamadas_fallidas(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run()
    runs.add(run)
    db_session.flush()

    calls = [
        (AgentCallStatus.OK, 10, 5),
        (AgentCallStatus.ERROR, 20, 0),
        (AgentCallStatus.TIMEOUT, 7, 3),
        (AgentCallStatus.INVALID_OUTPUT, 4, 1),
    ]
    for status, tokens_in, tokens_out in calls:
        agent_calls.add(
            make_agent_call(
                run_id=run.id, status=status, tokens_in=tokens_in, tokens_out=tokens_out
            )
        )
    db_session.flush()

    expected = sum(tokens_in + tokens_out for _, tokens_in, tokens_out in calls)
    assert agent_calls.tokens_used_for_run(run.id) == expected


def test_una_llamada_anadida_sin_commit_ya_se_cuenta_en_la_misma_sesion(db_session):
    """Prueba de `autoflush=True`: sin `flush()` ni `commit()` explícitos."""
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run()
    runs.add(run)
    db_session.flush()

    agent_calls.add(make_agent_call(run_id=run.id, tokens_in=42, tokens_out=8))

    # Ni `db_session.flush()` ni `db_session.commit()` aquí: el `SELECT` que
    # ejecuta `tokens_used_for_run` debe disparar el autoflush por sí solo.
    assert agent_calls.tokens_used_for_run(run.id) == 50


def test_el_acumulado_sobrevive_a_un_reinicio_leido_desde_sesion_nueva(db_session_factory):
    with db_session_factory() as setup_session:
        runs = SqlAlchemyRunRepository(setup_session)
        agent_calls = SqlAlchemyAgentCallRepository(setup_session)
        run = make_run()
        runs.add(run)
        setup_session.flush()
        agent_calls.add(make_agent_call(run_id=run.id, tokens_in=123, tokens_out=45))
        setup_session.commit()
        run_id = run.id

    # "Reinicio": una sesión nueva, sin relación con la que hizo el commit,
    # como si el proceso del pipeline hubiera arrancado de cero.
    with db_session_factory() as restarted_session:
        result = SqlAlchemyAgentCallRepository(restarted_session).tokens_used_for_run(run_id)

    assert result == 168


def test_count_for_run_sin_llamadas_devuelve_cero(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run()
    runs.add(run)
    db_session.flush()

    assert agent_calls.count_for_run(run.id, AgentRole.EDITOR) == 0


def test_count_for_run_cuenta_solo_el_rol_pedido(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run()
    runs.add(run)
    db_session.flush()

    agent_calls.add(make_agent_call(run_id=run.id, agent=AgentRole.READER))
    agent_calls.add(make_agent_call(run_id=run.id, agent=AgentRole.READER))
    agent_calls.add(make_agent_call(run_id=run.id, agent=AgentRole.EDITOR))
    db_session.flush()

    assert agent_calls.count_for_run(run.id, AgentRole.READER) == 2
    assert agent_calls.count_for_run(run.id, AgentRole.EDITOR) == 1
    assert agent_calls.count_for_run(run.id, AgentRole.POPULARIZER) == 0


def test_atomicidad_una_excepcion_deshace_agent_call_y_tokens_used(db_session_factory):
    with db_session_factory() as setup_session:
        runs = SqlAlchemyRunRepository(setup_session)
        run = make_run()
        runs.add(run)
        setup_session.commit()
        run_id = run.id

    with pytest.raises(_Boom):
        with unit_of_work(db_session_factory) as session:
            runs = SqlAlchemyRunRepository(session)
            agent_calls = SqlAlchemyAgentCallRepository(session)
            current_run = runs.get(run_id)
            call = make_agent_call(run_id=run_id, tokens_in=100, tokens_out=50)
            agent_calls.add(call)
            current_run.record_agent_call(call)
            runs.save(current_run)
            raise _Boom("fallo simulado antes de salir de la unidad de trabajo")

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        runs = SqlAlchemyRunRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == 0
        assert runs.get(run_id).tokens_used == 0


def test_atomicidad_camino_feliz_agent_call_y_tokens_used_visibles_desde_otra_sesion(
    db_session_factory,
):
    with db_session_factory() as setup_session:
        runs = SqlAlchemyRunRepository(setup_session)
        run = make_run()
        runs.add(run)
        setup_session.commit()
        run_id = run.id

    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        agent_calls = SqlAlchemyAgentCallRepository(session)
        current_run = runs.get(run_id)
        call = make_agent_call(run_id=run_id, tokens_in=70, tokens_out=30)
        agent_calls.add(call)
        current_run.record_agent_call(call)
        runs.save(current_run)

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        runs = SqlAlchemyRunRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == 100
        assert runs.get(run_id).tokens_used == 100


def test_run_tokens_used_manipulado_en_memoria_no_altera_tokens_used_for_run(db_session):
    runs = SqlAlchemyRunRepository(db_session)
    agent_calls = SqlAlchemyAgentCallRepository(db_session)
    run = make_run()
    runs.add(run)
    db_session.flush()
    agent_calls.add(make_agent_call(run_id=run.id, tokens_in=10, tokens_out=5))
    db_session.flush()

    # `Run.tokens_used` es un campo congelado (ver `domain/entities.py`);
    # `object.__setattr__` esquiva la guarda deliberadamente, igual que
    # documenta la entidad, para simular un objeto en memoria desincronizado.
    object.__setattr__(run, "tokens_used", 999_999)
    runs.save(run)
    db_session.flush()

    # `runs.save` persiste el valor manipulado tal cual (es una caché
    # desnormalizada), pero la fuente de verdad sigue siendo `agent_calls`.
    assert agent_calls.tokens_used_for_run(run.id) == 15
