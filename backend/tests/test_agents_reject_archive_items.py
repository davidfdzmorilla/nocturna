"""Defensa en profundidad (T85): ningún agente lee ni divulga ítems del archivo.

`ReadItem`, su relectura y `PopularizeReading` lanzan `InvariantViolation` si
`item.source != 'arxiv'`, antes de autorizar o llamar al LLM. Además, el
`InMemoryItemRepository.next_unread` replica el contrato "solo arxiv".
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryFindingRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    make_work_factory,
)

from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.use_cases.popularize_reading import PopularizeReading
from nocturna.application.use_cases.read_item import ReaderPrompt, ReadItem
from nocturna.domain.entities import (
    ITEM_SOURCE_EXOPLANET_ARCHIVE,
    Item,
    ItemStatus,
    Reading,
    Run,
)
from nocturna.domain.errors import InvariantViolation

pytestmark = pytest.mark.anyio

_NOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)


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


def _item(*, source: str = ITEM_SOURCE_EXOPLANET_ARCHIVE, status: ItemStatus = ItemStatus.NEW):
    return Item(
        source=source,
        external_id="X-1",
        title="t",
        abstract="a",
        categories=["astro-ph.EP"],
        published_at=_NOW,
        fetched_at=_NOW,
        status=status,
    )


def _reading(item: Item) -> Reading:
    return Reading(
        item_id=item.id,
        summary="r",
        objects=("x",),
        claims=("c",),
        interest_score=5,
        tokens_in=1,
        tokens_out=1,
        model="m",
        measurements=None,
        prompt_version="reader-v2",
    )


def _env(item: Item):
    policy = _policy()
    run = Run(started_at=_NOW, budget_tokens=policy.nightly_tokens)
    runs = InMemoryRunRepository(run)
    calls = InMemoryAgentCallRepository()
    guard = BudgetGuard(
        run_id=run.id, policy=policy, runs=runs, agent_calls=calls, clock=FakeClock(_NOW)
    )
    work = make_work_factory(
        guard=guard,
        runs=runs,
        items=InMemoryItemRepository(item),
        readings=InMemoryReadingRepository(),
        findings=InMemoryFindingRepository(),
        agent_calls=calls,
    )
    return work, calls


def _read_item(work, fake: FakeLLMProvider) -> ReadItem:
    return ReadItem(
        work=work,
        provider=fake,
        model="m",
        max_turns=3,
        max_attempts=2,
        base=ReaderPrompt("v2", "reader-v2", 6_000),
        measures=ReaderPrompt("v3", "reader-v3", 13_000),
        measures_categories=frozenset({"astro-ph.EP"}),
    )


async def test_read_item_rechaza_item_del_archivo_sin_llamar_al_llm():
    item = _item()
    work, calls = _env(item)
    fake = FakeLLMProvider()
    with pytest.raises(InvariantViolation):
        await _read_item(work, fake)(item)
    assert fake.calls == []
    assert calls.calls == []


async def test_relectura_rechaza_item_del_archivo_sin_llamar_al_llm():
    item = _item(status=ItemStatus.READ)
    work, calls = _env(item)
    fake = FakeLLMProvider()
    with pytest.raises(InvariantViolation):
        await _read_item(work, fake).reread_with_measurements(item, _reading(item))
    assert fake.calls == []
    assert calls.calls == []


async def test_popularize_rechaza_item_del_archivo_sin_llamar_al_llm():
    item = _item(status=ItemStatus.READ)
    work, calls = _env(item)
    fake = FakeLLMProvider()
    popularize = PopularizeReading(
        work=work,
        provider=fake,
        system_prompt="s",
        prompt_version="popularizer-v1",
        model="m",
        max_turns=3,
        estimated_tokens=100,
        max_attempts=1,
        min_interest_score=4,
    )
    with pytest.raises(InvariantViolation):
        await popularize(item=item, reading=_reading(item))
    assert fake.calls == []
    assert calls.calls == []


def test_next_unread_del_fake_solo_devuelve_arxiv():
    arxiv = _item(source="arxiv")
    archive = _item()
    repo = InMemoryItemRepository(arxiv)
    repo.save(archive)
    assert [i.id for i in repo.next_unread(10)] == [arxiv.id]
