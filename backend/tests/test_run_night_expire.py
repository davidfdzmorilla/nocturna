"""`RunNight` caduca la cola vieja antes del Reader (T94, ADR 0033)."""

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

from nocturna.application.use_cases import run_night as run_night_module
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.domain.entities import ItemStatus, RunStatus
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio


def _ingest(items: list) -> IngestResult:
    return IngestResult(
        fetched=len(items), new=len(items), duplicates=0, skipped=0, truncated=False, items=items
    )


def _fake(n: int) -> FakeLLMProvider:
    fake = FakeLLMProvider()
    for _ in range(n):
        fake.respond(
            AgentRole.READER,
            json=valid_reading_json(interest_score=1),
            tokens_in=100,
            tokens_out=10,
        )
    return fake


async def test_el_viejo_no_se_lee_ni_genera_agent_call_y_notes_cuenta_expired():
    old = make_item(published_at=WITHIN_WINDOW - timedelta(days=8))
    fresh = make_item(published_at=WITHIN_WINDOW - timedelta(days=1))
    env = Environment(items=[old, fresh], policy=make_policy(), now=WITHIN_WINDOW)
    fake = _fake(1)
    night = make_run_night(env=env, provider=fake, ingest_result=_ingest([old, fresh]))

    result = await night()

    assert old.status is ItemStatus.EXPIRED
    assert [c.item_id for c in fake.calls if c.role is AgentRole.READER] == [fresh.id]
    assert all(call.item_id != old.id for call in env.agent_calls.calls)
    assert "| expired=1 |" in result.notes
    assert (
        result.notes.index("ingest=")
        < result.notes.index("expired=1")
        < result.notes.index("read=")
    )
    assert result.items_read == 1


async def test_sin_caducados_notes_lleva_expired_cero():
    item = make_item(published_at=WITHIN_WINDOW)
    env = Environment(items=[item], policy=make_policy(), now=WITHIN_WINDOW)
    night = make_run_night(env=env, provider=_fake(1), ingest_result=_ingest([item]))

    result = await night()

    assert "| expired=0 |" in result.notes


async def test_el_exoplanet_match_viejo_se_lee(monkeypatch):
    exo = make_item(published_at=WITHIN_WINDOW - timedelta(days=90), exoplanet_match=True)
    env = Environment(items=[exo], policy=make_policy(), now=WITHIN_WINDOW)
    fake = _fake(1)
    night = make_run_night(env=env, provider=fake, ingest_result=_ingest([exo]))

    result = await night()

    assert result.items_read == 1
    assert "| expired=0 |" in result.notes


async def test_si_la_caducidad_falla_degrada_a_partial_y_el_reader_corre(monkeypatch):
    item = make_item(published_at=WITHIN_WINDOW)
    env = Environment(items=[item], policy=make_policy(), now=WITHIN_WINDOW)

    def _boom(*_a: object, **_k: object):
        raise RuntimeError("fallo simulado")

    monkeypatch.setattr(run_night_module, "expire_stale_unread", _boom)
    night = make_run_night(env=env, provider=_fake(1), ingest_result=_ingest([item]))

    result = await night()

    assert result.status is RunStatus.PARTIAL
    assert result.items_read == 1
    assert "expired=0:RuntimeError" in result.notes
    assert "end=expire_error" in result.notes


async def test_el_contador_se_reinicia_entre_llamadas():
    old = make_item(published_at=WITHIN_WINDOW - timedelta(days=8))
    env = Environment(items=[old], policy=make_policy(), now=WITHIN_WINDOW)
    night = make_run_night(env=env, provider=_fake(0), ingest_result=_ingest([old]))

    first = await night()
    second = await night()

    assert "| expired=1 |" in first.notes
    assert "| expired=0 |" in second.notes
