"""Tests de invariantes de las entidades de dominio.

Cubren rangos numéricos (`interest_score`, `confidence`), campos de texto
obligatorios, timestamps *aware* y la inmutabilidad de `Reading`. Ningún
test llama a Claude, a la red ni a la base de datos.
"""

import inspect
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from nocturna.domain.entities import (
    AgentCall,
    AgentCallStatus,
    Finding,
    FindingType,
    Item,
    ItemStatus,
    Reading,
    Run,
    RunStatus,
)
from nocturna.domain.errors import (
    ConfidenceOutOfRange,
    GuardedFieldAssignment,
    InterestScoreOutOfRange,
    InvalidTimestamp,
    InvalidTransition,
    InvariantViolation,
)
from nocturna.domain.llm import AgentRole

NOW = datetime(2026, 1, 1, tzinfo=UTC)
NAIVE = datetime(2026, 1, 1)


def _base_item_kwargs() -> dict:
    return dict(
        source="arxiv",
        external_id="2601.00001",
        title="Título",
        abstract="Abstract.",
        categories=["astro-ph.EP"],
        published_at=NOW,
        fetched_at=NOW,
    )


def _base_reading_kwargs() -> dict:
    return dict(
        item_id=uuid4(),
        summary="Resumen",
        objects=["Betelgeuse"],
        claims=["Una afirmación"],
        interest_score=3,
        tokens_in=10,
        tokens_out=5,
        model="sonnet",
    )


def _base_finding_kwargs() -> dict:
    return dict(
        item_id=uuid4(),
        run_id=uuid4(),
        type=FindingType.PAPER_EXPLAINED,
        title="Título",
        level_curious="curioso",
        level_amateur="aficionado",
        level_technical="técnico",
    )


def _base_agent_call_kwargs() -> dict:
    return dict(
        run_id=uuid4(),
        item_id=None,
        agent=AgentRole.READER,
        model="sonnet",
        tokens_in=10,
        tokens_out=5,
        duration_ms=100,
        status=AgentCallStatus.OK,
    )


# --- interest_score -----------------------------------------------------


@pytest.mark.parametrize("score", [1, 5])
def test_interest_score_valido_en_los_bordes(score):
    kwargs = _base_reading_kwargs()
    kwargs["interest_score"] = score

    reading = Reading(**kwargs)

    assert reading.interest_score == score


@pytest.mark.parametrize("score", [0, 6])
def test_interest_score_fuera_de_rango_falla(score):
    kwargs = _base_reading_kwargs()
    kwargs["interest_score"] = score

    with pytest.raises(InterestScoreOutOfRange):
        Reading(**kwargs)


# --- confidence -----------------------------------------------------------


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_finding_confidence_valida_en_constructor_en_los_bordes(confidence):
    kwargs = _base_finding_kwargs()
    kwargs["confidence"] = confidence
    kwargs["published_at"] = NOW

    finding = Finding(**kwargs)

    assert finding.confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_finding_confidence_fuera_de_rango_falla_en_constructor(confidence):
    kwargs = _base_finding_kwargs()
    kwargs["confidence"] = confidence
    kwargs["published_at"] = NOW

    with pytest.raises(ConfidenceOutOfRange):
        Finding(**kwargs)


@pytest.mark.parametrize("confidence", [0.0, 1.0])
def test_finding_publish_confidence_valida_en_los_bordes(confidence):
    finding = Finding(**_base_finding_kwargs())

    finding.publish(confidence, NOW)

    assert finding.confidence == confidence


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_finding_publish_confidence_fuera_de_rango_falla(confidence):
    finding = Finding(**_base_finding_kwargs())

    with pytest.raises(ConfidenceOutOfRange):
        finding.publish(confidence, NOW)


# --- Finding: confidence y published_at van juntos o ninguno -----------


def test_finding_con_confidence_sin_published_at_falla():
    kwargs = _base_finding_kwargs()
    kwargs["confidence"] = 0.5

    with pytest.raises(InvariantViolation):
        Finding(**kwargs)


def test_finding_con_published_at_sin_confidence_falla():
    kwargs = _base_finding_kwargs()
    kwargs["published_at"] = NOW

    with pytest.raises(InvariantViolation):
        Finding(**kwargs)


# --- campos de texto obligatorios --------------------------------------


