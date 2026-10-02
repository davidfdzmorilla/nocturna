"""Traducción de filas del NASA Exoplanet Archive a tipos de dominio (T74).

Funciones puras, sin IO. Formato observado de `pl_refname` en `ps` (ver
`tests/fixtures/exoplanet_archive/README.md`): un fragmento HTML
`<a refstr=... href=https://ui.adsabs.harvard.edu/abs/<bibcode>/abstract
target=ref> Faedi et al. 2011 </a>`, con el bibcode de ADS en el `href`. Un
preprint tiene `arXiv` en el bibcode (`2011arXiv1102.1375F`,
`2015arXiv150907750N`); un artículo de revista, no (`2026Natur.649..310L`).
"""

import html
import math
import re
from collections.abc import Mapping
from datetime import date
from urllib.parse import quote, unquote

from nocturna.domain.archive import ArchiveParameterValue, ArchiveSolution
from nocturna.domain.entities import (
    MeasurementLimit,
)
from nocturna.infrastructure.exoplanet_archive.client import ExoplanetArchiveUnavailable

_ARXIV_BIBCODE_RE = re.compile(r"abs/\d{4}arXiv(\d{4})\.?(\d{4,5})")
_ADS_BIBCODE_RE = re.compile(r"href=[\"']?https?://ui\.adsabs\.harvard\.edu/abs/([^/\s>\"']+)")
_TAG_RE = re.compile(r"<[^>]*>")
_WHITESPACE_RE = re.compile(r"\s+")

_OVERVIEW_URL = "https://exoplanetarchive.ipac.caltech.edu/overview/"
_LIMIT_BY_FLAG: Mapping[str, MeasurementLimit] = {
    "0": MeasurementLimit.NONE,
    "1": MeasurementLimit.UPPER,
    "-1": MeasurementLimit.LOWER,
}


def arxiv_id_from_refname(refname: str) -> str | None:
    """Identificador arXiv (`YYMM.NNNNN`, sin versión, como `Item.external_id`)
    del bibcode ADS del `href`, o `None` si la referencia no es un preprint."""
    match = _ARXIV_BIBCODE_RE.search(refname)
    if match is None:
        return None
    return f"{match.group(1)}.{match.group(2)}"


def reference_text(refname: str) -> str:
    """Texto visible de la referencia ("Livingston et al. 2026")."""
    text = html.unescape(_TAG_RE.sub("", refname))
    return _WHITESPACE_RE.sub(" ", text).strip()


def planet_overview_url(planet_name: str) -> str:
    return _OVERVIEW_URL + quote(planet_name, safe="")


def _parse_float(raw: str | None, column: str) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except ValueError as exc:
        raise ExoplanetArchiveUnavailable(
            f"valor no numérico {raw!r} en la columna {column} del Exoplanet Archive"
        ) from exc


def reference_key(refname: str) -> str:
    """Clave de la referencia: bibcode ADS del `href` si existe; si no, el
    texto visible normalizado (`reference_text`), nunca el HTML crudo."""
    match = _ADS_BIBCODE_RE.search(refname)
    if match is not None:
        return unquote(match.group(1))
    return reference_text(refname)


def _finite(raw: str | None, column: str) -> float | None:
    value = _parse_float(raw, column)
    if value is not None and not math.isfinite(value):
        raise ExoplanetArchiveUnavailable(
            f"valor no finito {raw!r} en la columna {column} del Exoplanet Archive"
        )
    return value


def _lim(raw: str | None, column: str) -> int | None:
    if raw is None:
        return None
    limit = _LIMIT_BY_FLAG.get(raw.strip())
    if limit is None:
        raise ExoplanetArchiveUnavailable(
            f"valor inesperado {raw!r} en la columna {column} del Exoplanet Archive"
        )
    return int(raw.strip())


def _param(
    row: Mapping[str, str | None], value: str, err1: str, err2: str, lim: str | None
) -> ArchiveParameterValue:
    return ArchiveParameterValue(
        value=_finite(row.get(value), value),
        err1=_finite(row.get(err1), err1),
        err2=_finite(row.get(err2), err2),
        lim=_lim(row.get(lim), lim) if lim is not None else None,
    )


def _flag(raw: str | None, column: str, *, nullable: bool) -> bool | None:
    if raw is None and nullable:
        return None
    if raw is not None and raw.strip() in ("0", "1"):
        return raw.strip() == "1"
    raise ExoplanetArchiveUnavailable(
        f"valor inesperado {raw!r} en la columna {column} del Exoplanet Archive"
    )


def _release_date(raw: str | None) -> date:
    if raw is None or len(raw) < 10 or (len(raw) > 10 and raw[10] not in " T"):
        raise ExoplanetArchiveUnavailable(
            f"releasedate nulo o mal formado ({raw!r}) en el Exoplanet Archive"
        )
    try:
        return date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise ExoplanetArchiveUnavailable(
            f"releasedate nulo o mal formado ({raw!r}) en el Exoplanet Archive"
        ) from exc


def archive_solution_from_ps_row(row: Mapping[str, str | None]) -> ArchiveSolution:
    """Fila de `ps` (29 columnas del snapshot) -> `ArchiveSolution`.

    Dato inválido (no numérico, no finito, `*lim` fuera de {-1, 0, 1},
    `releasedate` nulo o mal formado, flag fuera de 0/1) lanza
    `ExoplanetArchiveUnavailable`. `releasedate` con hora se trunca a fecha.
    """
    name = row.get("pl_name")
    if not name:
        raise ExoplanetArchiveUnavailable("fila de ps sin pl_name")
    refname = row.get("pl_refname") or ""
    return ArchiveSolution(
        pl_name=name,
        hostname=row.get("hostname") or "",
        pl_refname=refname,
        ref_key=reference_key(refname),
        ref_text=reference_text(refname),
        arxiv_id=arxiv_id_from_refname(refname),
        soltype=row.get("soltype"),
        releasedate=_release_date(row.get("releasedate")),
        pl_pubdate=row.get("pl_pubdate"),
        is_default=bool(_flag(row.get("default_flag"), "default_flag", nullable=False)),
        mass=_param(row, "pl_bmasse", "pl_bmasseerr1", "pl_bmasseerr2", "pl_bmasselim"),
        radius=_param(row, "pl_rade", "pl_radeerr1", "pl_radeerr2", "pl_radelim"),
        period=_param(row, "pl_orbper", "pl_orbpererr1", "pl_orbpererr2", "pl_orbperlim"),
        pl_bmassprov=row.get("pl_bmassprov"),
        st_rad=_param(row, "st_rad", "st_raderr1", "st_raderr2", None),
        st_mass=_param(row, "st_mass", "st_masserr1", "st_masserr2", None),
        discoverymethod=row.get("discoverymethod"),
        ttv_flag=_flag(row.get("ttv_flag"), "ttv_flag", nullable=True),
        pl_controv_flag=_flag(row.get("pl_controv_flag"), "pl_controv_flag", nullable=True),
    )
