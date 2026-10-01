"""`RunNight` lee los ítems en el orden priorizado por `next_unread` (T79):
los marcados `exoplanet_match` primero, luego `fetched_at`, luego `external_id`."""

from __future__ import annotations

from datetime import timedelta

import pytest
from fakes.llm import FakeLLMProvider
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    make_item,
    make_policy,
    make_run_night,
    valid_reading_json,
)

from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio


def _ingest_result(items: list) -> IngestResult:
    return IngestResult(
        fetched=len(items), new=len(items), duplicates=0, skipped=0, truncated=False, items=items
    )


async def test_la_fase_de_lectura_lee_primero_los_marcados_y_luego_por_fetched_at_y_external_id():
    plain_old = make_item(external_id="2501.900001", fetched_at=WITHIN_WINDOW)
    plain_new = make_item(
        external_id="2501.900002", fetched_at=WITHIN_WINDOW + timedelta(minutes=5)
    )
    exo_late = make_item(
        external_id="2501.900003",
        fetched_at=WITHIN_WINDOW + timedelta(minutes=9),
        exoplanet_match=True,
    )
    exo_early_b = make_item(
        external_id="2501.900005", fetched_at=WITHIN_WINDOW, exoplanet_match=True
    )
    exo_early_a = make_item(
        external_id="2501.900004", fetched_at=WITHIN_WINDOW, exoplanet_match=True
    )
    items = [plain_old, plain_new, exo_late, exo_early_b, exo_early_a]
    env = Environment(items=items, policy=make_policy(), now=WITHIN_WINDOW)
    fake = FakeLLMProvider()
    for _ in items:
        fake.respond(
            AgentRole.READER,
            json=valid_reading_json(interest_score=1),
            tokens_in=100,
            tokens_out=10,
        )
    run_night = make_run_night(env=env, provider=fake, ingest_result=_ingest_result(items))

    await run_night()

    read_order = [c.item_id for c in fake.calls if c.role is AgentRole.READER]
    assert read_order == [
        exo_early_a.id,
        exo_early_b.id,
        exo_late.id,
        plain_old.id,
        plain_new.id,
    ]


async def test_con_limite_los_marcados_entran_antes_que_los_no_marcados():
    plain = [make_item(external_id=f"2501.91000{i}") for i in range(3)]
    exo = make_item(external_id="2501.919999", exoplanet_match=True)
    items = [*plain, exo]
    env = Environment(items=items, policy=make_policy(), now=WITHIN_WINDOW)
    fake = FakeLLMProvider()
    for _ in range(2):
        fake.respond(
            AgentRole.READER,
            json=valid_reading_json(interest_score=1),
            tokens_in=100,
            tokens_out=10,
        )
    run_night = make_run_night(
        env=env, provider=fake, ingest_result=_ingest_result(items), max_items=2
    )

    await run_night()

    read_order = [c.item_id for c in fake.calls if c.role is AgentRole.READER]
    assert read_order == [exo.id, plain[0].id]
