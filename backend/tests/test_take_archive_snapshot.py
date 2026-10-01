"""Pruebas mínimas de T81 (pasos 4-5): fuente HTTP y caso de uso, sin red."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes.archive import InMemoryArchiveRepository
from fakes.clock import FakeClock
from helpers.archive import adql_of, fixture_rows, make_client, snapshot_handler

from nocturna.application.use_cases.take_archive_snapshot import (
    SnapshotAborted,
    TakeArchiveSnapshot,
)
from nocturna.domain.archive import SnapshotKind
from nocturna.infrastructure.exoplanet_archive.snapshot import ArchiveSnapshotSource

pytestmark = pytest.mark.anyio

TZ = ZoneInfo("Europe/Madrid")


def _use_case(source, repo, clock, **kw):
    return TakeArchiveSnapshot(
        source=source,
        archive=repo,
        clock=clock,
        max_requests=kw.get("max_requests", 6),
        planet_batch_size=75,
        max_change_fraction=kw.get("fraction", 0.5),
        timezone=TZ,
    )


async def test_source_batches_planets_and_escapes_quotes() -> None:
    client, seen = make_client(snapshot_handler())
    source = ArchiveSnapshotSource(client, planet_batch_size=2)
    sols = await source.solutions_for_planets(["V1298 Tau b", "V1298 Tau e", "HD 202206 c"])
    assert source.requests_made == 2
    assert {s.pl_name for s in sols} == {"V1298 Tau b", "V1298 Tau e", "HD 202206 c"}
    assert "pl_name in ('HD 202206 c','V1298 Tau b')" in adql_of(seen[0])


async def test_first_snapshot_is_full_then_incremental_same_month() -> None:
    repo = InMemoryArchiveRepository()
    clock = FakeClock(datetime(2026, 10, 1, 10, 0, tzinfo=TZ))
    client, _ = make_client(snapshot_handler())
    first = await _use_case(ArchiveSnapshotSource(client, planet_batch_size=2), repo, clock)(
        force_full=False, dry_run=False
    )
    assert first.snapshot.kind is SnapshotKind.FULL and first.persisted
    assert len(first.diff.added) == first.snapshot.rows_total

    client2, _ = make_client(snapshot_handler())
    clock.set(datetime(2026, 10, 8, 10, 0, tzinfo=TZ))
    second = await _use_case(ArchiveSnapshotSource(client2, planet_batch_size=2), repo, clock)(
        force_full=False, dry_run=False
    )
    assert second.snapshot.kind is SnapshotKind.INCREMENTAL
    assert second.diff.added == () and second.diff.removed == ()
    assert len(repo.snapshots) == 2

    clock.set(datetime(2026, 11, 2, 10, 0, tzinfo=UTC))
    client3, _ = make_client(snapshot_handler())
    third = await _use_case(ArchiveSnapshotSource(client3, planet_batch_size=2), repo, clock)(
        force_full=False, dry_run=True
    )
    assert third.snapshot.kind is SnapshotKind.FULL and not third.persisted
    assert len(repo.snapshots) == 2


async def test_mass_removal_aborts_without_persisting() -> None:
    repo = InMemoryArchiveRepository()
    clock = FakeClock(datetime(2026, 10, 1, 10, 0, tzinfo=TZ))
    client, _ = make_client(snapshot_handler())
    await _use_case(ArchiveSnapshotSource(client, planet_batch_size=2), repo, clock)(
        force_full=False, dry_run=False
    )
    truncated = fixture_rows("ps_t81_planets.csv")[:3]
    client2, _ = make_client(snapshot_handler(truncated))
    with pytest.raises(SnapshotAborted):
        await _use_case(ArchiveSnapshotSource(client2, planet_batch_size=2), repo, clock)(
            force_full=True, dry_run=False
        )
    assert len(repo.snapshots) == 1