@pytest.mark.parametrize(
    "entity_cls,base_kwargs_fn,field_name",
    [
        (Item, _base_item_kwargs, "source"),
        (Item, _base_item_kwargs, "external_id"),
        (Item, _base_item_kwargs, "title"),
        (Item, _base_item_kwargs, "abstract"),
        (Reading, _base_reading_kwargs, "summary"),
        (Reading, _base_reading_kwargs, "model"),
        (Finding, _base_finding_kwargs, "title"),
        (Finding, _base_finding_kwargs, "level_curious"),
        (Finding, _base_finding_kwargs, "level_amateur"),
        (Finding, _base_finding_kwargs, "level_technical"),
        (AgentCall, _base_agent_call_kwargs, "model"),
    ],
    ids=[
        "item-source",
        "item-external_id",
        "item-title",
        "item-abstract",
        "reading-summary",
        "reading-model",
        "finding-title",
        "finding-level_curious",
        "finding-level_amateur",
        "finding-level_technical",
        "agentcall-model",
    ],
)
def test_campo_de_texto_obligatorio_vacio_falla(entity_cls, base_kwargs_fn, field_name):
    kwargs = base_kwargs_fn()
    kwargs[field_name] = "   "

    with pytest.raises(InvariantViolation):
        entity_cls(**kwargs)


# --- Item.categories -----------------------------------------------------


def test_item_categories_vacia_falla():
    kwargs = _base_item_kwargs()
    kwargs["categories"] = []

    with pytest.raises(InvariantViolation):
        Item(**kwargs)


def test_item_categories_con_elemento_vacio_falla():
    kwargs = _base_item_kwargs()
    kwargs["categories"] = ["astro-ph.EP", "   "]

    with pytest.raises(InvariantViolation):
        Item(**kwargs)


# --- datetime naive -----------------------------------------------------


@pytest.mark.parametrize("field_name", ["published_at", "fetched_at"])
def test_item_datetime_naive_falla(field_name):
    kwargs = _base_item_kwargs()
    kwargs[field_name] = NAIVE

    with pytest.raises(InvalidTimestamp):
        Item(**kwargs)


def test_run_started_at_naive_falla():
    with pytest.raises(InvalidTimestamp):
        Run(started_at=NAIVE, budget_tokens=1000)


# --- tokens / duración / presupuesto negativos --------------------------


@pytest.mark.parametrize("field_name", ["tokens_in", "tokens_out"])
def test_reading_tokens_negativos_falla(field_name):
    kwargs = _base_reading_kwargs()
    kwargs[field_name] = -1

    with pytest.raises(InvariantViolation):
        Reading(**kwargs)


@pytest.mark.parametrize("field_name", ["tokens_in", "tokens_out"])
def test_agent_call_tokens_negativos_falla(field_name):
    kwargs = _base_agent_call_kwargs()
    kwargs[field_name] = -1

    with pytest.raises(InvariantViolation):
        AgentCall(**kwargs)


def test_agent_call_duration_ms_negativo_falla():
    kwargs = _base_agent_call_kwargs()
    kwargs["duration_ms"] = -1

    with pytest.raises(InvariantViolation):
        AgentCall(**kwargs)


@pytest.mark.parametrize("budget_tokens", [0, -100])
def test_run_budget_tokens_no_positivo_falla(budget_tokens):
    with pytest.raises(InvariantViolation):
        Run(started_at=NOW, budget_tokens=budget_tokens)


# --- Reading es frozen ----------------------------------------------------


def test_reading_es_frozen():
    reading = Reading(**_base_reading_kwargs())

    with pytest.raises(FrozenInstanceError):
        reading.interest_score = 5


# --- AgentCall.total_tokens e item_id opcional ----------------------------


def test_agent_call_total_tokens_es_la_suma():
    kwargs = _base_agent_call_kwargs()
    kwargs["tokens_in"] = 100
    kwargs["tokens_out"] = 40

    call = AgentCall(**kwargs)

    assert call.total_tokens == 140


def test_agent_call_item_id_none_es_valido_para_editor():
    kwargs = _base_agent_call_kwargs()
    kwargs["agent"] = AgentRole.EDITOR
    kwargs["item_id"] = None

    call = AgentCall(**kwargs)

    assert call.item_id is None
    assert call.agent == AgentRole.EDITOR


# --- Run.record_agent_call -------------------------------------------


def test_record_agent_call_suma_total_tokens():
    run = Run(started_at=NOW, budget_tokens=1000)
    kwargs = _base_agent_call_kwargs()
    kwargs["run_id"] = run.id
    kwargs["tokens_in"] = 100
    kwargs["tokens_out"] = 20
    call = AgentCall(**kwargs)

    run.record_agent_call(call)

    assert run.tokens_used == 120


def test_record_agent_call_dos_llamadas_acumulan():
    run = Run(started_at=NOW, budget_tokens=1000)
    kwargs1 = _base_agent_call_kwargs()
    kwargs1["run_id"] = run.id
    kwargs1["tokens_in"] = 100
    kwargs1["tokens_out"] = 20
    kwargs2 = _base_agent_call_kwargs()
    kwargs2["run_id"] = run.id
    kwargs2["tokens_in"] = 50
    kwargs2["tokens_out"] = 10

    run.record_agent_call(AgentCall(**kwargs1))
    run.record_agent_call(AgentCall(**kwargs2))

    assert run.tokens_used == 180


