"""`IngestArxiv` marca `Item.exoplanet_match` según el `ExoplanetFilter` (T79).

Fuente y repositorio en memoria; sin red ni base de datos.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

import pytest

from nocturna.application.use_cases.ingest_arxiv import IngestArxiv
from nocturna.domain.entities import Item
from nocturna.domain.exoplanet_filter import ExoplanetFilter
from nocturna.domain.sources import SourceFetch

pytestmark = pytest.mark.anyio

_SINCE = datetime(2026, 9, 1, tzinfo=UTC)
_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

_FILTER = ExoplanetFilter(keywords=("exoplanet",), designation_patterns=(r"\bTOI-\d+",))


def _item(external_id: str, title: str, abstract: str = "Texto neutro.") -> Item:
    return Item(
        source="arxiv",
        external_id=external_id,
        title=title,
        abstract=abstract,
        categories=["astro-ph.EP"],
        published_at=_NOW,
        fetched_at=_NOW,
    )


class _Source:
    def __init__(self, items: list[Item]) -> None:
        self._items = items

    async def fetch_new(
        self, *, since: datetime, categories: Sequence[str], max_results: int
    ) -> SourceFetch:
        return SourceFetch(items=self._items, truncated=False, skipped=0)


class _Repo:
    def __init__(self) -> None:
        self.stored: dict[str, Item] = {}

    def add_many(self, items: list[Item]) -> int:
        new = 0
        for item in items:
            if item.external_id not in self.stored:
                self.stored[item.external_id] = item
                new += 1
        return new


async def test_la_ingesta_marca_los_items_segun_el_filtro():
    items = [
        _item("2609.00001", "Mass of TOI-6981 b"),
        _item("2609.00002", "Galaxy clusters", "We detect an exoplanet by accident."),
        _item("2609.00003", "Galaxy clusters"),
    ]
    repo = _Repo()

    await IngestArxiv(_Source(items), repo, _FILTER)(
        since=_SINCE, categories=("astro-ph.EP",), max_results=10
    )

    assert {i: it.exoplanet_match for i, it in repo.stored.items()} == {
        "2609.00001": True,  # por designación en el título
        "2609.00002": True,  # por keyword en el abstract
        "2609.00003": False,
    }


async def test_un_filtro_vacio_no_marca_ningun_item():
    empty = ExoplanetFilter(keywords=(), designation_patterns=())
    repo = _Repo()

    await IngestArxiv(_Source([_item("2609.00001", "exoplanet TOI-1 b")]), repo, empty)(
        since=_SINCE, categories=("astro-ph.EP",), max_results=10
    )

    assert repo.stored["2609.00001"].exoplanet_match is False


async def test_la_marca_llega_al_add_many_ya_calculada():
    seen: list[bool] = []

    class _SpyRepo(_Repo):
        def add_many(self, items: list[Item]) -> int:
            seen.extend(i.exoplanet_match for i in items)
            return super().add_many(items)

    await IngestArxiv(
        _Source([_item("2609.00001", "TOI-5 b"), _item("2609.00002", "Stars")]), _SpyRepo(), _FILTER
    )(since=_SINCE, categories=("astro-ph.EP",), max_results=10)

    assert seen == [True, False]
