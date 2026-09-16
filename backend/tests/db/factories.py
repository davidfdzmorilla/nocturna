"""Constructores de entidades con valores por defecto válidos, para `tests/db/`.

Cada `make_*` acepta `**overrides` para que un test solo declare el campo
que le importa. Los `datetime` por defecto son *aware* (UTC), como exige
`domain/entities.py`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

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
from nocturna.domain.llm import AgentRole

_EPOCH = datetime(2026, 1, 1, tzinfo=UTC)


def aware(offset_minutes: int = 0) -> datetime:
    return _EPOCH + timedelta(minutes=offset_minutes)


def make_item(**overrides: object) -> Item:
    defaults: dict[str, object] = {
        "source": "arxiv",
        "external_id": "2601.00001",
        "title": "A test paper about neutron stars",
        "abstract": "This paper studies something interesting.",
        "categories": ["astro-ph.EP"],
        "published_at": aware(),
        "fetched_at": aware(1),
        "status": ItemStatus.NEW,
    }
    defaults.update(overrides)
    return Item(**defaults)


def make_reading(item_id: UUID, **overrides: object) -> Reading:
    defaults: dict[str, object] = {
        "item_id": item_id,
        "summary": "Resumen de prueba.",
        "objects": ["Betelgeuse"],
        "claims": ["El objeto es interesante."],
        "interest_score": 4,
        "tokens_in": 100,
        "tokens_out": 50,
        "model": "claude-sonnet-test",
    }
    defaults.update(overrides)
    return Reading(**defaults)


def make_finding(item_id: UUID, run_id: UUID, **overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "item_id": item_id,
        "run_id": run_id,
        "type": FindingType.PAPER_EXPLAINED,
        "title": "Un hallazgo de prueba",
        "level_curious": "Nivel curioso.",
        "level_amateur": "Nivel aficionado.",
        "level_technical": "Nivel técnico.",
    }
    defaults.update(overrides)
    return Finding(**defaults)


def make_run(**overrides: object) -> Run:
    defaults: dict[str, object] = {
        "started_at": aware(),
        "budget_tokens": 300_000,
        "status": RunStatus.RUNNING,
    }
    defaults.update(overrides)
    return Run(**defaults)


def make_agent_call(run_id: UUID, **overrides: object) -> AgentCall:
    defaults: dict[str, object] = {
        "run_id": run_id,
        "item_id": None,
        "agent": AgentRole.READER,
        "model": "claude-sonnet-test",
        "tokens_in": 100,
        "tokens_out": 50,
        "duration_ms": 1200,
        "status": AgentCallStatus.OK,
    }
    defaults.update(overrides)
    return AgentCall(**defaults)
