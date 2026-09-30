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
from collections.abc import Mapping, Sequence
from urllib.parse import quote

from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.entities import (
    MeasuredParameter,
    MeasurementLimit,
    MeasurementUnit,
)
from nocturna.infrastructure.exoplanet_archive.client import ExoplanetArchiveUnavailable

_ARXIV_BIBCODE_RE = re.compile(r"abs/\d{4}arXiv(\d{4})\.?(\d{4,5})")
_TAG_RE = re.compile(r"<[^>]*>")
_WHITESPACE_RE = re.compile(r"\s+")

_OVERVIEW_URL = "https://exoplanetarchive.ipac.caltech.edu/overview/"
_MISSING_REFERENCE = "(sin referencia)"

# Solo `Mass` es masa verdadera; `Msini` y `Msin(i)/sin(i)` son cotas
# inferiores de la masa y no se comparan con una masa medida (decisión del
# autor, T74).
_TRUE_MASS_PROVENANCE = "Mass"

# parámetro -> (valor, err1, err2, lim, unidad)
_COLUMNS: Mapping[MeasuredParameter, tuple[str, str, str, str, MeasurementUnit]] = {
    MeasuredParameter.MASS: (
        "pl_bmasse",
        "pl_bmasseerr1",
        "pl_bmasseerr2",
        "pl_bmasselim",
        MeasurementUnit.M_EARTH,
    ),
    MeasuredParameter.RADIUS: (
        "pl_rade",
        "pl_radeerr1",
        "pl_radeerr2",
        "pl_radelim",
        MeasurementUnit.R_EARTH,
    ),
    MeasuredParameter.PERIOD: (
        "pl_orbper",
        "pl_orbpererr1",
        "pl_orbpererr2",
        "pl_orbperlim",
        MeasurementUnit.DAY,
    ),
}

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


def _parse_error(raw: str | None, column: str) -> float | None:
    value = _parse_float(raw, column)
    if value is None or not math.isfinite(value):
        return None
    return abs(value)


def solutions_from_ps_rows(
    rows: Sequence[Mapping[str, str | None]], parameter: MeasuredParameter
) -> tuple[CatalogSolution, ...]:
    """Una `CatalogSolution` por fila de `ps` que tenga un valor válido de `parameter`.

    Se omiten las filas sin valor, con valor no positivo o no finito y, para
    la masa, las que no son masa verdadera (`pl_bmassprov != "Mass"`).
    """
    value_col, err1_col, err2_col, lim_col, unit = _COLUMNS[parameter]
    solutions: list[CatalogSolution] = []
    for row in rows:
        if parameter == MeasuredParameter.MASS and row.get("pl_bmassprov") != _TRUE_MASS_PROVENANCE:
            continue
        value = _parse_float(row.get(value_col), value_col)
        if value is None or not math.isfinite(value) or value <= 0:
            continue
        lim_raw = row.get(lim_col)
        limit = (
            _LIMIT_BY_FLAG.get(lim_raw.strip()) if lim_raw is not None else MeasurementLimit.NONE
        )
        if limit is None:
            raise ExoplanetArchiveUnavailable(
                f"valor inesperado {lim_raw!r} en la columna {lim_col} del Exoplanet Archive"
            )
        planet_name = row.get("pl_name")
        if not planet_name:
            raise ExoplanetArchiveUnavailable("fila de ps sin pl_name")
        refname = row.get("pl_refname") or ""
        solutions.append(
            CatalogSolution(
                planet_name=planet_name,
                parameter=parameter,
                value=value,
                err_plus=_parse_error(row.get(err1_col), err1_col),
                err_minus=_parse_error(row.get(err2_col), err2_col),
                unit=unit,
                limit=limit,
                reference=reference_text(refname) or _MISSING_REFERENCE,
                is_default=row.get("default_flag") == "1",
                arxiv_id=arxiv_id_from_refname(refname),
            )
        )
    return tuple(solutions)
