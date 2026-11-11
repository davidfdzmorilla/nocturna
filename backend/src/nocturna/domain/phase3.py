"""Instrumento de medición del cierre de la fase 3 (T110.a, ADR 0044).

Puro, sin IO. Convierte las comprobaciones de la web pública (`phase3-probes.log`),
las entregas del informe matinal (`morning-deliveries.log`) y los `Run` de noche
en un veredicto por día para los criterios automáticos (a), (b) y (e). Los
criterios (c) y (d) son manuales (docs/CIERRE_FASE_3.md).

Convención de "noche N": la noche que empieza a las 00:00 locales del día N
(coherente con `night_report.target_night`). El "día N" de (a) y (b) es el mismo
día natural local N, que contiene el final de esa noche.

Las líneas de log son `clave=valor` separadas por espacios; nunca llevan la URL.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

A_MIN = 13
B_MIN = 12
E_MIN = 14
WINDOW_DAYS = 14

MANUAL_TRIGGER = "manual"
_NO_CAUSES = "-"
_UNCLASSIFIED = "unclassified"
_MAX_HTML = 2_000_000


class Verdict(StrEnum):
    CUMPLE = "cumple"
    NO_CUMPLE = "no_cumple"
    INCOMPLETA = "incompleta"


@dataclass(frozen=True, slots=True)
class ProbeRecord:
    checked_at: datetime
    trigger: str
    http_status: int | None
    tls_ok: bool
    error_class: str | None
    noindex: bool
    footer_updated_at: datetime | None


@dataclass(frozen=True, slots=True)
class DeliveryRecord:
    generated_at: datetime
    night: date
    trigger: str
    status: str
    causes: tuple[str, ...]
    notified: str


@dataclass(frozen=True, slots=True)
class DayVerdict:
    day: date
    ok: bool
    reason: str


@dataclass(frozen=True, slots=True)
class CriterionResult:
    name: str
    days: tuple[DayVerdict, ...]
    threshold: int

    @property
    def count(self) -> int:
        return sum(1 for d in self.days if d.ok)


@dataclass(frozen=True, slots=True)
class Phase3Measurement:
    first_night: date
    days: int
    a: CriterionResult
    b: CriterionResult
    e: CriterionResult
    status: Verdict
    complete_at: datetime
    # Criterios que ya no pueden alcanzar su umbral aunque los días pendientes salgan bien.
    cannot_comply: tuple[str, ...] = ()


# --- Líneas de log ----------------------------------------------------------------------


def _fmt_dt(value: datetime | None) -> str:
    return value.isoformat(timespec="seconds") if value is not None else "-"


def format_probe_line(r: ProbeRecord) -> str:
    """`checked_at=… trigger=… http=200|- tls=ok|fail error=Clase|- noindex=yes|no footer=…|-`."""
    return (
        f"checked_at={_fmt_dt(r.checked_at)} trigger={r.trigger} "
        f"http={r.http_status if r.http_status is not None else '-'} "
        f"tls={'ok' if r.tls_ok else 'fail'} error={r.error_class or '-'} "
        f"noindex={'yes' if r.noindex else 'no'} footer={_fmt_dt(r.footer_updated_at)}"
    )


def _parse_dt(raw: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


_PROBE_KEYS = {"checked_at", "trigger", "http", "tls", "error", "noindex", "footer"}


def parse_probe_line(s: str) -> ProbeRecord | None:
    pairs: dict[str, str] = {}
    for token in s.split():
        key, sep, value = token.partition("=")
        if not sep or not key:
            return None
        pairs[key] = value
    if set(pairs) != _PROBE_KEYS:
        return None
    checked_at = _parse_dt(pairs["checked_at"])
    if checked_at is None:
        return None
    if pairs["http"] == "-":
        http_status = None
    elif pairs["http"].isdigit():
        http_status = int(pairs["http"])
    else:
        return None
    if pairs["tls"] not in ("ok", "fail") or pairs["noindex"] not in ("yes", "no"):
        return None
    if pairs["footer"] == "-":
        footer = None
    else:
        footer = _parse_dt(pairs["footer"])
        if footer is None:
            return None
    return ProbeRecord(
        checked_at=checked_at,
        trigger=pairs["trigger"],
        http_status=http_status,
        tls_ok=pairs["tls"] == "ok",
        error_class=None if pairs["error"] == "-" else pairs["error"],
        noindex=pairs["noindex"] == "yes",
        footer_updated_at=footer,
    )


def parse_delivery_line(s: str) -> DeliveryRecord | None:
    """Línea de `delivery_line()`: `generated_at night trigger status causes notified=x`."""
    parts = s.split()
    if len(parts) != 6 or not parts[5].startswith("notified="):
        return None
    generated_at = _parse_dt(parts[0])
    if generated_at is None:
        return None
    try:
        night = date.fromisoformat(parts[1])
    except ValueError:
        return None
    causes = () if parts[4] == _NO_CAUSES else tuple(parts[4].split(","))
    return DeliveryRecord(
        generated_at=generated_at,
        night=night,
        trigger=parts[2],
        status=parts[3],
        causes=causes,
        notified=parts[5].removeprefix("notified="),
    )


# --- HTML de la web pública -------------------------------------------------------------


class _SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.noindex = False
        self.footer: str | None = None
        self._in_footer = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {k.lower(): (v or "") for k, v in attrs}
        if tag == "footer":
            self._in_footer += 1
        elif tag == "meta":
            is_robots = values.get("name", "").strip().lower() == "robots"
            if is_robots and "noindex" in values.get("content", "").lower():
                self.noindex = True
        elif tag == "time" and self._in_footer and self.footer is None:
            if values.get("datetime"):
                self.footer = values["datetime"]

    def handle_endtag(self, tag: str) -> None:
        if tag == "footer" and self._in_footer:
            self._in_footer -= 1


def parse_site_html(html: str) -> tuple[bool, datetime | None]:
    """`(noindex, fecha del pie)` de la portada. Sin fecha válida con zona: `None`."""
    parser = _SiteParser()
    parser.feed(html[:_MAX_HTML])
    parser.close()
    footer = _parse_dt(parser.footer) if parser.footer else None
    return parser.noindex, footer


def robots_header_is_noindex(value: str | None) -> bool:
    """`X-Robots-Tag` (varias cabeceras unidas con comas): `noindex` o `none`, sin mayúsculas."""
    if not value:
        return False
    lowered = value.lower()
    if "noindex" in lowered:
        return True
    for token in lowered.split(","):
        directive = token.rsplit(":", 1)[-1].strip()  # admite `googlebot: none`
        if directive == "none":
            return True
    return False


# --- Evaluación -------------------------------------------------------------------------


def _local_date(value: datetime, tz: ZoneInfo) -> date:
    return value.astimezone(tz).date()


def _limit(day: date, deadline: time, tz: ZoneInfo) -> datetime:
    return datetime.combine(day, deadline, tzinfo=tz)


def _eval_a(day: date, probes: Sequence[ProbeRecord], tz: ZoneInfo) -> DayVerdict:
    todays = sorted(
        (p for p in probes if p.trigger != MANUAL_TRIGGER and _local_date(p.checked_at, tz) == day),
        key=lambda p: p.checked_at,
    )
    if not todays:
        return DayVerdict(day, False, "sin comprobación")
    first = todays[0]
    if first.error_class is not None:
        # Cuerpo demasiado grande (200 sin HTML) u otro fallo: no se pudo comprobar el robots.
        return DayVerdict(day, False, f"error: {first.error_class}")
    if first.http_status != 200:
        status = first.http_status if first.http_status is not None else (first.error_class or "-")
        return DayVerdict(day, False, f"http {status}")
    if not first.tls_ok:
        return DayVerdict(day, False, "tls")
    if first.noindex:
        return DayVerdict(day, False, "noindex")
    return DayVerdict(day, True, "ok")


def _eval_b(
    day: date,
    probes: Sequence[ProbeRecord],
    finished: Sequence[datetime],
    tz: ZoneInfo,
    deadline: time,
) -> DayVerdict:
    limit = _limit(day, deadline, tz)
    early = sorted(
        (p for p in probes if p.checked_at < limit and _local_date(p.checked_at, tz) == day),
        key=lambda p: p.checked_at,
    )
    if not early:
        return DayVerdict(day, False, f"sin comprobación antes de las {deadline:%H:%M}")
    saw_run = False
    saw_footer = False
    for probe in early:
        previous = [f for f in finished if f < probe.checked_at]
        if not previous:
            continue
        saw_run = True
        if probe.footer_updated_at is None:
            continue
        saw_footer = True
        if probe.footer_updated_at >= max(previous):
            return DayVerdict(day, True, "ok")
    if not saw_run:
        return DayVerdict(day, False, "sin Run previo")
    if not saw_footer:
        return DayVerdict(day, False, "sin fecha de pie")
    return DayVerdict(day, False, "pie anterior al último Run")


def _eval_e(
    night: date, deliveries: Sequence[DeliveryRecord], tz: ZoneInfo, deadline: time
) -> DayVerdict:
    lines = [d for d in deliveries if d.night == night]
    if not lines:
        return DayVerdict(night, False, "sin línea de entrega")
    if not any(d.notified == "ok" for d in lines):
        return DayVerdict(night, False, "sin aviso entregado")
    if all(d.status == "completed" for d in lines):
        return DayVerdict(night, True, "ok")
    limit = _limit(night, deadline, tz)
    for d in lines:
        has_cause = any(c != _UNCLASSIFIED for c in d.causes)
        if d.notified == "ok" and d.generated_at < limit and has_cause:
            return DayVerdict(night, True, "ok (no completed, aviso con causa)")
    return DayVerdict(night, False, "no completed sin aviso con causa antes de las 09:00")


def _cannot_comply(
    criteria: Sequence[CriterionResult], dates: Sequence[date], tz: ZoneInfo, now: datetime
) -> tuple[str, ...]:
    """Criterios cuyos fallos en días ya cerrados (pasada la medianoche) superan el margen.

    Conservador: un día solo cuenta como fallo definitivo cuando ya no puede llegar ningún
    registro que lo arregle (fin del día natural local).
    """
    closed = [
        i
        for i, d in enumerate(dates)
        if datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz) <= now
    ]
    names: list[str] = []
    for c in criteria:
        failed = sum(1 for i in closed if not c.days[i].ok)
        if failed > len(dates) - c.threshold:
            names.append(c.name)
    return tuple(names)


def evaluate_phase3(
    first_night: date,
    days: int,
    probes: Iterable[ProbeRecord],
    deliveries: Iterable[DeliveryRecord],
    night_runs_finished: Sequence[datetime],
    tz: ZoneInfo,
    now: datetime,
    deadline: time = time(9, 0),
) -> Phase3Measurement:
    probe_list = list(probes)
    delivery_list = list(deliveries)
    finished = list(night_runs_finished)
    dates = [first_night + timedelta(days=i) for i in range(days)]
    a = CriterionResult("a", tuple(_eval_a(d, probe_list, tz) for d in dates), A_MIN)
    b = CriterionResult(
        "b", tuple(_eval_b(d, probe_list, finished, tz, deadline) for d in dates), B_MIN
    )
    e = CriterionResult("e", tuple(_eval_e(d, delivery_list, tz, deadline) for d in dates), E_MIN)
    last = dates[-1] if dates else first_night
    complete_at = _limit(last, deadline, tz)
    if now < complete_at:
        status = Verdict.INCOMPLETA
    elif a.count >= A_MIN and b.count >= B_MIN and e.count >= E_MIN:
        status = Verdict.CUMPLE
    else:
        status = Verdict.NO_CUMPLE
    cannot = _cannot_comply((a, b, e), dates, tz, now)
    return Phase3Measurement(first_night, days, a, b, e, status, complete_at, cannot)
