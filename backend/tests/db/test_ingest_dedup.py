"""Deduplicación de `IngestArxiv` contra PostgreSQL real.

Complementa `tests/test_ingest_arxiv.py` (puro): aquí `ArxivClient` sirve un
fixture Atom grabado a través de `httpx.MockTransport` (nunca red real) y
`IngestArxiv` corre con `SqlAlchemyItemRepository` dentro del `unit_of_work`
real de `infrastructure/db/session.py`, dos veces en dos transacciones
separadas, para comprobar la deduplicación que solo la base de datos puede
demostrar de verdad.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nocturna.application.use_cases.ingest_arxiv import IngestArxiv
from nocturna.domain.entities import ItemStatus
from nocturna.infrastructure.arxiv.client import ArxivClient
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.arxiv.retry import RetryPolicy
from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository
from nocturna.infrastructure.db.session import unit_of_work

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"
_FETCHED_AT = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
_VERY_OLD_SINCE = datetime(2000, 1, 1, tzinfo=UTC)
_CATEGORIES = ("astro-ph.EP", "astro-ph.GA")


class _IncreasingClock:
    """`monotonic()` siempre salta hacia delante: `RateLimiter.acquire()`
    nunca tiene que esperar de verdad en este test."""

    def __init__(self) -> None:
        self._value = 0.0

    def __call__(self) -> float:
        value = self._value
        self._value += 1000.0
        return value


async def _no_sleep(seconds: float) -> None:
    return None


def _build_client() -> tuple[ArxivClient, httpx.AsyncClient]:
    payload = (FIXTURES_DIR / "feed_three_entries.xml").read_bytes()

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=payload)

    http = httpx.AsyncClient(transport=httpx.MockTransport(handle), timeout=10.0)
    limiter = RateLimiter(3.0, sleep=_no_sleep, monotonic=_IncreasingClock())
    # Un solo intento: este fichero prueba deduplicación, no reintentos
    # (ver `tests/test_arxiv_client.py` para esos).
    retry_policy = RetryPolicy(max_attempts=1, base_delay_s=1.0, max_elapsed_s=1.0)
    client = ArxivClient(
        http, limiter=limiter, page_size=10, now=lambda: _FETCHED_AT, retry_policy=retry_policy
    )
    return client, http


@pytest.mark.anyio
async def test_ingerir_feed_three_entries_dos_veces_deja_tres_filas_en_items(
    db_session_factory,
) -> None:
    client, http = _build_client()

    async with http:
        with unit_of_work(db_session_factory) as session:
            first = await IngestArxiv(client, SqlAlchemyItemRepository(session))(
                since=_VERY_OLD_SINCE, categories=_CATEGORIES, max_results=100
            )

        with unit_of_work(db_session_factory) as session:
            second = await IngestArxiv(client, SqlAlchemyItemRepository(session))(
                since=_VERY_OLD_SINCE, categories=_CATEGORIES, max_results=100
            )

    assert first.new == 3
    assert second.new == 0

    with unit_of_work(db_session_factory) as session:
        stored = SqlAlchemyItemRepository(session).next_unread(limit=10)

    assert len(stored) == 3
    assert {item.status for item in stored} == {ItemStatus.NEW}
    for item in stored:
        assert item.published_at.tzinfo is not None
