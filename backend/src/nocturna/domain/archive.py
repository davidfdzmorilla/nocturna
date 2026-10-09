"""Instantánea del NASA Exoplanet Archive y su diferencia entre noches (T81).

Dominio puro: solo stdlib. Una `ArchiveSolution` es una fila de la tabla `ps`
(una solución publicada de un planeta). El snapshot guarda qué soluciones
existen, no su historia completa; `diff_snapshot` compara lo visto hoy con lo
que ya está en la base.
"""

import hashlib
import math
from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from nocturna.domain.entities import (
    CatalogSolution,
    MeasuredParameter,
    MeasurementLimit,
    MeasurementUnit,
)
from nocturna.domain.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class ArchiveParameterValue:
    """Valor de un parámetro con sus errores y su límite (`lim` en {-1, 0, 1})."""

    value: float | None = None
    err1: float | None = None
    err2: float | None = None
    lim: int | None = None


@dataclass(frozen=True, slots=True)
class ArchiveSolution:
    pl_name: str
    hostname: str
    pl_refname: str
    ref_key: str
    ref_text: str
    arxiv_id: str | None
    soltype: str | None
    releasedate: date
    pl_pubdate: str | None
    is_default: bool
    mass: ArchiveParameterValue
    radius: ArchiveParameterValue
    period: ArchiveParameterValue
    pl_bmassprov: str | None
    st_rad: ArchiveParameterValue
    st_mass: ArchiveParameterValue
    discoverymethod: str | None
    ttv_flag: bool | None
    pl_controv_flag: bool | None

    @property
    def solution_key(self) -> str:
        """Clave estable de la solución, formato v1.

        sha256 hexadecimal (UTF-8) de la cadena
        `pl_name|ref_key|soltype|` seguida de 12 valores canónicos separados
        por `|`: para masa, radio y periodo (en ese orden), `value`, `err1`,
        `err2`, `lim`. Canon de cada valor: `None` -> cadena vacía; número ->
        `repr(float(x))`, con `-0.0` convertido a `0.0`; `lim` -> `str(int)`.
        `soltype` nulo -> cadena vacía. No entran `default_flag`,
        `releasedate` ni los campos descriptivos: cambiarlos no crea otra
        solución.
        """
        parts = [self.pl_name, self.ref_key, self.soltype or ""]
        for p in (self.mass, self.radius, self.period):
            parts += canon_parameter(p)
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def canon_parameter(p: ArchiveParameterValue) -> list[str]:
    """Valores canónicos de un parámetro: `value`, `err1`, `err2`, `lim`."""
    return [
        canon_float(p.value),
        canon_float(p.err1),
        canon_float(p.err2),
        "" if p.lim is None else str(int(p.lim)),
    ]


def canon_float(x: float | None) -> str:
    if x is None:
        return ""
    value = float(x)
    if value == 0.0:
        return "0.0"
    return repr(value)


_TRUE_MASS_PROVENANCE = "Mass"
_UNIT_BY_PARAMETER: dict[MeasuredParameter, MeasurementUnit] = {
    MeasuredParameter.MASS: MeasurementUnit.M_EARTH,
    MeasuredParameter.RADIUS: MeasurementUnit.R_EARTH,
    MeasuredParameter.PERIOD: MeasurementUnit.DAY,
}
_LIMIT_BY_FLAG: dict[int, MeasurementLimit] = {
    0: MeasurementLimit.NONE,
    1: MeasurementLimit.UPPER,
    -1: MeasurementLimit.LOWER,
}


def parameter_unit(parameter: MeasuredParameter) -> MeasurementUnit:
    """Unidad en que el archivo da el parámetro (M⊕, R⊕, días)."""
    return _UNIT_BY_PARAMETER[parameter]


def limit_of(p: ArchiveParameterValue) -> MeasurementLimit | None:
    """Límite del valor (`lim`: 0 valor, 1 cota superior, -1 inferior); `None` si es desconocido."""
    return MeasurementLimit.NONE if p.lim is None else _LIMIT_BY_FLAG.get(p.lim)


def catalog_solution_from_archive(
    sol: ArchiveSolution, parameter: MeasuredParameter, *, is_default: bool
) -> CatalogSolution | None:
    """`CatalogSolution` de `parameter` a partir de una fila del archivo.

    `None` si no hay valor válido (ausente, no finito o no positivo), si el
    `lim` es desconocido o, para la masa, si no es masa verdadera
    (`pl_bmassprov != "Mass"`). Los errores se toman en valor absoluto
    (el archivo da `err2` negativo); no finitos pasan a `None`.
    """
    if parameter == MeasuredParameter.MASS and sol.pl_bmassprov != _TRUE_MASS_PROVENANCE:
        return None
    p = {
        MeasuredParameter.MASS: sol.mass,
        MeasuredParameter.RADIUS: sol.radius,
        MeasuredParameter.PERIOD: sol.period,
    }[parameter]
    if p.value is None or not math.isfinite(p.value) or p.value <= 0:
        return None
    limit = limit_of(p)
    if limit is None:
        return None

    def _err(x: float | None) -> float | None:
        return abs(x) if x is not None and math.isfinite(x) else None

    return CatalogSolution(
        planet_name=sol.pl_name,
        parameter=parameter,
        value=p.value,
        err_plus=_err(p.err1),
        err_minus=_err(p.err2),
        unit=parameter_unit(parameter),
        limit=limit,
        reference=sol.ref_text.strip() or "(sin referencia)",
        is_default=is_default,
        arxiv_id=sol.arxiv_id,
        solution_key=sol.solution_key,
        soltype=sol.soltype,
        pl_pubdate=sol.pl_pubdate,
        releasedate=sol.releasedate,
        ttv_flag=sol.ttv_flag,
    )


