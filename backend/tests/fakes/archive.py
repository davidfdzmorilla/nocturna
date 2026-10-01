"""`ArchiveRepository` en memoria, para los tests del caso de uso del snapshot (T81).

Replica la semantica de `SqlAlchemyArchiveRepository` (altas, bajas,
reactivaciones, `is_default_current`) sin PostgreSQL. Los tests de `-m db`
fijan la semantica real; este doble solo debe coincidir con ella.
"""

from __future__ import annotations

from collections.abc import Collection, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from uuid import UUID

from nocturna.domain.archive import (
    ArchiveSnapshot,
    ArchiveSolution,
    DefaultChange,
    SnapshotDiff,
    SnapshotKind,
)


@dataclass
class _Stored:
    solution: ArchiveSolution
    first_seen: UUID
    last_seen: UUID
    is_default_current: bool = False
    removed_at: datetime | None = None


class InMemoryArchiveRepository:
    def __init__(self) -> None:
        self.snapshots: list[ArchiveSnapshot] = []
        self.stored: dict[str, _Stored] = {}
        self.default_changes: list[tuple[UUID, datetime, DefaultChange]] = []

    def last_snapshot(self) -> ArchiveSnapshot | None:
        return max(self.snapshots, key=lambda s: s.taken_at, default=None)

    def last_full_snapshot(self) -> ArchiveSnapshot | None:
        fulls = [s for s in self.snapshots if s.kind is SnapshotKind.FULL]
        return max(fulls, key=lambda s: s.taken_at, default=None)

    def active_keys(self, planets: Collection[str] | None = None) -> dict[str, str]:
        wanted = None if planets is None else set(planets)
        return {
            key: st.solution.pl_name
            for key, st in self.stored.items()
            if st.removed_at is None and (wanted is None or st.solution.pl_name in wanted)
        }

    def removed_keys(self, keys: Collection[str]) -> frozenset[str]:
        return frozenset(
            k for k in keys if k in self.stored and self.stored[k].removed_at is not None
        )

    def current_defaults(self) -> dict[str, str]:
        return {
            st.solution.pl_name: key for key, st in self.stored.items() if st.is_default_current
        }

    def save_snapshot(
        self,
        snapshot: ArchiveSnapshot,
        solutions: Sequence[ArchiveSolution],
        diff: SnapshotDiff,
    ) -> None:
        self.snapshots.append(snapshot)
        for sol in solutions:
            key = sol.solution_key
            existing = self.stored.get(key)
            if existing is None:
                self.stored[key] = _Stored(sol, snapshot.id, snapshot.id)
            else:
                existing.solution = replace(
                    sol, pl_name=existing.solution.pl_name, ref_key=existing.solution.ref_key
                )
                existing.last_seen = snapshot.id
                existing.removed_at = None
        for key in diff.removed:
            st = self.stored[key]
            st.removed_at = snapshot.taken_at
            st.is_default_current = False
        stale = {c.pl_name for c in diff.default_changes} | set(diff.lost_defaults)
        for st in self.stored.values():
            if st.solution.pl_name in stale:
                st.is_default_current = False
        for sol in solutions:
            if sol.is_default:
                self.stored[sol.solution_key].is_default_current = True
        for change in diff.default_changes:
            self.default_changes.append((snapshot.id, snapshot.taken_at, change))