def test_record_agent_call_con_run_id_ajeno_falla():
    run = Run(started_at=NOW, budget_tokens=1000)
    kwargs = _base_agent_call_kwargs()
    kwargs["run_id"] = uuid4()
    call = AgentCall(**kwargs)

    with pytest.raises(InvariantViolation):
        run.record_agent_call(call)


def test_record_agent_call_sobre_run_ya_cerrado_falla():
    run = Run(started_at=NOW, budget_tokens=1000)
    run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=1))
    kwargs = _base_agent_call_kwargs()
    kwargs["run_id"] = run.id
    call = AgentCall(**kwargs)

    with pytest.raises(InvalidTransition):
        run.record_agent_call(call)


# --- Control de gasto: decrementar tokens_used está bloqueado -------------


def test_decrementar_tokens_used_directamente_lanza_guardedfieldassignment():
    """La garantía real para el control de gasto (T30): un decremento directo
    de `tokens_used` no se queda callado ni tiene éxito, lanza
    `GuardedFieldAssignment` y el contador conserva su valor.
    """
    run = Run(started_at=NOW, budget_tokens=1_000_000, tokens_used=500_000)

    with pytest.raises(GuardedFieldAssignment):
        run.tokens_used -= 500_000

    assert run.tokens_used == 500_000


def test_run_solo_expone_finish_y_record_agent_call_como_metodos_publicos():
    """Documenta, para el control de gasto (T30), que `Run` no expone ningún
    otro método público que pudiera tocar `tokens_used`. Si en el futuro
    alguien añade un `decrement_tokens`, un `classmethod` tipo `from_row` o
    un `@property.setter` sobre `tokens_used`, este test debe dispararse:
    `inspect.isfunction` por sí solo no ve `classmethod`, `staticmethod` ni
    `property`, así que se comprueban también esos tipos de miembro. La
    garantía de que no se puede decrementar `tokens_used` la comprueba, por
    separado, `test_run_tokens_used_asignacion_directa_siempre_bloqueada`.
    """
    public_callables = sorted(
        name
        for name, member in inspect.getmembers(Run)
        if not name.startswith("_")
        and (
            inspect.isfunction(member)
            or inspect.ismethod(member)
            or isinstance(inspect.getattr_static(Run, name), (classmethod, staticmethod, property))
        )
    )

    assert public_callables == ["finish", "record_agent_call"]


# --- Campos bloqueados tras la construcción (GuardedFieldAssignment) ------


def _guarded_field_cases():
    return [
        pytest.param(
            lambda: Item(**_base_item_kwargs()),
            "status",
            ItemStatus.READ,
            id="item-status",
        ),
        pytest.param(
            lambda: Finding(**_base_finding_kwargs()),
            "confidence",
            0.9,
            id="finding-confidence",
        ),
        pytest.param(
            lambda: Finding(**_base_finding_kwargs()),
            "published_at",
            NOW,
            id="finding-published_at",
        ),
        pytest.param(
            lambda: Run(started_at=NOW, budget_tokens=1000),
            "status",
            RunStatus.COMPLETED,
            id="run-status",
        ),
        pytest.param(
            lambda: Run(started_at=NOW, budget_tokens=1000),
            "budget_tokens",
            2000,
            id="run-budget_tokens",
        ),
    ]


@pytest.mark.parametrize("build_entity,field_name,new_value", _guarded_field_cases())
def test_asignar_campo_bloqueado_desde_fuera_falla_y_no_cambia_el_valor(
    build_entity, field_name, new_value
):
    entity = build_entity()
    original_value = getattr(entity, field_name)

    with pytest.raises(GuardedFieldAssignment):
        setattr(entity, field_name, new_value)

    assert getattr(entity, field_name) == original_value


# --- Run: contadores monótonos no decrecientes -----------------------------


@pytest.mark.parametrize(
    "field_name",
    ["items_fetched", "items_read", "findings_published"],
)
def test_run_contador_monotono_admite_incremento_y_repetir_el_mismo_valor(field_name):
    run = Run(started_at=NOW, budget_tokens=1000, **{field_name: 10})

    setattr(run, field_name, 15)
    assert getattr(run, field_name) == 15

    setattr(run, field_name, 15)
    assert getattr(run, field_name) == 15


@pytest.mark.parametrize(
    "field_name",
    ["items_fetched", "items_read", "findings_published"],
)
def test_run_contador_monotono_rechaza_decremento_y_no_cambia_el_valor(field_name):
    run = Run(started_at=NOW, budget_tokens=1000, **{field_name: 10})

    with pytest.raises(GuardedFieldAssignment):
        setattr(run, field_name, 9)

    assert getattr(run, field_name) == 10