class SnapshotKind(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


@dataclass(frozen=True, slots=True)
class DefaultChange:
    """Cambio de la solución por defecto de un planeta. `old_key` es `None`
    si el planeta es nuevo en la base."""

    pl_name: str
    old_key: str | None
    new_key: str


@dataclass(frozen=True, slots=True)
class SnapshotDiff:
    added: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()
    reactivated: tuple[str, ...] = ()
    default_changes: tuple[DefaultChange, ...] = ()
    lost_defaults: tuple[str, ...] = ()  # pl_name sin default hoy y con default antes


@dataclass(frozen=True, slots=True)
class ArchiveSnapshot:
    id: UUID
    taken_at: datetime
    kind: SnapshotKind
    max_releasedate: date | None
    rows_total: int
    defaults_total: int
    duplicate_rows: int
    payload_sha256: str
    requests: int
    duration_ms: int


def collapse_duplicates(
    rows: Sequence[ArchiveSolution],
) -> tuple[tuple[ArchiveSolution, ...], int]:
    """Fusiona filas con la misma `solution_key`; devuelve (filas, nº fusionadas).

    `is_default` de la fusionada es el OR del grupo; los campos descriptivos
    son los de la primera tras ordenar por default descendente y
    `releasedate` descendente (desempate por texto, determinista). El
    resultado sale ordenado por `(pl_name, solution_key)`.
    """
    groups: dict[str, list[ArchiveSolution]] = defaultdict(list)
    for row in rows:
        groups[row.solution_key].append(row)
    merged: list[ArchiveSolution] = []
    for group in groups.values():
        group.sort(
            key=lambda r: (
                not r.is_default,
                -r.releasedate.toordinal(),
                r.pl_refname,
                r.hostname,
                r.pl_pubdate or "",
            )
        )
        first = group[0]
        if any(r.is_default for r in group) and not first.is_default:
            first = replace(first, is_default=True)
        merged.append(first)
    merged.sort(key=lambda r: (r.pl_name, r.solution_key))
    return tuple(merged), len(rows) - len(merged)


def diff_snapshot(
    seen: Collection[ArchiveSolution],
    *,
    active: Mapping[str, str],
    removed: Collection[str],
    previous_defaults: Mapping[str, str],
    removal_scope: frozenset[str] | None,
) -> SnapshotDiff:
    """Diferencia entre lo visto hoy (`seen`, ya sin duplicados) y la base.

    - `active`: clave -> pl_name de las soluciones activas en la base.
    - `removed`: claves de la base con `removed_at` (retiradas antes).
    - `previous_defaults`: pl_name -> clave del default vigente. Vacío =
      primer snapshot: no hay cambios de default ni defaults perdidos.
    - `removal_scope`: planetas cuyas bajas se pueden afirmar (`None` = todos,
      snapshot completo). Fuera del alcance, no verse no implica baja.
    """
    seen_by_key = {s.solution_key: s for s in seen}
    removed_set = set(removed)
    in_scope = (lambda name: True) if removal_scope is None else removal_scope.__contains__

    added = sorted(k for k in seen_by_key if k not in active and k not in removed_set)
    reactivated = sorted(k for k in seen_by_key if k not in active and k in removed_set)
    gone = sorted(k for k, name in active.items() if in_scope(name) and k not in seen_by_key)

    seen_defaults: dict[str, str] = {}
    for key, sol in seen_by_key.items():
        if not sol.is_default:
            continue
        if sol.pl_name in seen_defaults:
            raise InvariantViolation(f"dos soluciones por defecto para el planeta {sol.pl_name!r}")
        seen_defaults[sol.pl_name] = key

    changes: list[DefaultChange] = []
    lost: list[str] = []
    # Tras una caída total de defaults no se registran transiciones; no puede ocurrir sin
    # edición manual porque la guarda del 10 % aborta antes.
    if previous_defaults:
        for name in sorted(seen_defaults):
            old = previous_defaults.get(name)
            if old != seen_defaults[name]:
                changes.append(DefaultChange(name, old, seen_defaults[name]))
        lost = sorted(
            name for name in previous_defaults if in_scope(name) and name not in seen_defaults
        )
    return SnapshotDiff(
        added=tuple(added),
        removed=tuple(gone),
        reactivated=tuple(reactivated),
        default_changes=tuple(changes),
        lost_defaults=tuple(lost),
    )


class ArchiveSolutionSource(Protocol):
    """Fuente de soluciones del archivo (el adaptador HTTP cumple por estructura)."""

    @property
    def requests_made(self) -> int: ...

    async def all_solutions(self) -> Sequence[ArchiveSolution]: ...

    async def released_since(self, day: date) -> Sequence[ArchiveSolution]: ...

    async def default_solutions(self) -> Sequence[ArchiveSolution]: ...

    async def solutions_for_planets(self, names: Collection[str]) -> Sequence[ArchiveSolution]: ...
