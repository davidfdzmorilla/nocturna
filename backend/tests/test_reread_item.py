"""T82: `ReadItem.reread_with_measurements` y `reread_refusal` (en memoria, sin Claude).

`FakeLLMProvider` simula el Reader; ningún test toca la red ni la base
(`.claude/skills/testing-without-claude`).
"""

from __future__ import annotations

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

from nocturna.application.budget import (
    BudgetDenied,
    BudgetExceeded,
    BudgetGuard,
    BudgetPolicy,
    CallLimitReached,
    OutsideExecutionWindow,
)
from nocturna.application.use_cases.read_item import (
    ReaderPrompt,
    ReadItem,
    ReadOutcome,
    RereadRefusal,
    RereadRefused,
    reread_refusal,
)
from nocturna.domain.entities import AgentCallStatus, Item, ItemStatus, Reading, Run
from nocturna.domain.errors import LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio

_WITHIN = datetime(2026, 1, 1, 2, 0, tzinfo=UTC)
_OUTSIDE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
_CATEGORIES = frozenset({"astro-ph.EP"})
_ABSTRACT = "The planet Kepler-0000 b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 100_000,
        "editor_reserve_tokens": 10_000,
        "writer_reserve_tokens": 0,
        "max_items_per_night": 10,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_writer_calls_per_night": 0,
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


def _item(status: ItemStatus = ItemStatus.READ, **overrides: object) -> Item:
    defaults: dict[str, object] = {
        "source": "arxiv",
        "external_id": f"2609.{uuid4().hex[:5]}",
        "title": "Un título de prueba",
        "abstract": _ABSTRACT,
        "categories": ["astro-ph.EP"],
        "published_at": _WITHIN,
        "fetched_at": _WITHIN,
        "exoplanet_match": True,
        "status": status,
    }
    defaults.update(overrides)
    return Item(**defaults)


def _previous(item: Item, **overrides: object) -> Reading:
    defaults: dict[str, object] = {
        "item_id": item.id,
        "summary": "Resumen v2",
        "objects": ("Kepler-0000",),
        "claims": ("algo",),
        "interest_score": 3,
        "tokens_in": 100,
        "tokens_out": 10,
        "model": "claude-sonnet-test",
        "measurements": None,
        "prompt_version": "reader-v2",
    }
    defaults.update(overrides)
    return Reading(**defaults)


