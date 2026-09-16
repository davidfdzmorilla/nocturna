"""Tests de `application/use_cases/ingest_arxiv.py::IngestArxiv`.

Puros: fuente de arXiv falsa (`_FakeArxivSource`, cumple `ArxivSource`
estructuralmente) y repositorio en memoria o doble de `unittest.mock`, sin
base de datos. Ningún test de este fichero abre PostgreSQL ni toca la red.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any
from unittest.mock import Mock, call

import pytest

from nocturna.application.use_cases.ingest_arxiv import IngestArxiv
from nocturna.domain.entities import Item, ItemStatus
from nocturna.domain.repositories import ItemRepository
from nocturna.domain.sources import SourceFetch
from nocturna.infrastructure.config import load_pipeline_config

_SINCE = datetime(2026, 9, 1, tzinfo=UTC)
_CATEGORIES = ("astro-ph.EP", "astro-ph.GA")
_FETCHED_AT = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)


def _make_item(external_id: str, *, source: str = "arxiv") -> Item:
    return Item(
        source=source,
        external_id=external_id,
        title=f"Título de {external_id}",
        abstract="Abstract de prueba.",
        categories=["astro-ph.EP"],
        published_at=_FETCHED_AT,
        fetched_at=_FETCHED_AT,
    )


class _FakeArxivSource:
    """Cumple `ArxivSource` estructuralmente: no hereda de él."""

    def __init__(self, fetch: SourceFetch) -> None:
        self._fetch = fetch
        self.calls: list[dict[str, Any]] = []

    async def fetch_new(
        self, *, since: datetime, categories: Sequence[str], max_results: int
    ) -> SourceFetch:
        self.calls.append(
            {"since": since, "categories": list(categories), "max_results": max_results}
        )
        return self._fetch


class _InMemoryItemRepository:
    """Doble sencillo con la misma semántica de deduplicación que
    `SqlAlchemyItemRepository.add_many`: por `(source, external_id)`."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], Item] = {}

    def add_many(self, items: list[Item]) -> int:
        inserted = 0
        for item in items:
            key = (item.source, item.external_id)
            if key not in self._items:
                self._items[key] = item
                inserted += 1
        return inserted

    def get(self, item_id):  # pragma: no cover - no usado por IngestArxiv
        raise NotImplementedError

    def next_unread(self, limit: int) -> list[Item]:
        return list(self._items.values())[:limit]

    def save(self, item: Item) -> None:  # pragma: no cover - no usado por IngestArxiv
        raise NotImplementedError


@pytest.mark.anyio
async def test_persiste_los_items_con_status_new() -> None:
    items = [_make_item("2609.00001"), _make_item("2609.00002")]
    assert all(item.status == ItemStatus.NEW for item in items)
    source = _FakeArxivSource(SourceFetch(items=items, truncated=False, skipped=0))
    repo = _InMemoryItemRepository()
    use_case = IngestArxiv(source, repo)

    await use_case(since=_SINCE, categories=_CATEGORIES, max_results=100)

    stored = repo.next_unread(limit=10)
    assert len(stored) == 2
    assert all(item.status == ItemStatus.NEW for item in stored)


@pytest.mark.anyio
async def test_segunda_ejecucion_con_las_mismas_entradas_da_new_cero_y_duplicates_n() -> None:
    items = [_make_item("2609.00001"), _make_item("2609.00002"), _make_item("2609.00003")]
    source = _FakeArxivSource(SourceFetch(items=items, truncated=False, skipped=0))
    repo = _InMemoryItemRepository()
    use_case = IngestArxiv(source, repo)

    first = await use_case(since=_SINCE, categories=_CATEGORIES, max_results=100)
    second = await use_case(since=_SINCE, categories=_CATEGORIES, max_results=100)

    assert first.new == 3
    assert first.duplicates == 0
    assert second.new == 0
    assert second.duplicates == 3


@pytest.mark.anyio
async def test_los_contadores_de_ingestresult_cuadran() -> None:
    """`fetched`, `new`, `duplicates`, `skipped` y `truncated` cuadran entre sí."""
    already_stored = _make_item("2609.00001")
    fresh_items = [already_stored, _make_item("2609.00002"), _make_item("2609.00003")]
    source = _FakeArxivSource(SourceFetch(items=fresh_items, truncated=True, skipped=2))
    repo = _InMemoryItemRepository()
    repo.add_many([already_stored])
    use_case = IngestArxiv(source, repo)

    result = await use_case(since=_SINCE, categories=_CATEGORIES, max_results=100)

    assert result.fetched == 3
    assert result.new == 2
    assert result.duplicates == 1
    assert result.skipped == 2
    assert result.truncated is True
    assert result.items == fresh_items


@pytest.mark.anyio
async def test_con_50_entradas_y_max_items_per_night_en_40_se_persisten_las_50() -> None:
    """El tope `limits.max_items_per_night` (T44, selección de qué se
    procesa esta noche) no debe aplicarse aquí. Si la ingesta recortara,
    los papers sobrantes se perderían para siempre: `since` avanza en la
    siguiente ejecución y nadie vuelve a pedirlos. No "arreglar" este test
    en T30 ni en T44 haciendo que falle con menos de 50: el tope ya vive
    donde debe, en `ItemRepository.next_unread`.
    """
    max_items_per_night = load_pipeline_config().limits.max_items_per_night
    assert max_items_per_night < 50, "el test deja de probar nada si el tope de config sube a 50+"

    fifty_items = [_make_item(f"2609.{i:05d}") for i in range(50)]
    source = _FakeArxivSource(SourceFetch(items=fifty_items, truncated=False, skipped=0))
    repo = _InMemoryItemRepository()
    use_case = IngestArxiv(source, repo)

    result = await use_case(since=_SINCE, categories=_CATEGORIES, max_results=1000)

    assert result.new == 50
    assert len(repo.next_unread(limit=1000)) == 50


@pytest.mark.anyio
async def test_ingest_arxiv_no_abre_ni_cierra_transaccion_solo_llama_a_add_many() -> None:
    """`IngestArxiv` no es la frontera transaccional (ver docstring del
    módulo): un repositorio falso que registra cada llamada recibida no
    debe ver nada aparte de una única llamada a `add_many`."""
    items = [_make_item("2609.00001")]
    source = _FakeArxivSource(SourceFetch(items=items, truncated=False, skipped=0))
    repo = Mock(spec=ItemRepository)
    repo.add_many.return_value = 1
    use_case = IngestArxiv(source, repo)

    await use_case(since=_SINCE, categories=_CATEGORIES, max_results=100)

    assert repo.method_calls == [call.add_many(items)]
