"""Resumen semanal de cambios de referencia del NASA Exoplanet Archive (T84).

Dominio puro: sin IO ni LLM. Se calcula al vuelo a partir de las filas de
`archive_default_change` de los snapshots de una semana ISO (lunes-domingo en
la zona de la configuración); no se persiste ni es un `Finding`.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from nocturna.domain.archive import (
    ArchiveParameterValue,
    ArchiveSolution,
    canon_float,
)
from nocturna.domain.entities import MeasuredParameter
from nocturna.domain.errors import InvariantViolation


class TransitionKind(StrEnum):
    CHANGED = "changed"
    NEW_PLANET = "new_planet"
    REGAINED = "regained"
    LOST = "lost"


_KIND_ORDER: dict[TransitionKind, int] = {
    TransitionKind.CHANGED: 0,
    TransitionKind.NEW_PLANET: 1,
    TransitionKind.REGAINED: 2,
    TransitionKind.LOST: 3,
}


@dataclass(frozen=True, slots=True)
class DefaultTransition:
    """Una fila de `archive_default_change` con el contexto que necesita el resumen."""

    pl_name: str
    old_key: str | None
    new_key: str | None
    snapshot_taken_at: datetime
    planet_seen_before: bool


@dataclass(frozen=True, slots=True)
class ParameterChange:
    """Un parámetro cuya solución por defecto cambió. En la masa, las
    procedencias (`pl_bmassprov`: Mass, Msini...) de cada lado; `None` en el resto."""

    parameter: MeasuredParameter
    old: ArchiveParameterValue | None
    new: ArchiveParameterValue | None
    old_mass_provenance: str | None = None
    new_mass_provenance: str | None = None


@dataclass(frozen=True, slots=True)
class DigestEntry:
    kind: TransitionKind
    pl_name: str
    snapshot_taken_at: datetime
    old: ArchiveSolution | None
    new: ArchiveSolution | None
    parameter_changes: tuple[ParameterChange, ...]


@dataclass(frozen=True, slots=True)
class WeeklyDigest:
    week: str  # "2026-W41"
    snapshots: int
    entries: tuple[DigestEntry, ...]


def classify_transition(t: DefaultTransition) -> TransitionKind:
    if t.old_key is not None and t.new_key is not None:
        return TransitionKind.CHANGED
    if t.new_key is not None:
        return TransitionKind.REGAINED if t.planet_seen_before else TransitionKind.NEW_PLANET
    if t.old_key is not None:
        return TransitionKind.LOST
    raise InvariantViolation(f"transición sin clave anterior ni nueva para {t.pl_name!r}")


def _canon(p: ArchiveParameterValue) -> tuple[str, str, str, str]:
    return (
        canon_float(p.value),
        canon_float(p.err1),
        canon_float(p.err2),
        "" if p.lim is None else str(int(p.lim)),
    )


def parameter_changes(old: ArchiveSolution, new: ArchiveSolution) -> tuple[ParameterChange, ...]:
    """Parámetros (masa, radio, periodo) que difieren entre dos soluciones.

    Compara valor, errores y límite con la misma canonización que
    `solution_key` (1.5 == 1.50); en la masa, además, `pl_bmassprov`.
    """
    changes: list[ParameterChange] = []
    pairs = (
        (MeasuredParameter.MASS, old.mass, new.mass),
        (MeasuredParameter.RADIUS, old.radius, new.radius),
        (MeasuredParameter.PERIOD, old.period, new.period),
    )
    for parameter, o, n in pairs:
        is_mass = parameter == MeasuredParameter.MASS
        provenance_differs = is_mass and old.pl_bmassprov != new.pl_bmassprov
        if _canon(o) == _canon(n) and not provenance_differs:
            continue
        changes.append(
            ParameterChange(
                parameter=parameter,
                old=o,
                new=n,
                old_mass_provenance=old.pl_bmassprov if is_mass else None,
                new_mass_provenance=new.pl_bmassprov if is_mass else None,
            )
        )
    return tuple(changes)


def build_weekly_digest(
    week: str,
    snapshots: int,
    transitions: Sequence[DefaultTransition],
    solutions: Mapping[str, ArchiveSolution],
) -> WeeklyDigest:
    """Resumen ordenado por (tipo, planeta, fecha). Una clave sin solución es
    una incoherencia (las soluciones no se borran): `InvariantViolation`."""

    def lookup(key: str | None) -> ArchiveSolution | None:
        if key is None:
            return None
        sol = solutions.get(key)
        if sol is None:
            raise InvariantViolation(f"solución {key[:8]} del resumen semanal no existe")
        return sol

    entries: list[DigestEntry] = []
    for t in transitions:
        kind = classify_transition(t)
        old = lookup(t.old_key)
        new = lookup(t.new_key)
        entries.append(
            DigestEntry(
                kind=kind,
                pl_name=t.pl_name,
                snapshot_taken_at=t.snapshot_taken_at,
                old=old,
                new=new,
                parameter_changes=(
                    parameter_changes(old, new) if old is not None and new is not None else ()
                ),
            )
        )
    entries.sort(key=lambda e: (_KIND_ORDER[e.kind], e.pl_name, e.snapshot_taken_at))
    return WeeklyDigest(week=week, snapshots=snapshots, entries=tuple(entries))


_WEEK_RE = re.compile(r"([0-9]{4})-W([0-9]{2})")


def iso_week_of(at: datetime, tz: ZoneInfo) -> str:
    """Semana ISO ("2026-W41") de `at` vista en `tz`. `at` debe ser aware."""
    if at.tzinfo is None:
        raise ValueError("iso_week_of exige un datetime con zona horaria")
    year, week, _ = at.astimezone(tz).isocalendar()
    return f"{year:04d}-W{week:02d}"


def week_bounds(week: str, tz: ZoneInfo) -> tuple[datetime, datetime]:
    """[lunes 00:00, lunes siguiente 00:00) de la semana ISO en `tz`.

    `ValueError` si el formato no es `YYYY-Www` o la semana no existe (p. ej.
    semana 53 de un año de 52). El fin se calcula como medianoche local del
    lunes siguiente, no sumando 7x24 h: correcto con el cambio de hora.
    """
    match = _WEEK_RE.fullmatch(week)
    if match is None:
        raise ValueError(f"semana inválida {week!r}: se esperaba YYYY-Www")
    year, number = int(match.group(1)), int(match.group(2))
    try:
        monday = date.fromisocalendar(year, number, 1)
    except ValueError as exc:
        raise ValueError(f"semana inválida {week!r}: {exc}") from exc
    start = datetime.combine(monday, time.min, tzinfo=tz)
    end = datetime.combine(monday + timedelta(days=7), time.min, tzinfo=tz)
    return start, end
