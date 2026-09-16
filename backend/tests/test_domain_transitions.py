"""Tests de transiciones de estado de las entidades de dominio.

Cubren `Item.status`, `Run.status` y `Finding.is_published`: qué saltos
están permitidos y qué excepción concreta lanza cada salto ilegal. Ningún
test llama a Claude, a la red ni a la base de datos.
"""

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nocturna.domain.entities import (
    _ITEM_TRANSITIONS,
    AgentCall,
    AgentCallStatus,
    Finding,
    FindingType,
    Item,
    ItemStatus,
    Run,
    RunStatus,
)
from nocturna.domain.errors import InvalidTransition, InvariantViolation, RunAlreadyFinished
from nocturna.domain.llm import AgentRole

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _make_item(**overrides) -> Item:
    defaults = dict(
        source="arxiv",
        external_id="2601.00001",
        title="Un título cualquiera",
        abstract="Un abstract cualquiera.",
        categories=["astro-ph.EP"],
        published_at=NOW,
        fetched_at=NOW,
    )
    defaults.update(overrides)
    return Item(**defaults)


def _make_run(**overrides) -> Run:
    defaults = dict(started_at=NOW, budget_tokens=1000)
    defaults.update(overrides)
    return Run(**defaults)


def _make_agent_call(run_id, **overrides) -> AgentCall:
    defaults = dict(
        run_id=run_id,
        item_id=None,
        agent=AgentRole.READER,
        model="sonnet",
        tokens_in=10,
        tokens_out=5,
        duration_ms=100,
        status=AgentCallStatus.OK,
    )
    defaults.update(overrides)
    return AgentCall(**defaults)


def _make_finding(**overrides) -> Finding:
    defaults = dict(
        item_id=uuid4(),
        run_id=uuid4(),
        type=FindingType.PAPER_EXPLAINED,
        title="Título",
        level_curious="curioso",
        level_amateur="aficionado",
        level_technical="técnico",
    )
    defaults.update(overrides)
    return Finding(**defaults)


# --- Item -------------------------------------------------------------


def test_item_nace_en_new():
    item = _make_item()

    assert item.status == ItemStatus.NEW


def test_dos_items_tienen_id_distinto():
    a = _make_item()
    b = _make_item()

    assert a.id != b.id


def test_mark_read_lleva_de_new_a_read():
    item = _make_item()

    item.mark_read()

    assert item.status == ItemStatus.READ


def test_read_a_discarded_es_legal():
    item = _make_item()
    item.mark_read()

    item.discard()

    assert item.status == ItemStatus.DISCARDED


def test_read_a_published_es_legal():
    item = _make_item()
    item.mark_read()

    item.publish()

    assert item.status == ItemStatus.PUBLISHED


def test_new_a_published_es_ilegal():
    item = _make_item()

    with pytest.raises(InvalidTransition):
        item.publish()


def test_new_a_discarded_es_ilegal():
    item = _make_item()

    with pytest.raises(InvalidTransition):
        item.discard()


def test_mark_read_dos_veces_falla_sin_idempotencia_silenciosa():
    item = _make_item()
    item.mark_read()

    with pytest.raises(InvalidTransition):
        item.mark_read()


@pytest.mark.parametrize("origin_status", [ItemStatus.PUBLISHED, ItemStatus.DISCARDED])
@pytest.mark.parametrize("action_name", ["mark_read", "discard", "publish"])
def test_desde_estado_terminal_cualquier_transicion_falla(origin_status, action_name):
    item = _make_item(status=origin_status)

    with pytest.raises(InvalidTransition):
        getattr(item, action_name)()


def test_mensaje_de_invalid_transition_contiene_origen_y_destino():
    item = _make_item()

    with pytest.raises(InvalidTransition) as exc_info:
        item.publish()

    assert "new" in str(exc_info.value)
    assert "published" in str(exc_info.value)


# --- Run ----------------------------------------------------------------


def test_run_nace_en_running_sin_tokens_ni_finished_at():
    run = _make_run()

    assert run.status == RunStatus.RUNNING
    assert run.tokens_used == 0
    assert run.finished_at is None


def test_run_finish_fija_status_y_finished_at():
    run = _make_run()
    at = NOW + timedelta(hours=1)

    run.finish(RunStatus.COMPLETED, at)

    assert run.status == RunStatus.COMPLETED
    assert run.finished_at == at


def test_run_finish_dos_veces_falla():
    run = _make_run()
    run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=1))

    with pytest.raises(InvalidTransition):
        run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=2))


def test_run_finish_con_running_falla_por_no_ser_terminal():
    run = _make_run()

    with pytest.raises(InvalidTransition):
        run.finish(RunStatus.RUNNING, NOW + timedelta(hours=1))


def test_run_finish_con_at_anterior_a_started_at_falla():
    run = _make_run()

    with pytest.raises(InvariantViolation):
        run.finish(RunStatus.COMPLETED, NOW - timedelta(hours=1))


# --- Finding --------------------------------------------------------------


def test_finding_publish_pasa_is_published_de_false_a_true():
    finding = _make_finding()
    assert finding.is_published is False

    finding.publish(0.8, NOW)

    assert finding.is_published is True