def _v3_json() -> dict:
    return {
        "summary": "Resumen v3",
        "objects": ["Kepler-0000"],
        "claims": ["algo"],
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


class _Env:
    def __init__(
        self,
        item: Item,
        previous: Reading | None,
        *,
        policy: BudgetPolicy | None = None,
        now: datetime = _WITHIN,
        max_attempts: int = 2,
        base_estimated: int = 6_000,
        measures_estimated: int = 13_000,
    ) -> None:
        self.item = item
        self.policy = policy or _policy()
        run = Run(started_at=_WITHIN, budget_tokens=self.policy.nightly_tokens)
        self.runs = InMemoryRunRepository(run)
        self.items = InMemoryItemRepository(item)
        self.readings = InMemoryReadingRepository()
        if previous is not None:
            self.readings.add(previous)
        self.agent_calls = InMemoryAgentCallRepository()
        guard = BudgetGuard(
            run_id=run.id,
            policy=self.policy,
            runs=self.runs,
            agent_calls=self.agent_calls,
            clock=FakeClock(now),
        )
        work = make_work_factory(
            guard=guard,
            runs=self.runs,
            items=self.items,
            readings=self.readings,
            agent_calls=self.agent_calls,
        )
        self.fake = FakeLLMProvider()
        self.read_item = ReadItem(
            work=work,
            provider=self.fake,
            model="claude-sonnet-test",
            max_turns=3,
            max_attempts=max_attempts,
            base=ReaderPrompt("sistema v2", "reader-v2", base_estimated),
            measures=ReaderPrompt("sistema v3", "reader-v3", measures_estimated),
            measures_categories=_CATEGORIES,
        )


# --- 1. éxito ---------------------------------------------------------------


@pytest.mark.parametrize("status", [ItemStatus.READ, ItemStatus.DISCARDED, ItemStatus.PUBLISHED])
async def test_relectura_sustituye_la_vigente_y_conserva_la_previa(status):
    item = _item(status)
    previous = _previous(item)
    env = _Env(item, previous)
    env.fake.respond(AgentRole.READER, json=_v3_json(), tokens_in=1200, tokens_out=300)

    result = await env.read_item.reread_with_measurements(item, previous)

    assert result.outcome is ReadOutcome.READ
    assert result.prompt_version == "reader-v3"
    new = result.reading
    assert new is not None and new.id != previous.id
    assert new.prompt_version == "reader-v3"
    assert new.measurements is not None and len(new.measurements) == 1
    assert env.readings.get_for_item(item.id) == new
    assert previous in env.readings.readings
    assert env.readings.with_measurements() == [new]
    assert item.status is status
    assert env.items.get(item.id).status is status
    assert len(env.fake.calls) == 1
    assert env.fake.calls[0].system_prompt == "sistema v3"
    assert len(env.agent_calls.calls) == 1
    call = env.agent_calls.calls[0]
    assert call.prompt_version == "reader-v3"
    assert call.item_id == item.id
    assert call.agent is AgentRole.READER


async def test_authorize_usa_la_estimacion_de_v3_no_la_de_v2():
    item = _item()
    previous = _previous(item)
    # 10.000 restantes: cabe la estimación v2 (6.000), no la v3 (13.000).
    env = _Env(item, previous, policy=_policy(nightly_tokens=10_000, editor_reserve_tokens=0))

    with pytest.raises(BudgetExceeded):
        await env.read_item.reread_with_measurements(item, previous)

    assert env.fake.calls == []
    assert env.agent_calls.calls == []


# --- 2. rechazos sin gasto --------------------------------------------------


def _refusal_cases():
    base = _item()
    other = _item()
    return [
        (_item(ItemStatus.NEW), None, RereadRefusal.STATUS_NOT_REREADABLE),
        (_item(ItemStatus.FAILED), None, RereadRefusal.STATUS_NOT_REREADABLE),
        (
            _item(categories=["astro-ph.GA"]),
            None,
            RereadRefusal.NOT_MEASUREMENT_ELIGIBLE,
        ),
        (_item(exoplanet_match=False), None, RereadRefusal.NOT_MEASUREMENT_ELIGIBLE),
        (_item(), {"measurements": ()}, RereadRefusal.ALREADY_HAS_MEASUREMENTS),
        (_item(), "con_medidas", RereadRefusal.ALREADY_HAS_MEASUREMENTS),
        (base, {"item_id": other.id}, RereadRefusal.READING_MISMATCH),
    ]


@pytest.mark.parametrize("idx", range(7))
async def test_rechazos_no_llaman_al_proveedor_ni_dejan_agentcall(idx):
    item, overrides, expected = _refusal_cases()[idx]
    if overrides == "con_medidas":
        first = _Env(item, _previous(item))
        first.fake.respond(AgentRole.READER, json=_v3_json(), tokens_in=10, tokens_out=10)
        previous = (
            await first.read_item.reread_with_measurements(item, first.readings.readings[0])
        ).reading
    else:
        previous = _previous(item, **(overrides or {}))
    env = _Env(item, previous)

    assert reread_refusal(item, previous, _CATEGORIES) is expected
    with pytest.raises(RereadRefused) as exc_info:
        await env.read_item.reread_with_measurements(item, previous)

    assert exc_info.value.reason is expected
    assert env.fake.calls == []
    assert env.agent_calls.calls == []
    assert env.readings.readings == [previous]


def test_reread_refusal_none_si_es_elegible():
    item = _item()
    assert reread_refusal(item, _previous(item), _CATEGORIES) is None


# --- 3. JSON inválido -------------------------------------------------------


@pytest.mark.parametrize("status", [ItemStatus.READ, ItemStatus.DISCARDED, ItemStatus.PUBLISHED])
async def test_json_invalido_dos_veces_no_cambia_nada_ni_marca_failed(status):
    item = _item(status)
    previous = _previous(item)
    env = _Env(item, previous)
    env.fake.respond(AgentRole.READER, raw="no es json", tokens_in=100, tokens_out=10)
    env.fake.respond(AgentRole.READER, raw="tampoco", tokens_in=100, tokens_out=10)

    result = await env.read_item.reread_with_measurements(item, previous)

    assert result.outcome is ReadOutcome.INVALID_OUTPUT
    assert result.reading is None
    assert result.attempts == 2
    assert len(env.agent_calls.calls) == 2
    assert all(c.status is AgentCallStatus.INVALID_OUTPUT for c in env.agent_calls.calls)
    assert item.status is status
    assert env.items.get(item.id).status is status
    assert env.readings.get_for_item(item.id) == previous
    assert env.readings.readings == [previous]


# --- 4. timeout / límite de tasa / error ------------------------------------


@pytest.mark.parametrize(
    ("error", "outcome", "status"),
    [
        (LLMTimeout("t"), ReadOutcome.TIMEOUT, AgentCallStatus.TIMEOUT),
        (LLMRateLimited("r"), ReadOutcome.RATE_LIMITED, AgentCallStatus.ERROR),
        (LLMError("e"), ReadOutcome.AGENT_ERROR, AgentCallStatus.ERROR),
    ],
)
async def test_fallos_del_proveedor_solo_dejan_agentcall(error, outcome, status):
    item = _item(ItemStatus.DISCARDED)
    previous = _previous(item)
    env = _Env(item, previous)
    env.fake.fail(AgentRole.READER, error=error)

    result = await env.read_item.reread_with_measurements(item, previous)

    assert result.outcome is outcome
    assert result.reading is None
    assert len(env.agent_calls.calls) == 1
    assert env.agent_calls.calls[0].status is status
    assert item.status is ItemStatus.DISCARDED
    assert env.readings.readings == [previous]
    assert env.readings.get_for_item(item.id) == previous


# --- 5. BudgetDenied se propaga ---------------------------------------------


async def test_fuera_de_ventana_se_propaga_sin_llamar():
    item = _item()
    previous = _previous(item)
    env = _Env(item, previous, now=_OUTSIDE)
    with pytest.raises(OutsideExecutionWindow):
        await env.read_item.reread_with_measurements(item, previous)
    assert env.fake.calls == [] and env.agent_calls.calls == []


async def test_presupuesto_agotado_se_propaga_sin_llamar():
    item = _item()
    previous = _previous(item)
    env = _Env(item, previous, policy=_policy(nightly_tokens=5_000, editor_reserve_tokens=0))
    with pytest.raises(BudgetExceeded):
        await env.read_item.reread_with_measurements(item, previous)
    assert env.fake.calls == [] and env.agent_calls.calls == []


async def test_tope_de_llamadas_se_propaga_antes_de_la_segunda_llamada():
    item = _item()
    previous = _previous(item)
    env = _Env(item, previous, policy=_policy(max_calls_per_item=1, max_items_per_night=1))
    env.fake.respond(AgentRole.READER, raw="roto", tokens_in=10, tokens_out=10)
    with pytest.raises(CallLimitReached) as exc_info:
        await env.read_item.reread_with_measurements(item, previous)
    assert isinstance(exc_info.value, BudgetDenied)
    assert len(env.fake.calls) == 1
    assert env.readings.readings == [previous]


# --- 6. camino normal informa prompt_version --------------------------------


async def test_camino_normal_v3_y_v2_informan_prompt_version():
    ep = _item(ItemStatus.NEW)
    env = _Env(ep, None)
    env.fake.respond(AgentRole.READER, json=_v3_json(), tokens_in=100, tokens_out=10)
    result = await env.read_item(ep)
    assert result.reading is not None and result.reading.prompt_version == "reader-v3"
    assert ep.status is ItemStatus.READ

    ga = _item(ItemStatus.NEW, categories=["astro-ph.GA"])
    env2 = _Env(ga, None)
    env2.fake.respond(
        AgentRole.READER,
        json={"summary": "r", "objects": [], "claims": ["c"], "interest_score": 2},
        tokens_in=100,
        tokens_out=10,
    )
    result2 = await env2.read_item(ga)
    assert result2.reading is not None and result2.reading.prompt_version == "reader-v2"
