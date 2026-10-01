"""Caso de uso: instantánea del NASA Exoplanet Archive (T81).

Cero LLM y cero tokens. Completa una vez al mes natural (o con `force_full`);
el resto de ejecuciones son incrementales: filas nuevas desde la última
`releasedate` vista, más las soluciones por defecto, más las soluciones de los
planetas cuya default cambió o desapareció. Una caída masiva (muchas bajas o
muchos cambios de default) aborta sin persistir: casi seguro es una respuesta
truncada del archivo, no la realidad.

No captura `ExoplanetArchiveUnavailable`: sube al llamador. No hace commit:
`save_snapshot` va en la unidad de trabajo del llamador.
"""

import hashlib
import math
from dataclasses import dataclass
from datetime import date
from uuid import uuid4
from zoneinfo import ZoneInfo

from nocturna.domain.archive import (
    ArchiveSnapshot,
    ArchiveSolution,
    ArchiveSolutionSource,
    SnapshotDiff,
    SnapshotKind,
    collapse_duplicates,
    diff_snapshot,
)
from nocturna.domain.clock import Clock
from nocturna.domain.repositories import ArchiveRepository


class SnapshotAborted(Exception):
    """La guarda de cambios masivos abortó el snapshot; no se persistió nada."""


@dataclass(frozen=True, slots=True)
class ArchiveSnapshotReport:
    snapshot: ArchiveSnapshot
    diff: SnapshotDiff
    fell_back_to_full: bool
    persisted: bool
    # Soluciones vistas (ya sin duplicados), para que quien informe pueda
    # mostrar la referencia de un cambio de default sin volver a consultar.
    solutions: tuple[ArchiveSolution, ...] = ()


class TakeArchiveSnapshot:
    def __init__(
        self,
        *,
        source: ArchiveSolutionSource,
        archive: ArchiveRepository,
        clock: Clock,
        max_requests: int,
        planet_batch_size: int,
        max_change_fraction: float,
        timezone: ZoneInfo,
    ) -> None:
        self._source = source
        self._archive = archive
        self._clock = clock
        self._max_requests = max_requests
        self._batch = planet_batch_size
        self._max_fraction = max_change_fraction
        self._tz = timezone

    def _needs_full(self, force_full: bool) -> bool:
        if force_full:
            return True
        last_full = self._archive.last_full_snapshot()
        if last_full is None:
            return True
        now = self._clock.now().astimezone(self._tz)
        taken = last_full.taken_at.astimezone(self._tz)
        return (taken.year, taken.month) != (now.year, now.month)

    async def __call__(self, *, force_full: bool, dry_run: bool) -> ArchiveSnapshotReport:
        started = self._clock.now()
        last = self._archive.last_snapshot()
        previous_defaults = self._archive.current_defaults()

        fell_back = False
        scope: frozenset[str] | None
        if self._needs_full(force_full):
            kind = SnapshotKind.FULL
            rows = list(await self._source.all_solutions())
            scope = None
        else:
            kind = SnapshotKind.INCREMENTAL
            since = last.max_releasedate
            new_rows = (
                list(await self._source.released_since(since))
                if since is not None
                else list(await self._source.all_solutions())
            )
            defaults = list(await self._source.default_solutions())
            affected = self._affected(new_rows, defaults, previous_defaults)
            batches = math.ceil(len(affected) / self._batch)
            remaining = self._max_requests - self._source.requests_made
            if batches > remaining:
                fell_back = True
                kind = SnapshotKind.FULL
                rows = list(await self._source.all_solutions())
                scope = None
            else:
                extra = list(await self._source.solutions_for_planets(affected)) if affected else []
                rows = new_rows + defaults + extra
                scope = frozenset(affected)

        seen, duplicates = collapse_duplicates(rows)
        keys = [s.solution_key for s in seen]
        # Todas las activas, no solo las del alcance: `seen` incluye las
        # defaults de planetas fuera de `scope`, que ya están en la base y no
        # son altas. El alcance solo limita las BAJAS (`removal_scope`).
        active = self._archive.active_keys()
        diff = diff_snapshot(
            seen,
            active=active,
            removed=self._archive.removed_keys(keys),
            previous_defaults=previous_defaults,
            removal_scope=scope,
        )
        if last is not None:
            self._guard(diff, active_before=len(active), previous=previous_defaults)

        releasedates = [s.releasedate for s in seen]
        candidates: list[date] = list(releasedates)
        if last is not None and last.max_releasedate is not None:
            candidates.append(last.max_releasedate)
        finished = self._clock.now()
        snapshot = ArchiveSnapshot(
            id=uuid4(),
            taken_at=started,
            kind=kind,
            max_releasedate=max(candidates) if candidates else None,
            rows_total=len(seen),
            defaults_total=sum(1 for s in seen if s.is_default),
            duplicate_rows=duplicates,
            payload_sha256=hashlib.sha256("\n".join(sorted(keys)).encode("utf-8")).hexdigest(),
            requests=self._source.requests_made,
            duration_ms=max(0, int((finished - started).total_seconds() * 1000)),
        )
        if not dry_run:
            self._archive.save_snapshot(snapshot, seen, diff)
        return ArchiveSnapshotReport(
            snapshot=snapshot,
            diff=diff,
            fell_back_to_full=fell_back,
            persisted=not dry_run,
            solutions=seen,
        )

    @staticmethod
    def _affected(
        new_rows: list[ArchiveSolution],
        defaults: list[ArchiveSolution],
        previous_defaults: dict[str, str],
    ) -> list[str]:
        affected = {r.pl_name for r in new_rows}
        seen_defaults: dict[str, str] = {}
        for r in defaults:
            seen_defaults[r.pl_name] = r.solution_key
        if previous_defaults:
            for name, key in seen_defaults.items():
                if previous_defaults.get(name) != key:
                    affected.add(name)
            affected |= set(previous_defaults) - set(seen_defaults)
        return sorted(affected)

    def _guard(self, diff: SnapshotDiff, *, active_before: int, previous: dict[str, str]) -> None:
        """Aborta si el diff es sospechosamente grande (no se persiste nada).

        Numerador de cambios de default: `default_changes` (que incluye planetas
        nuevos, con `old` None) mas `lost_defaults` (planetas que pierden el
        default sin sustituto), sobre `len(previous)` defaults previos.
        """
        if len(diff.removed) > self._max_fraction * active_before:
            raise SnapshotAborted(
                f"{len(diff.removed)} bajas sobre {active_before} soluciones activas "
                f"superan el {self._max_fraction:.0%} permitido; no se persiste nada"
            )
        default_changes = len(diff.default_changes) + len(diff.lost_defaults)
        if default_changes > self._max_fraction * len(previous):
            raise SnapshotAborted(
                f"{default_changes} cambios de default sobre {len(previous)} "
                f"superan el {self._max_fraction:.0%} permitido; no se persiste nada"
            )