@pytest.mark.parametrize(
    "new_value",
    [500_000, 499_999, 500_000],
    ids=["incremento", "decremento", "mismo_valor"],
)
def test_run_tokens_used_asignacion_directa_siempre_bloqueada(new_value):
    """`tokens_used` es el campo crítico de gasto (T30): a diferencia de los
    contadores monótonos, no admite ninguna asignación directa desde fuera,
    ni siquiera un incremento o repetir el mismo valor. Su único mutador
    legítimo es `record_agent_call()` (ver los tests de esa sección).
    """
    run = Run(started_at=NOW, budget_tokens=1_000_000, tokens_used=500_000)

    with pytest.raises(GuardedFieldAssignment):
        run.tokens_used = new_value

    assert run.tokens_used == 500_000


# --- Run: finished_at >= started_at también se valida en rehidratación ----


def test_run_construido_con_finished_at_anterior_a_started_at_falla():
    """No es `finish()`: es la rehidratación desde el ORM (T11), que
    construye el `Run` directamente por constructor con `finished_at` ya
    informado.
    """
    with pytest.raises(InvariantViolation):
        Run(
            started_at=NOW,
            budget_tokens=1000,
            status=RunStatus.COMPLETED,
            finished_at=NOW - timedelta(hours=1),
        )


# --- Run.finished_at bloqueado tras la construcción ------------------------


def _run_terminal() -> Run:
    run = Run(started_at=NOW, budget_tokens=1000)
    run.finish(RunStatus.COMPLETED, NOW + timedelta(hours=1))
    return run


def _run_running() -> Run:
    return Run(started_at=NOW, budget_tokens=1000)


@pytest.mark.parametrize(
    "build_run,new_value",
    [
        pytest.param(_run_terminal, None, id="reasignar_none_sobre_run_terminal"),
        pytest.param(_run_running, NOW, id="asignar_fecha_sobre_run_running"),
        pytest.param(_run_running, NAIVE, id="asignar_fecha_naive"),
        pytest.param(
            _run_running,
            NOW - timedelta(hours=1),
            id="asignar_fecha_anterior_a_started_at",
        ),
    ],
)
def test_run_finished_at_asignacion_directa_siempre_bloqueada(build_run, new_value):
    """`finished_at` solo cambia a través de `finish()`, que valida la
    transición y la coherencia con `started_at`. Una asignación directa se
    rechaza siempre, sin importar si el `Run` ya es terminal o sigue
    `running`, ni si el valor sería por sí mismo inválido.
    """
    run = build_run()
    original_value = run.finished_at

    with pytest.raises(GuardedFieldAssignment):
        run.finished_at = new_value

    assert run.finished_at == original_value


def test_run_finish_sigue_fijando_finished_at():
    run = Run(started_at=NOW, budget_tokens=1000)
    at = NOW + timedelta(hours=1)

    run.finish(RunStatus.COMPLETED, at)

    assert run.finished_at == at
    assert run.status == RunStatus.COMPLETED


# --- Run: la guarda monótona valida el tipo, no solo el valor --------------


@pytest.mark.parametrize("bad_value", [500.4, True, None, "abc"])
def test_run_contador_monotono_con_tipo_invalido_lanza_error_de_dominio(bad_value):
    """Asignar un valor no-`int` (incluido `bool`, que en Python es subclase
    de `int`) a un contador monótono se rechaza con una excepción de
    dominio, nunca con un `TypeError` de comparación sin capturar.
    """
    run = Run(started_at=NOW, budget_tokens=1000, items_read=10)

    with pytest.raises((GuardedFieldAssignment, InvariantViolation)):
        run.items_read = bad_value

    assert run.items_read == 10


# --- Reading: objects y claims se normalizan a tupla desde una lista -------


def test_reading_objects_es_tupla_inmutable():
    """`_base_reading_kwargs()` pasa listas para `objects` y `claims` —el
    camino real, que es lo que devolverá la columna JSON/ARRAY cuando T11
    rehidrate—, así que este test demuestra que `Reading` las normaliza ella
    misma a tupla en `__post_init__`, y no solo que una tupla ya construida
    carece de `.append`.
    """
    reading = Reading(**_base_reading_kwargs())

    assert type(reading.objects) is tuple
    with pytest.raises(AttributeError):
        reading.objects.append("Otra cosa")
    assert hash(reading) is not None


def test_reading_claims_es_tupla_inmutable():
    reading = Reading(**_base_reading_kwargs())

    assert type(reading.claims) is tuple
    with pytest.raises(AttributeError):
        reading.claims.append("Otra afirmación")
    assert hash(reading) is not None
