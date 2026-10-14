"""Dobles en memoria de T85: `SolutionTensionRepository` y `ArchiveItemRepository`.

Replican la semántica de los repositorios SQLAlchemy: clave única
(new_solution_key, parameter) y `set_item` que falla si el id no existe.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import UUID

from nocturna.domain.entities import Item, MeasuredParameter
from nocturna.domain.solution_tension import SolutionTension


class InMemorySolutionTensionRepository:
    def __init__(self) -> None:
        self._by_id: dict[UUID, SolutionTension] = {}

    def add(self, tension: SolutionTension) -> None:
        if tension.id in self._by_id:
            raise ValueError(f"ya existe SolutionTension con id={tension.id}")
        if (tension.new_solution_key, tension.parameter) in self.keys_computed():
            raise ValueError("ya existe una tensión para (alta, parámetro)")
        self._by_id[tension.id] = tension

    def get(self, tension_id: UUID) -> SolutionTension | None:
        return self._by_id.get(tension_id)

    def all(self) -> list[SolutionTension]:
        return sorted(self._by_id.values(), key=lambda t: (t.computed_at, str(t.id)))

    def keys_computed(self) -> frozenset[tuple[str, MeasuredParameter]]:
        return frozenset((t.new_solution_key, t.parameter) for t in self._by_id.values())

    def set_item(self, tension_id: UUID, item_id: UUID) -> None:
        if tension_id not in self._by_id:
            raise LookupError(f"no existe SolutionTension con id={tension_id}")
        self._by_id[tension_id] = replace(self._by_id[tension_id], item_id=item_id)


class InMemoryArchiveItemRepository:
    def __init__(self) -> None:
        self.items: dict[tuple[str, str], Item] = {}

    def get_by_external_id(self, source: str, external_id: str) -> Item | None:
        return self.items.get((source, external_id))

    def add_many(self, items: list[Item]) -> int:
        added = 0
        for item in items:
            key = (item.source, item.external_id)
            if key not in self.items:
                self.items[key] = item
                added += 1
        return added
