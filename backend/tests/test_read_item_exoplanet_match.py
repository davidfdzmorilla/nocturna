"""Selección de variante del Reader según `Item.exoplanet_match` (T79).

`reader-v3` solo si el ítem está marcado Y su categoría está en
`measures_categories`. Se comprueba la estimación que llega a
`BudgetGuard.authorize` (espía sobre el guard real). `FakeLLMProvider`, sin
Claude ni base de datos.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

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

from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.use_cases.read_item import ReaderPrompt, ReadItem
from nocturna.domain.entities import Item, Run
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_V2_ESTIMATE = 6_000
_V3_ESTIMATE = 13_000


class _SpyGuard(BudgetGuard):
    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        self.authorized: list[tuple[AgentRole, int]] = []

    def authorize(self, role: AgentRole, estimated_tokens: int) -> None:
        self.authorized.append((role, estimated_tokens))
        super().authorize(role, estimated_tokens)


def _policy() -> BudgetPolicy:
    return BudgetPolicy(
        nightly_tokens=100_000,
        editor_reserve_tokens=10_000,
        writer_reserve_tokens=0,
        max_items_per_night=10,
        max_turns_per_agent=3,
        max_editor_calls_per_night=2,
        max_writer_calls_per_night=0,
        max_calls_per_item=5,
        item_timeout_s=180,
        editor_timeout_s=300,
        run_timeout_s=16_200,
        window_start=time(0, 0),
        window_hard_stop=time(4, 45),
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _item(*, categories: list[str], exoplanet_match: bool) -> Item:
    return Item(
        source="arxiv",
        external_id="2609.00001",
        title="Título de prueba",
        abstract=(
            "The planet Kepler-0000 b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
        ),
        categories=categories,
        published_at=_NOW,
        fetched_at=_NOW,
        exoplanet_match=exoplanet_match,
    )


def _v2_json() -> dict:
    return {"summary": "r", "objects": ["x"], "claims": ["c"], "interest_score": 3}


def _v3_json() -> dict:
    return {
        **_v2_json(),
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


async def _read(item: Item, *, measures_categories: frozenset[str] = frozenset({"astro-ph.EP"})):
    policy = _policy()
    run = Run(started_at=_NOW, budget_tokens=policy.nightly_tokens)
    runs = InMemoryRunRepository(run)
    calls = InMemoryAgentCallRepository()
    guard = _SpyGuard(
        run_id=run.id, policy=policy, runs=runs, agent_calls=calls, clock=FakeClock(_NOW)
    )
    work = make_work_factory(
        guard=guard,
        runs=runs,
        items=InMemoryItemRepository(item),
        readings=InMemoryReadingRepository(),
        agent_calls=calls,
    )
    fake = FakeLLMProvider()
    # El Reader v3 exige measurements; el v2 las ignora al validar.
    use_v3 = item.exoplanet_match and not measures_categories.isdisjoint(item.categories)
    fake.respond(
        AgentRole.READER, json=_v3_json() if use_v3 else _v2_json(), tokens_in=100, tokens_out=10
    )
    read_item = ReadItem(
        work=work,
        provider=fake,
        model="claude-sonnet-test",
        max_turns=3,
        max_attempts=2,
        base=ReaderPrompt("sistema v2", "reader-v2", _V2_ESTIMATE),
        measures=ReaderPrompt("sistema v3", "reader-v3", _V3_ESTIMATE),
        measures_categories=measures_categories,
    )
    result = await read_item(item)
    return result, guard, fake, calls


async def test_ep_con_match_usa_reader_v3_y_autoriza_con_la_estimacion_v3():
    result, guard, fake, calls = await _read(
        _item(categories=["astro-ph.EP"], exoplanet_match=True)
    )

    assert result.prompt_version == "reader-v3"
    assert calls.calls[0].prompt_version == "reader-v3"
    assert fake.calls[0].system_prompt == "sistema v3"
    assert guard.authorized == [(AgentRole.READER, _V3_ESTIMATE)]


async def test_ep_sin_match_usa_reader_v2_y_autoriza_con_la_estimacion_v2():
    result, guard, fake, calls = await _read(
        _item(categories=["astro-ph.EP"], exoplanet_match=False)
    )

    assert result.prompt_version == "reader-v2"
    assert calls.calls[0].prompt_version == "reader-v2"
    assert fake.calls[0].system_prompt == "sistema v2"
    assert result.reading.measurements is None
    assert guard.authorized == [(AgentRole.READER, _V2_ESTIMATE)]


async def test_no_ep_con_match_usa_reader_v2_y_la_estimacion_v2():
    result, guard, _fake, _calls = await _read(
        _item(categories=["astro-ph.GA"], exoplanet_match=True)
    )

    assert result.prompt_version == "reader-v2"
    assert guard.authorized == [(AgentRole.READER, _V2_ESTIMATE)]


async def test_measurement_categories_vacio_usa_siempre_v2_aunque_haya_match():
    result, guard, _fake, _calls = await _read(
        _item(categories=["astro-ph.EP"], exoplanet_match=True),
        measures_categories=frozenset(),
    )

    assert result.prompt_version == "reader-v2"
    assert guard.authorized == [(AgentRole.READER, _V2_ESTIMATE)]


async def test_categoria_ep_secundaria_con_match_usa_v3():
    result, guard, _fake, _calls = await _read(
        _item(categories=["astro-ph.GA", "astro-ph.EP"], exoplanet_match=True)
    )

    assert result.prompt_version == "reader-v3"
    assert guard.authorized == [(AgentRole.READER, _V3_ESTIMATE)]
