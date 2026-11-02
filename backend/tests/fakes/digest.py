"""Dobles en memoria del redactor del resumen semanal (T84.r).

Sin ningun import nuevo de `nocturna` (solo dominio ya existente): se pueden
importar desde `helpers/run_night.py` sin romper los tests previos a T84.r.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from nocturna.domain.archive_digest import iso_week_of


class InMemoryArchiveDigestTextRepository:
    """Cumple `domain.repositories.ArchiveDigestTextRepository` (get / all / add)."""

    def __init__(self) -> None:
        self._texts: dict[str, Any] = {}

    def get(self, week: str) -> Any | None:
        return self._texts.get(week)

    def all(self) -> dict[str, Any]:
        return dict(self._texts)

    def add(self, text: Any) -> None:
        if text.week in self._texts:
            raise ValueError(f"ya existe un texto para la semana {text.week}")
        self._texts[text.week] = text


class FakeDigestReader:
    """`ArchiveDigestReader` en memoria y mutable: el test rellena `snapshots`,
    `transitions` y `solutions` despues de construirlo."""

    def __init__(self) -> None:
        self.snapshots: list[datetime] = []
        self.transitions: list[Any] = []
        self.solutions: dict[str, Any] = {}

    def snapshot_weeks(self, tz):
        counts: dict[str, int] = {}
        for at in self.snapshots:
            week = iso_week_of(at, tz)
            counts[week] = counts.get(week, 0) + 1
        return sorted(counts.items())

    def transitions_between(self, start, end):
        return [t for t in self.transitions if start <= t.snapshot_taken_at < end]

    def solutions_by_key(self, keys):
        wanted = set(keys)
        return {k: v for k, v in self.solutions.items() if k in wanted}
