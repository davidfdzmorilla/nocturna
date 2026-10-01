"""Tests de la variante `reader-v3` (medidas estructuradas) de `ReadItem` (T71.c).

Complementa `tests/test_read_item.py` (que fija `measures_categories=frozenset()`
a propósito y nunca ejercita esta variante): aquí `FakeLLMProvider` simula el
Reader con medidas, sin tocar Claude, la red ni la base de datos --
`.claude/skills/testing-without-claude`.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    make_work_factory,
)

from nocturna.application.budget import BudgetExceeded, BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases import read_item as read_item_module
from nocturna.application.use_cases.read_item import ReaderPrompt, ReadItem, ReadOutcome
from nocturna.domain.entities import AgentCallStatus, Item, ItemStatus, Run
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 100_000,
        "editor_reserve_tokens": 10_000,
        "max_items_per_night": 10,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 5,
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


def _make_run(**overrides: object) -> Run:
    defaults: dict[str, object] = {"started_at": _WITHIN_WINDOW, "budget_tokens": 100_000}
    defaults.update(overrides)
    return Run(**defaults)


def _make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "source": "arxiv",
        "external_id": f"2609.{uuid4().hex[:5]}",
        "title": "Un título de prueba",
        "abstract": (
            "The planet Kepler-0000 b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
        ),
        "categories": ["astro-ph.EP"],
        "published_at": _WITHIN_WINDOW,
        "fetched_at": _WITHIN_WINDOW,
        # T79: el abstract por defecto trata de un exoplaneta, así que la
        # ingesta lo habría marcado.
        "exoplanet_match": True,
    }
    defaults.update(overrides)
    return Item(**defaults)


def _valid_v2_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "summary": "Resumen de prueba",
        "objects": ["NGC 1234"],
        "claims": ["Una afirmación de prueba"],
        "interest_score": 4,
    }
    defaults.update(overrides)
    return defaults


def _valid_v3_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "summary": "Resumen de prueba con medidas",
        "objects": ["Kepler-0000"],
        "claims": ["Una afirmación de prueba"],
        "interest_score": 4,
        "measurements": [
            {
                "planet_name": "Kepler-0000 b",
                "parameter": "mass",
                "value": 2.8,
                "err_plus": 0.5,
                "err_minus": 0.5,
                "unit": "M_jup",
                "limit": "none",
                "origin": "this_work",
                "evidence": "2.8 (+0.5/-0.5) M_jup",
            }
        ],
    }
    defaults.update(overrides)
    return defaults


@dataclass
class _Environment:
    work: AgentWorkFactory
    runs: InMemoryRunRepository
    items: InMemoryItemRepository
    readings: InMemoryReadingRepository
    agent_calls: InMemoryAgentCallRepository
    run: Run


def _make_environment(
    *,
    item: Item,
    policy: BudgetPolicy | None = None,
    run: Run | None = None,
    now: datetime = _WITHIN_WINDOW,
) -> _Environment:
    resolved_policy = policy or _policy()
    resolved_run = run or _make_run(budget_tokens=resolved_policy.nightly_tokens)
    runs = InMemoryRunRepository(resolved_run)
    items = InMemoryItemRepository(item)
    readings = InMemoryReadingRepository()
    agent_calls = InMemoryAgentCallRepository()
    guard = BudgetGuard(
        run_id=resolved_run.id,
        policy=resolved_policy,
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(now),
    )
    work: AgentWorkFactory = make_work_factory(
        guard=guard, runs=runs, items=items, readings=readings, agent_calls=agent_calls
    )
    return _Environment(
        work=work,
        runs=runs,
        items=items,
        readings=readings,
        agent_calls=agent_calls,
        run=resolved_run,
    )


def _make_read_item(
    *,
    work: AgentWorkFactory,
    provider: FakeLLMProvider,
    max_attempts: int = 2,
    model: str = "claude-sonnet-test",
    max_turns: int = 3,
    base_estimated_tokens: int = 6_000,
    measures_estimated_tokens: int = 13_000,
    measures_categories: frozenset[str] = frozenset({"astro-ph.EP"}),
) -> ReadItem:
    return ReadItem(
        work=work,
        provider=provider,
        model=model,
        max_turns=max_turns,
        max_attempts=max_attempts,
        base=ReaderPrompt(
            system_prompt="prompt de sistema del Reader v2",
            prompt_version="reader-v2",
            estimated_tokens=base_estimated_tokens,
        ),
        measures=ReaderPrompt(
            system_prompt="prompt de sistema del Reader v3",
            prompt_version="reader-v3",
            estimated_tokens=measures_estimated_tokens,
        ),
        measures_categories=measures_categories,
    )


# --- 1. Ítem EP usa reader-v3 --------------------------------------------


async def test_item_ep_usa_el_system_prompt_v3_y_el_agentcall_registra_su_prompt_version():
    item = _make_item(categories=["astro-ph.EP"])
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_v3_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.prompt_version == "reader-v3"
    assert len(fake.calls) == 1
    assert fake.calls[0].system_prompt == "prompt de sistema del Reader v3"
    assert len(env.agent_calls.calls) == 1
    assert env.agent_calls.calls[0].prompt_version == "reader-v3"
    assert result.reading is not None
    assert result.reading.measurements is not None
    assert len(result.reading.measurements) == 1


# --- 2. Ítem GA usa reader-v2, measurements es None -----------------------


async def test_item_ga_usa_el_system_prompt_v2_y_measurements_es_none():
    item = _make_item(categories=["astro-ph.GA"])
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_v2_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.prompt_version == "reader-v2"
    assert fake.calls[0].system_prompt == "prompt de sistema del Reader v2"
    assert env.agent_calls.calls[0].prompt_version == "reader-v2"
    assert result.reading is not None
    assert result.reading.measurements is None


# --- 3. Categoría secundaria de una lista cruzada dispara v3 --------------


async def test_categoria_ep_secundaria_en_una_lista_cruzada_usa_v3():
    item = _make_item(categories=["astro-ph.GA", "astro-ph.EP"])
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_v3_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake)

    result = await read_item(item)

    assert result.prompt_version == "reader-v3"
    assert result.reading.measurements is not None


# --- 4. measures_categories vacío: todo se lee con v2, incluso EP ---------


async def test_measures_categories_vacio_usa_siempre_v2_incluso_para_ep():
    item = _make_item(categories=["astro-ph.EP"])
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_v2_json(), tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake, measures_categories=frozenset())

    result = await read_item(item)

    assert result.prompt_version == "reader-v2"
    assert result.reading.measurements is None


# --- 5. Una medida descartada: un solo intento, log con el motivo ---------


async def test_una_medida_descartada_deja_attempts_uno_una_llamada_y_log_con_su_reason(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """`monkeypatch.setattr(..., "disabled", False)`: mismo artefacto de
    orden de ejecución que documenta
    `test_fallo_al_contabilizar_durante_cancelacion_se_descarta_y_relanza_intacta`
    en `test_read_item.py` -- al lanzar la suite completa, `tests/db/` corre
    antes (orden alfabético) y su fixture de migraciones invoca
    `alembic/env.py::fileConfig`, que deshabilita cualquier logger ya
    existente y no declarado en `alembic.ini`, incluido
    `nocturna.application.use_cases.read_item`. Sin reactivarlo aquí, este
    test pasa solo o en el fichero, pero falla en la suite completa por algo
    ajeno al código bajo test."""
    monkeypatch.setattr(read_item_module._logger, "disabled", False)
    item = _make_item(
        categories=["astro-ph.EP"],
        abstract=(
            "The planet Kepler-0000 b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
        ),
    )
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    payload = _valid_v3_json(
        measurements=[
            _valid_v3_json()["measurements"][0],
            {
                "planet_name": "Kepler-0000 b",
                "parameter": "radius",
                "value": 6.4,
                "err_plus": 0.9,
                "err_minus": 0.7,
                "unit": "R_earth",
                "limit": "none",
                "origin": "this_work",
                # 'evidence' parafraseada, no aparece literal en el abstract.
                "evidence": "un radio bastante más grande que el de la Tierra",
            },
        ]
    )
    fake.respond(AgentRole.READER, json=payload, tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake)

    with caplog.at_level(logging.WARNING, logger="nocturna.application.use_cases.read_item"):
        result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.attempts == 1
    assert len(fake.calls) == 1
    assert len(result.reading.measurements) == 1
    assert result.reading.measurements[0].parameter.value == "mass"

    discard_records = [
        r for r in caplog.records if r.getMessage() == "reader.measurement_discarded"
    ]
    assert len(discard_records) == 1
    assert discard_records[0].reason == "evidence_not_literal"


# --- 6. Todas las medidas descartadas: persiste () -------------------------


async def test_todas_las_medidas_descartadas_persiste_tupla_vacia():
    item = _make_item(categories=["astro-ph.EP"])
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    payload = _valid_v3_json(
        measurements=[
            {
                "planet_name": "b",  # sin anfitriona: HOST_NOT_FOUND
                "parameter": "mass",
                "value": 2.8,
                "err_plus": 0.5,
                "err_minus": 0.5,
                "unit": "M_jup",
                "limit": "none",
                "origin": "this_work",
                "evidence": "2.8 (+0.5/-0.5) M_jup",
            }
        ]
    )
    fake.respond(AgentRole.READER, json=payload, tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.reading.measurements == ()


# --- 7. Sin campo 'measurements': reintento, failed tras dos llamadas -----


async def test_sin_measurements_reintenta_y_falla_tras_dos_llamadas_ambas_v3():
    item = _make_item(categories=["astro-ph.EP"])
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    payload_sin_measurements = _valid_v2_json()  # sin 'measurements'
    assert "measurements" not in payload_sin_measurements
    fake.respond(AgentRole.READER, json=payload_sin_measurements, tokens_in=500, tokens_out=50)
    fake.respond(AgentRole.READER, json=payload_sin_measurements, tokens_in=500, tokens_out=50)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    result = await read_item(item)

    assert result.outcome is ReadOutcome.INVALID_OUTPUT
    assert result.reading is None
    assert result.attempts == 2
    assert item.status is ItemStatus.FAILED
    assert len(env.agent_calls.calls) == 2
    assert all(call.status is AgentCallStatus.INVALID_OUTPUT for call in env.agent_calls.calls)
    assert all(call.prompt_version == "reader-v3" for call in env.agent_calls.calls)


# --- 7bis. El reintento con v3 autoriza también con la estimación v3 ------


async def test_reintento_con_v3_autoriza_el_segundo_intento_con_la_estimacion_v3():
    """Intento 1 (JSON sin `measurements`, `InvalidAgentOutput`): consume un
    gasto real tal que el presupuesto restante queda estrictamente entre la
    estimación de `reader-v2` (pequeña) y la de `reader-v3` (grande).
    `AgentRunner.run` reintenta con el MISMO runner -- construido una sola
    vez con `estimated_tokens=measures_estimated_tokens` -- así que el
    segundo `authorize()` debe seguir comparando contra la estimación de
    `reader-v3`, no contra la de `reader-v2`: si usara la pequeña, el
    presupuesto restante (200) le bastaría y se llamaría al proveedor una
    segunda vez; como no queda una segunda respuesta programada para ese
    caso, `BudgetExceeded` es la única señal de que el segundo intento se
    denegó ANTES de llamar al proveedor -- exactamente lo que pide el
    contrato."""
    base_estimated_tokens = 200
    measures_estimated_tokens = 5_000
    policy = _policy(nightly_tokens=5_000, editor_reserve_tokens=0)
    item = _make_item(categories=["astro-ph.EP"])
    env = _make_environment(item=item, policy=policy)
    fake = FakeLLMProvider()
    payload_sin_measurements = _valid_v2_json()  # sin 'measurements'
    assert "measurements" not in payload_sin_measurements
    fake.respond(AgentRole.READER, json=payload_sin_measurements, tokens_in=4_700, tokens_out=100)
    read_item = _make_read_item(
        work=env.work,
        provider=fake,
        max_attempts=2,
        base_estimated_tokens=base_estimated_tokens,
        measures_estimated_tokens=measures_estimated_tokens,
    )

    with pytest.raises(BudgetExceeded):
        await read_item(item)

    assert len(fake.calls) == 1, (
        "el primer intento sí debe llamar al proveedor; el segundo debe denegarse en "
        "authorize() con la estimación de reader-v3 (5.000), antes de gastar una "
        "segunda llamada real -- si usara la estimación de reader-v2 (200, cabría en "
        "el resto de 200), esta llamada llegaría a intentarse"
    )
    assert len(env.agent_calls.calls) == 1
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert env.agent_calls.calls[0].prompt_version == "reader-v3"


# --- 8. Estimación propia: el Reader autoriza cada variante con su propia --
# --- estimación (nunca con la de la otra) -----------------------------------


async def test_reader_autoriza_cada_variante_con_su_propia_estimacion():
    """Presupuesto restante estrictamente entre la estimación de `reader-v2`
    (pequeña) y la de `reader-v3` (grande, T71.c): un ítem `astro-ph.GA`
    (v2) se autoriza y se lee; un ítem `astro-ph.EP` (v3) debe denegarse por
    `BudgetExceeded`, sin llamar al proveedor -- si `ReadItem` usara la
    estimación de la otra variante por error, este test lo distinguiría."""
    base_estimated_tokens = 6_000
    measures_estimated_tokens = 13_000
    remaining_budget = 10_000  # entre las dos estimaciones
    policy = _policy(nightly_tokens=remaining_budget, editor_reserve_tokens=0)

    ga_item = _make_item(categories=["astro-ph.GA"])
    ga_env = _make_environment(item=ga_item, policy=policy)
    fake_ga = FakeLLMProvider()
    fake_ga.respond(AgentRole.READER, json=_valid_v2_json(), tokens_in=100, tokens_out=10)
    read_item_ga = _make_read_item(
        work=ga_env.work,
        provider=fake_ga,
        base_estimated_tokens=base_estimated_tokens,
        measures_estimated_tokens=measures_estimated_tokens,
    )

    ga_result = await read_item_ga(ga_item)

    assert ga_result.outcome is ReadOutcome.READ
    assert len(fake_ga.calls) == 1

    ep_item = _make_item(categories=["astro-ph.EP"])
    ep_env = _make_environment(item=ep_item, policy=policy)
    fake_ep = FakeLLMProvider()
    read_item_ep = _make_read_item(
        work=ep_env.work,
        provider=fake_ep,
        base_estimated_tokens=base_estimated_tokens,
        measures_estimated_tokens=measures_estimated_tokens,
    )

    with pytest.raises(BudgetExceeded):
        await read_item_ep(ep_item)

    assert fake_ep.calls == [], (
        "el ítem EP debe denegarse en authorize() antes de llamar al proveedor: si "
        "ReadItem usara la estimación de reader-v2 (6.000, cabría en 10.000) en vez "
        "de la de reader-v3 (13.000, no cabe), esta llamada llegaría a salir"
    )
    assert ep_env.agent_calls.calls == []


# --- 9. Descartes de un intento fallido no se registran (T71.c, correcciones) --


async def test_descartes_de_un_intento_fallido_no_contaminan_el_log_del_intento_aceptado(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """Intento 1: produce medidas con un descarte (`HOST_NOT_FOUND`), pero
    `Reading(...)` revienta por invariante (`InterestScoreOutOfRange`,
    subclase de `InvariantViolation`) -- el runner lo trata como
    `invalid_output` y reintenta (`runner.py`, "build corre DENTRO del
    runner"). Intento 2: parsea de verdad, con OTRO descarte distinto
    (`EVIDENCE_NOT_LITERAL`), y `Reading(...)` sí se construye.

    No se puede forzar la invariante de `Reading` con `tokens_in`/`tokens_out`/
    `model` negativos o en blanco (la salida natural del intento 1 para
    provocar eso): esos mismos valores viajan sin cambios a `AgentCall`
    (`AgentCall.__post_init__` tiene las MISMAS comprobaciones que
    `Reading.__post_init__` para esos tres campos), así que `_record_attempt`
    reventaría también al contabilizar el intento inválido, en vez de dejar
    que el runner reintente limpio -- no es el escenario que pide este test.
    En su lugar se parchea `parse_reader_v3_output` (mismo patrón de
    'divergencia simulada' que
    `test_reader_measurements_filter.py::test_divergencia_simulada_entre_esquema_y_entidad_descarta_por_invariant_sin_lanzar`,
    aquí a nivel de `Reading` en vez de a nivel de `Measurement`) para que
    el primer intento devuelva un `interest_score=6` que ningún JSON real
    podría producir -- `ReaderOutput` ya lo rechaza con Pydantic antes de
    esto (`Field(ge=1, le=5)`) -- simulando que esa garantía divergiera.

    `monkeypatch.setattr(..., "disabled", False)`: mismo artefacto de orden
    de ejecución que documenta el test nº 5 de este fichero."""
    monkeypatch.setattr(read_item_module._logger, "disabled", False)
    item = _make_item(categories=["astro-ph.EP"])
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    # El contenido de esta primera respuesta es irrelevante -- el parche de
    # abajo intercepta la PRIMERA llamada a `parse_reader_v3_output` y
    # devuelve un objeto fabricado a mano, sin tocar `result.output_text`.
    fake.respond(AgentRole.READER, json=_valid_v3_json(), tokens_in=1200, tokens_out=300)
    payload_attempt_2 = _valid_v3_json(
        measurements=[
            {
                "planet_name": "Kepler-0000 b",
                "parameter": "radius",
                "value": 6.4,
                "err_plus": 0.9,
                "err_minus": 0.7,
                "unit": "R_earth",
                "limit": "none",
                "origin": "this_work",
                # Parafraseada, no aparece literal en el abstract:
                # EVIDENCE_NOT_LITERAL, distinto del HOST_NOT_FOUND del
                # intento 1 -- así el test distingue de cuál intento viene
                # el único registro que debe sobrevivir en el log.
                "evidence": "un radio bastante más grande que el de la Tierra",
            }
        ]
    )
    fake.respond(AgentRole.READER, json=payload_attempt_2, tokens_in=1200, tokens_out=300)
    read_item = _make_read_item(work=env.work, provider=fake, max_attempts=2)

    class _FakeParsedV3:
        def __init__(self, *, summary, objects, claims, interest_score, measurements):
            self.summary = summary
            self.objects = objects
            self.claims = claims
            self.interest_score = interest_score
            self.measurements = measurements

    original_parse_v3 = read_item_module.parse_reader_v3_output
    call_count = {"n": 0}

    def _fake_parse_reader_v3_output(text: str):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _FakeParsedV3(
                summary="Resumen de prueba con medidas",
                objects=["Kepler-0000"],
                claims=["Una afirmación de prueba"],
                interest_score=6,  # fuera de [1, 5]: Reading.__post_init__ lo rechaza
                measurements=[
                    {
                        # sin anfitriona reconocible: HOST_NOT_FOUND, distinto
                        # del descarte del intento 2.
                        "planet_name": "b",
                        "parameter": "mass",
                        "value": 2.8,
                        "err_plus": 0.5,
                        "err_minus": 0.5,
                        "unit": "M_jup",
                        "limit": "none",
                        "origin": "this_work",
                        "evidence": "2.8 (+0.5/-0.5) M_jup",
                    }
                ],
            )
        return original_parse_v3(text)

    monkeypatch.setattr(read_item_module, "parse_reader_v3_output", _fake_parse_reader_v3_output)

    with caplog.at_level(logging.WARNING, logger="nocturna.application.use_cases.read_item"):
        result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.attempts == 2, "el intento 1 cuenta como invalid_output y se reintenta"
    assert len(fake.calls) == 2
    assert len(env.agent_calls.calls) == 2
    assert env.agent_calls.calls[0].status is AgentCallStatus.INVALID_OUTPUT
    assert env.agent_calls.calls[1].status is AgentCallStatus.OK
    assert result.reading is not None
    assert result.reading.measurements == ()  # la única medida del intento 2 se descarta

    discard_records = [
        r for r in caplog.records if r.getMessage() == "reader.measurement_discarded"
    ]
    assert len(discard_records) == 1, (
        "solo debe sobrevivir el descarte del intento ACEPTADO (el 2º); el descarte del "
        "intento 1 (host_not_found), que nunca llegó a persistirse, no debe aparecer en "
        "el log"
    )
    assert discard_records[0].reason == "evidence_not_literal", (
        "el único registro que sobrevive debe ser el del intento 2 -- si el cierre de "
        "'discards_by_attempt' sobre el intento 1 contaminara el log, este assert vería "
        "'host_not_found' en su lugar (o ambos motivos, dos registros en vez de uno)"
    )
