"""Casos de uso de lectura del resumen semanal de cambios de referencia (T84).

Sin LLM ni escritura: orquestan `ArchiveDigestReader` y las reglas puras de
`domain/archive_digest.py`.
"""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from nocturna.domain.archive_digest import (
    TransitionKind,
    WeeklyDigest,
    build_weekly_digest,
    classify_transition,
    week_bounds,
)
from nocturna.domain.repositories import ArchiveDigestReader


@dataclass(frozen=True, slots=True)
class DigestWeekSummary:
    week: str
    snapshots: int
    counts: dict[TransitionKind, int]


class ListDigestWeeks:
    """Semanas con snapshot (más recientes primero), con recuento por tipo."""

    def __init__(self, reader: ArchiveDigestReader, tz: ZoneInfo) -> None:
        self._reader = reader
        self._tz = tz

    def __call__(self) -> list[DigestWeekSummary]:
        result: list[DigestWeekSummary] = []
        for week, snapshots in reversed(self._reader.snapshot_weeks(self._tz)):
            start, end = week_bounds(week, self._tz)
            counts = dict.fromkeys(TransitionKind, 0)
            for t in self._reader.transitions_between(start, end):
                counts[classify_transition(t)] += 1
            result.append(DigestWeekSummary(week=week, snapshots=snapshots, counts=counts))
        return result


class GetWeeklyDigest:
    """Resumen de una semana ISO; `None` si no tiene ningún snapshot.

    `week` inválida -> `ValueError` (de `week_bounds`).
    """

    def __init__(self, reader: ArchiveDigestReader, tz: ZoneInfo) -> None:
        self._reader = reader
        self._tz = tz

    def __call__(self, week: str) -> WeeklyDigest | None:
        start, end = week_bounds(week, self._tz)
        snapshots = dict(self._reader.snapshot_weeks(self._tz)).get(week, 0)
        if snapshots == 0:
            return None
        transitions = self._reader.transitions_between(start, end)
        keys = {k for t in transitions for k in (t.old_key, t.new_key) if k is not None}
        solutions = self._reader.solutions_by_key(keys)
        return build_weekly_digest(week, snapshots, transitions, solutions)
