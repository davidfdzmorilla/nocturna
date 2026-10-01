"""CLI `archive-snapshot` sin base de datos (T81): parseo e informe."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fakes.archive import InMemoryArchiveRepository
from fakes.archive_source import FakeArchiveSource, make_catalog, make_solution
from fakes.clock import FakeClock

from nocturna import cli
from nocturna.application.use_cases.take_archive_snapshot import TakeArchiveSnapshot

pytestmark = pytest.mark.anyio

MADRID = ZoneInfo("Europe/Madrid")
OCT_1 = datetime(2026, 10, 1, 10, 0, tzinfo=MADRID)
OCT_8 = datetime(2026, 10, 8, 10, 0, tzinfo=MADRID)


def _parse(*argv: str):
    return cli._build_parser().parse_args(["archive-snapshot", *argv])


def test_sin_flags_ni_full_ni_dry_run():
    args = _parse()
    assert args.command == "archive-snapshot" and args.full is False and args.dry_run is False


def test_full():
    args = _parse("--full")
    assert args.full is True and args.dry_run is False


def test_dry_run():
    args = _parse("--dry-run")
    assert args.dry_run is True and args.full is False


def test_full_y_dry_run_juntos():
    args = _parse("--full", "--dry-run")
    assert args.full and args.dry_run


def test_un_argumento_desconocido_es_error_de_argumentos():
    with pytest.raises(SystemExit) as exc:
        _parse("--nope")
    assert exc.value.code == 2


def test_main_despacha_archive_snapshot(monkeypatch):
    seen = {}

    def fake(args):
        seen["args"] = args
        return 7

    monkeypatch.setattr(cli, "_archive_snapshot", fake)

    assert cli.main(["archive-snapshot", "--full"]) == 7
    assert seen["args"].full is True


async def _report(catalog, repo, now, *, full=False, dry=False, max_requests=6, batch=75):
    source = FakeArchiveSource(catalog, planet_batch_size=batch)
    uc = TakeArchiveSnapshot(
        source=source,
        archive=repo,
        clock=FakeClock(now),
        max_requests=max_requests,
        planet_batch_size=batch,
        max_change_fraction=0.5,
        timezone=MADRID,
    )
    return await uc(force_full=full, dry_run=dry)


async def test_el_informe_muestra_modo_peticiones_y_recuentos():
    repo = InMemoryArchiveRepository()
    report = await _report(make_catalog(4), repo, OCT_1)

    text = cli._format_archive_snapshot_report(report, dry_run=False)

    assert "modo=full" in text
    assert "dry-run" not in text
    assert "peticiones: 1" in text
    assert "filas: 8" in text and "defaults: 4" in text
    assert "altas: 8" in text and "bajas: 0" in text
    assert "cambios de default: 0" in text
    assert "planetas que pierden default: 0" in text


async def test_el_informe_en_dry_run_lo_dice():
    report = await _report(make_catalog(2), InMemoryArchiveRepository(), OCT_1, dry=True)

    text = cli._format_archive_snapshot_report(report, dry_run=True)

    assert "dry-run: no se escribió nada" in text


async def test_el_informe_incremental_cuenta_peticiones_y_lista_defaults():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(4) + [make_solution("P03 b", "Newest 2026", release="2026-09-25")]
    await _report(catalog, repo, OCT_1)
    # P01 pierde el default; P02 lo cambia a otra solucion.
    changed = []
    for s in catalog:
        if s.pl_name == "P01 b":
            s = make_solution(s.pl_name, s.pl_refname, release=str(s.releasedate))
        elif s.pl_name == "P02 b":
            s = make_solution(
                s.pl_name,
                s.pl_refname,
                default=s.pl_refname == "Jones 2021",
                release=str(s.releasedate),
            )
        changed.append(s)

    report = await _report(changed, repo, OCT_8)
    text = cli._format_archive_snapshot_report(report, dry_run=False)

    assert "modo=incremental" in text
    assert f"peticiones: {report.snapshot.requests}" in text and report.snapshot.requests >= 3
    assert "cambios de default: 1" in text and "P02 b:" in text
    assert "planetas que pierden default: 1" in text and "    P01 b" in text


async def test_el_informe_marca_el_salto_a_completo():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(6) + [make_solution("P05 b", "Newest 2026", release="2026-09-25")]
    await _report(catalog, repo, OCT_1, max_requests=3, batch=1)
    more = catalog + [make_solution(f"P0{i} b", "N2", release="2026-09-25") for i in range(3)]

    report = await _report(more, repo, OCT_8, max_requests=3, batch=1)

    assert report.fell_back_to_full
    assert "modo=full (salto desde incremental)" in cli._format_archive_snapshot_report(
        report, dry_run=False
    )