def test_finding_publish_dos_veces_falla():
    finding = _make_finding()
    finding.publish(0.8, NOW)

    with pytest.raises(InvalidTransition):
        finding.publish(0.9, NOW + timedelta(hours=1))


# --- AgentCall es frozen ----------------------------------------------


def test_agent_call_es_frozen():
    run = _make_run()
    call = _make_agent_call(run.id)

    with pytest.raises(FrozenInstanceError):
        call.status = AgentCallStatus.ERROR


# --- Item.discard() ya no acepta 'reason' ----------------------------------


def test_discard_no_acepta_reason():
    item = _make_item()
    item.mark_read()

    with pytest.raises(TypeError):
        item.discard(reason="interest_score bajo")


# --- InvalidTransition expone entity/origin/target como atributos ---------


def test_invalid_transition_expone_entity_origin_y_target_como_atributos():
    item = _make_item()

    with pytest.raises(InvalidTransition) as exc_info:
        item.publish()

    assert exc_info.value.entity == "Item"
    assert exc_info.value.origin == "new"
    assert exc_info.value.target == "published"


def test_invalid_transition_de_run_y_de_item_se_distinguen_por_entity_sin_parsear_mensaje():
    run = _make_run()
    run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=1))
    call = _make_agent_call(run.id)

    item = _make_item()

    with pytest.raises(InvalidTransition) as run_exc_info:
        run.record_agent_call(call)
    with pytest.raises(InvalidTransition) as item_exc_info:
        item.publish()

    assert run_exc_info.value.entity == "Run"
    assert item_exc_info.value.entity == "Item"
    assert run_exc_info.value.entity != item_exc_info.value.entity


# --- RunAlreadyFinished: capturable como InvalidTransition, mensaje claro -


def test_run_already_finished_es_capturable_como_invalid_transition():
    run = _make_run()
    run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=1))
    call = _make_agent_call(run.id)

    with pytest.raises(InvalidTransition):
        run.record_agent_call(call)


def test_record_agent_call_sobre_run_cerrado_lanza_run_already_finished():
    run = _make_run()
    run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=1))
    call = _make_agent_call(run.id)

    with pytest.raises(RunAlreadyFinished) as exc_info:
        run.record_agent_call(call)

    assert "cerrado" in str(exc_info.value)
    assert "completed" in str(exc_info.value)


# --- Run.finish conserva 'notes' si no se pasan -----------------------------


def test_run_finish_sin_notes_conserva_las_notes_de_la_construccion():
    run = _make_run(notes="incidencia")

    run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=1))

    assert run.notes == "incidencia"


def test_run_finish_con_notes_sobrescribe_las_notes_de_la_construccion():
    run = _make_run(notes="incidencia")

    run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=1), notes="cierre limpio")

    assert run.notes == "cierre limpio"


# --- Rehidratación: construir por constructor en un estado ya avanzado ----


def test_run_rehidratado_completed_con_finished_at_y_tokens_used_es_valido():
    run = Run(
        started_at=NOW,
        budget_tokens=1000,
        status=RunStatus.COMPLETED,
        finished_at=NOW + timedelta(hours=2),
        tokens_used=850,
        items_fetched=20,
        items_read=15,
        findings_published=3,
    )

    assert run.status == RunStatus.COMPLETED
    assert run.finished_at == NOW + timedelta(hours=2)
    assert run.tokens_used == 850


def test_item_rehidratado_en_published_es_valido():
    item = _make_item(status=ItemStatus.PUBLISHED)

    assert item.status == ItemStatus.PUBLISHED


def test_finding_rehidratado_ya_publicado_es_valido():
    finding = _make_finding(confidence=0.7, published_at=NOW)

    assert finding.is_published is True
    assert finding.confidence == 0.7
    assert finding.published_at == NOW


# --- Contrato ejecutable: pertenencia de los enums y de las transiciones --


def test_item_status_contiene_exactamente_los_valores_esperados():
    assert {status.value for status in ItemStatus} == {
        "new",
        "read",
        "discarded",
        "published",
    }


def test_run_status_contiene_exactamente_los_valores_esperados():
    assert {status.value for status in RunStatus} == {
        "running",
        "completed",
        "partial",
        "failed",
        "killed",
    }


def test_agent_call_status_contiene_exactamente_los_valores_esperados():
    assert {status.value for status in AgentCallStatus} == {
        "ok",
        "invalid_output",
        "error",
        "timeout",
    }


def test_agent_role_contiene_exactamente_los_valores_esperados():
    assert {role.value for role in AgentRole} == {"reader", "popularizer", "editor"}


def test_finding_type_contiene_exactamente_los_valores_esperados():
    assert {type_.value for type_ in FindingType} == {"paper_explained"}


def test_item_transitions_tiene_exactamente_las_transiciones_esperadas():
    assert _ITEM_TRANSITIONS == {
        ItemStatus.NEW: frozenset({ItemStatus.READ}),
        ItemStatus.READ: frozenset({ItemStatus.DISCARDED, ItemStatus.PUBLISHED}),
        ItemStatus.DISCARDED: frozenset(),
        ItemStatus.PUBLISHED: frozenset(),
    }
