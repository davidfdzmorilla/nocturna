"""Constructores de escenarios de la ventana de medicion de la fase 3 (T110.a).

Una ventana "perfecta": por dia, una comprobacion automatica a las 06:00 locales con el pie al
dia, un Run de noche cerrado a las 04:00 y una entrega completada con aviso. Cada test rompe
lo que quiere probar. Nombres de tests en espanol.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from nocturna.domain.entities import Run, RunStatus
from nocturna.domain.phase3 import (
    DeliveryRecord,
    ProbeRecord,
    format_probe_line,
    parse_delivery_line,
)

MADRID = ZoneInfo("Europe/Madrid")
FIRST = date(2026, 11, 2)


def at(day: date, hh: int, mm: int = 0, ss: int = 0, tz: ZoneInfo = MADRID) -> datetime:
    return datetime.combine(day, time(hh, mm, ss), tzinfo=tz)


def probe(
    when: datetime,
    *,
    trigger: str = "scheduled",
    http: int | None = 200,
    tls: bool = True,
    error: str | None = None,
    noindex: bool = False,
    footer: datetime | None = None,
) -> ProbeRecord:
    return ProbeRecord(when, trigger, http, tls, error, noindex, footer)


def delivery(
    night: date,
    *,
    generated: datetime | None = None,
    status: str = "completed",
    causes: str = "-",
    notified: str = "ok",
    trigger: str = "scheduled",
) -> DeliveryRecord:
    generated = generated or at(night, 7, 30)
    line = (
        f"{generated.isoformat(timespec='seconds')} {night.isoformat()} {trigger} "
        f"{status} {causes} notified={notified}"
    )
    parsed = parse_delivery_line(line)
    assert parsed is not None, line
    return parsed


def days(first: date = FIRST, n: int = 14) -> list[date]:
    return [first + timedelta(days=i) for i in range(n)]


def perfect_window(first: date = FIRST, n: int = 14):
    """(probes, deliveries, finished) de una ventana sin fallos."""
    probes = [probe(at(d, 6), footer=at(d, 4, 30)) for d in days(first, n)]
    deliveries = [delivery(d) for d in days(first, n)]
    finished = [at(d, 4) for d in days(first, n)]
    return probes, deliveries, finished


def after_window(first: date = FIRST, n: int = 14) -> datetime:
    return at(first + timedelta(days=n - 1), 9, 0, 1)


def night_run(day: date, finished: datetime, notes: str = "ingest=ok | tokens=1") -> Run:
    run = Run(started_at=at(day, 0, 5), budget_tokens=300_000)
    run.finish(RunStatus.COMPLETED, finished, notes)
    return run


def write_probe_log(path, probes: list[ProbeRecord], extra: list[str] | None = None) -> None:
    lines = [format_probe_line(p) for p in probes] + (extra or [])
    path.write_text("\n".join(lines) + "\n")


def write_delivery_log(path, deliveries: list[DeliveryRecord]) -> None:
    lines = []
    for d in deliveries:
        causes = ",".join(d.causes) or "-"
        lines.append(
            f"{d.generated_at.isoformat(timespec='seconds')} {d.night.isoformat()} {d.trigger} "
            f"{d.status} {causes} notified={d.notified}"
        )
    path.write_text("\n".join(lines) + "\n")
