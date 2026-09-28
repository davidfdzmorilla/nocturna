"""Experimento de viabilidad del cruce con el NASA Exoplanet Archive (T71).

## Por qué existe

T72-T78 (rol nuevo "redactor de tensiones", cálculo determinista de
discrepancias con el catálogo, etc.) solo se construyen si este experimento
demuestra que hay tensiones reales que contar. Este script no toca el
pipeline ni gasta un solo token: lee los `Item` de `astro-ph.EP` que ya
tienen `Reading` en la base local, los cruza con el NASA Exoplanet Archive
por su API TAP pública (sin autenticación), y emite un informe en texto
plano con los recuentos que pide el plan de T71 (ver `docs/PLAN_TAREAS.md`).

**No hay decisión de umbral aquí.** El informe da los tramos de sigma para
que el autor decida si el experimento merece continuar (T72) o se
replantea. La fórmula de sigma es deliberadamente provisional: T73 decide
la definitiva (errores asimétricos, medida de referencia, umbral).

## Por qué vive fuera de `src/nocturna/`

Mismo motivo que `seed_demo.py`: no es importable desde el paquete
(`[tool.hatch.build.targets.wheel].packages` no lo incluye) y no se
registra en `[project.scripts]` (`nocturna` sigue siendo la única entrada).
No hay ningún camino por el que este script acabe empaquetado.

## Guarda: `NOCTURNA_ALLOW_ARCHIVE_QUERY`

Mismo patrón que `NOCTURNA_ALLOW_SEED`: sin la variable en el entorno, el
script no toca la base de datos ni sale a la red, y explica por `stderr`
qué hace y cómo autorizarlo. Este script SÍ sale a Internet (dos servicios
públicos del Exoplanet Archive, sin autenticación, sin coste de
suscripción) y SÍ lee PostgreSQL (en modo estrictamente de solo lectura,
ver `load_ep_readings`): una ejecución accidental no debe pasar en
silencio.

## Presupuesto de peticiones HTTP

Tope duro de 150 peticiones por ejecución (`ArchiveClient.max_requests`,
infranqueable: se cuenta ANTES de pedir, no después), con un subtope de 40
para el servicio de alias (solo se usa para nombres CON dígitos que no
emparejaron por la vía normal) y un espaciado de cortesía de 2 segundos
entre peticiones. Sin reintentos: un fallo se propaga tal cual.

## Sin Claude, sin `application/`

Este script no importa `claude_agent_sdk`, ni `nocturna.application`, ni
`nocturna.infrastructure.llm`. No hay ningún camino de gasto de la
suscripción aquí: es lectura de base de datos + HTTP a un servicio público.

## Uso

    docker compose up -d
    cd backend && uv run alembic upgrade head
    NOCTURNA_ALLOW_ARCHIVE_QUERY=1 uv run python scripts/exoplanet_viability.py
    NOCTURNA_ALLOW_ARCHIVE_QUERY=1 uv run python scripts/exoplanet_viability.py \\
        --json /tmp/casos.json
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import math
import os
import re
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import UUID

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session

from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import Settings
from nocturna.infrastructure.db.models import AgentCallRow, ItemRow, ReadingRow
from nocturna.infrastructure.db.session import create_db_engine, create_session_factory

_ALLOW_ENV_VAR = "NOCTURNA_ALLOW_ARCHIVE_QUERY"

# Constantes de conversión Júpiter -> Tierra. Los tests del contrato
# (`tests/test_exoplanet_viability_script.py`) declaran su propia copia de
# estos valores y comparan contra la salida de `parse_measurements`: si
# alguno de los dos cambia sin el otro, esos tests lo notan.
R_JUP_IN_R_EARTH = 11.209
M_JUP_IN_M_EARTH = 317.83

_TOTAL_REQUEST_CAP = 150
_ALIAS_SUBCAP = 40
_COURTESY_SPACING_S = 2.0
_MAX_RESPONSE_BYTES = 20 * 1024 * 1024
_USER_AGENT = "nocturna/0.1.0"
_TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
_ALIAS_URL = "https://exoplanetarchive.ipac.caltech.edu/cgi-bin/Lookup/nph-aliaslookup.py"
_HTTP_TIMEOUT = httpx.Timeout(30.0)

_REMEDY = (
    f"exoplanet_viability.py saltado: falta {_ALLOW_ENV_VAR} en el entorno.\n\n"
    "Este script hace peticiones HTTP reales al NASA Exoplanet Archive (TAP "
    "sync y el servicio de alias, ambos sin autenticación) y lee la base de "
    "datos configurada (por defecto la de docker compose, ver "
    "Settings.database_url) en una transacción de SOLO LECTURA. No escribe "
    "nada en ningún sitio y no gasta un solo token de la suscripción de "
    f"Claude. Tope duro de {_TOTAL_REQUEST_CAP} peticiones por ejecución, "
    f"con {_COURTESY_SPACING_S} s de cortesía entre cada una.\n\n"
    f"Para autorizarlo explícitamente:\n\n"
    f"    {_ALLOW_ENV_VAR}=1 uv run python scripts/exoplanet_viability.py\n"
)


def _require_opt_in() -> None:
    if _ALLOW_ENV_VAR not in os.environ:
        print(_REMEDY, file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# normalize_name
# ---------------------------------------------------------------------------

_DASH_VARIANTS = "‐‑‒–—−"
_DIGIT_LETTER_BOUNDARY = re.compile(r"(?<=[0-9])(?=[a-zA-Z])")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Normaliza un nombre de objeto para comparar de forma robusta.

    Unifica variantes unicode del guion, colapsa espacios repetidos e
    inserta un espacio entre un dígito y la letra que lo sigue sin espacio
    ("WASP-12b" -> "WASP-12 b"), para que las dos notaciones habituales del
    mismo planeta normalicen igual. Todo en minúsculas.
    """
    text = name.strip()
    for ch in _DASH_VARIANTS:
        text = text.replace(ch, "-")
    text = _WHITESPACE_RE.sub(" ", text)
    text = _DIGIT_LETTER_BOUNDARY.sub(" ", text)
    return text.strip().lower()


# ---------------------------------------------------------------------------
# CatalogIndex / match_object
# ---------------------------------------------------------------------------

_ALIAS_COLUMNS = ("hostname", "hd_name", "hip_name", "tic_id")


@dataclass(frozen=True, slots=True)
class CatalogIndex:
    """Índice de nombres del archivo: planeta exacto y alias de sistema."""

    planet_by_name: dict[str, str]
    planets_by_alias: dict[str, tuple[str, ...]]

    @classmethod
    def from_rows(cls, rows: list[dict[str, str | None]]) -> CatalogIndex:
        """Construye el índice a partir de filas de `pscomppars`.

        Columnas esperadas: `pl_name,hostname,hd_name,hip_name,tic_id,
        sy_pnum` (`gaia_id` no existe en el archivo). `sy_pnum` no se usa
        aquí: la ambigüedad de un alias de sistema (`host_multi`) se deriva
        de cuántos `pl_name` distintos referencia ese alias en estas filas,
        no del contador `sy_pnum`.
        """
        planet_by_name: dict[str, str] = {}
        alias_map: dict[str, list[str]] = {}
        for row in rows:
            pl_name = row.get("pl_name")
            if not pl_name:
                continue
            planet_by_name[normalize_name(pl_name)] = pl_name
            for alias_col in _ALIAS_COLUMNS:
                alias = row.get(alias_col)
                if not alias:
                    continue
                norm_alias = normalize_name(alias)
                candidates = alias_map.setdefault(norm_alias, [])
                if pl_name not in candidates:
                    candidates.append(pl_name)
        planets_by_alias = {key: tuple(value) for key, value in alias_map.items()}
        return cls(planet_by_name=planet_by_name, planets_by_alias=planets_by_alias)


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Resultado de emparejar un nombre de `Reading.objects` contra el archivo."""

    kind: Literal["planet", "host_single", "host_multi", "unmatched"]
    pl_name: str | None
    candidates: tuple[str, ...] = ()


def match_object(raw_name: str, index: CatalogIndex) -> MatchResult:
    norm = normalize_name(raw_name)
    exact = index.planet_by_name.get(norm)
    if exact is not None:
        return MatchResult(kind="planet", pl_name=exact)

    candidates = index.planets_by_alias.get(norm)
    if candidates is not None:
        if len(candidates) == 1:
            return MatchResult(kind="host_single", pl_name=candidates[0])
        return MatchResult(kind="host_multi", pl_name=None, candidates=candidates)

    return MatchResult(kind="unmatched", pl_name=None)


# ---------------------------------------------------------------------------
# Measurement / parse_measurements
# ---------------------------------------------------------------------------

_ParameterName = Literal["mass_earth", "radius_earth", "period_days"]


@dataclass(frozen=True, slots=True)
class Measurement:
    parameter: _ParameterName
    value: float
    err_plus: float
    err_minus: float


# Grupo numérico: dígitos con como mucho UN separador decimal (coma o
# punto) seguido de más dígitos. Deliberadamente NO admite `[\d.,]*` suelto:
# esa forma se come la puntuación de la frase (una coma de lista, un punto
# final) como si fuera parte del número y `_to_float` explota con
# `ValueError` (bug B1 de la revisión de T71).
_NUM = r"\d+(?:[.,]\d+)?"
_VALUE_NUM = rf"-?{_NUM}"
# Lookbehind sugerido por el reviewer: sin él, el grupo `value` puede
# empezar a mitad de OTRO número (p. ej. casar "23" dentro de "123" si el
# resto de la frase encajase por casualidad con el patrón). Un dígito, un
# punto o una coma justo antes del inicio del valor lo descartan.
_VALUE_LOOKBEHIND = r"(?<![\d.,])"

_PM_PATTERN = re.compile(
    rf"{_VALUE_LOOKBEHIND}(?P<value>{_VALUE_NUM})\s*\$?\s*(?:\\pm|±|\+/-|\+-)\s*\$?\s*"
    rf"(?P<error>{_NUM})"
)
# `\$?` opcional entre el valor y `^`: notación real vista en la BD como
# `2.13$^{+0.2}_{-0.1}$ M_J` (un `$` cierra/abre modo matemático justo ahí).
_ASYM_PATTERN = re.compile(
    rf"{_VALUE_LOOKBEHIND}(?P<value>{_VALUE_NUM})\$?\s*\^\{{\+(?P<err_plus>{_NUM})\}}_?"
    rf"\{{-(?P<err_minus>{_NUM})\}}"
)

# Clase de caracteres "conectores" que pueden aparecer entre el símbolo
# (R/M) y la palabra "Jup"/"J"/"Jupiter"/"oplus" en notación LaTeX:
# subíndices, llaves, barras invertidas, signos de dólar y espacios.
# Deliberadamente SIN `re.IGNORECASE`: con mayúsculas/minúsculas
# indistintas, la "r" de "for" o la "m" de "mass" emparejaban como si
# fueran el símbolo R/M de una unidad (bug B2 de la revisión de T71).
_CONNECTOR = r"[_\s\$\{\}\\]*"
# Envoltorio opcional entre el conector y el núcleo de la unidad
# ("Jup"/"J"/"oplus"): `\rm`, `\text{` o `\mathrm{` (el backslash de cada
# uno ya lo consume `_CONNECTOR`, que incluye `\`).
_UNIT_WRAPPER = r"(?:rm|text\{|mathrm\{)?"
_JUP_CORE = r"(?:Jupiter\b|J(?:up)?\b)"
_OPLUS_CORE = r"oplus\b"
_MASS_JUP_RE = re.compile(rf"M{_CONNECTOR}{_UNIT_WRAPPER}{_CONNECTOR}{_JUP_CORE}")
_RADIUS_JUP_RE = re.compile(rf"R{_CONNECTOR}{_UNIT_WRAPPER}{_CONNECTOR}{_JUP_CORE}")
_MASS_EARTH_SYMBOL_RE = re.compile(rf"M{_CONNECTOR}{_UNIT_WRAPPER}{_CONNECTOR}{_OPLUS_CORE}")
_RADIUS_EARTH_SYMBOL_RE = re.compile(rf"R{_CONNECTOR}{_UNIT_WRAPPER}{_CONNECTOR}{_OPLUS_CORE}")
_EARTH_MASS_RE = re.compile(r"masas?\s+terrestres|earth\s+mass(?:es)?", re.IGNORECASE)
_EARTH_RADIUS_RE = re.compile(r"radios?\s+terrestres|earth\s+radi(?:us|i)", re.IGNORECASE)
# Frase textual "Jupiter masses"/"Jupiter radii" (siempre con mayúscula en
# los abstracts reales, por eso sin `re.IGNORECASE`, igual que los símbolos
# de arriba).
_JUPITER_MASS_PHRASE_RE = re.compile(r"Jupiter\s+masses?\b")
_JUPITER_RADIUS_PHRASE_RE = re.compile(r"Jupiter\s+radi(?:us|i)\b")
# "days"/"day"/"días"/"día"/"d" (abreviatura de una sola letra, habitual
# junto a "P ="): la unidad de periodo, nunca de rotación (ver
# `_valid_orbital_backward`).
_DAYS_RE = re.compile(r"\bdays?\b|\bd[ií]as?\b|\bd\b", re.IGNORECASE)
_ORBITAL_CONTEXT_RE = re.compile(r"orbital|per[ií]odo\s+orbital|\bP\s*=", re.IGNORECASE)
_ROTATION_BLOCK_RE = re.compile(r"rotation|rotaci[oó]n|\bspin\b", re.IGNORECASE)
_DIGIT_ANY_RE = re.compile(r"\d")

# Separadores triviales que pueden aparecer entre el error y la unidad
# (o el factor de escala) sin que cuenten como "otro texto en medio": un
# espacio, `$`, `~`, `)` o los espacios finos de LaTeX `\,`/`\;`.
_GAP_RE = re.compile(r"(?:\s|\$|~|\)|\\,|\\;)*")
# Igual que el hueco anterior, pero también admite la llave de cierre de
# la unidad (`M_{\rm J}}` deja un `}` colgando que la unidad no consume,
# por el `\b` de cierre) al mirar qué viene DESPUÉS de la unidad.
_POST_UNIT_GAP_RE = re.compile(r"(?:\s|\$|~|\)|\}|\\,|\\;)*")
# Envoltorios que pueden aparecer ANTES del símbolo M/R (no entre M/R y
# Jup/oplus, eso ya lo consume `_UNIT_WRAPPER`/`_CONNECTOR`): `{\rm ` con
# su espacio final (`~{\rm M_\oplus}`), `\mathrm{`, `\text{`, o una llave
# suelta de apertura/cierre. Sin este consumo previo la unidad nunca se
# reconoce, porque el patrón exige empezar literalmente por `M`/`R`.
_WRAPPER_OPEN_TOKENS = ("{\\rm ", "\\mathrm{", "\\text{", "{", "}")

_SCALE_FACTOR_RE = re.compile(r"\\times|\\cdot|×|x\s*10|e[-+]?\d+|10\^")
_INEQUALITY_TOKEN = r"<|>|\\lesssim|\\gtrsim|\\la|\\ga|≲|≳|≤|≥"
_INEQUALITY_RE = re.compile(_INEQUALITY_TOKEN)
_INEQUALITY_TAIL_RE = re.compile(rf"(?:{_INEQUALITY_TOKEN})(?:\s|\$)*$")

_BACKWARD_CONTEXT_WINDOW = 100
# Separador de lista trivial: coma, "and"/"y", espacios, `$` (en cualquier
# orden/repetición). Solo si TODO el texto entre dos valores consecutivos
# es esto, la unidad pegada al último valor de la lista se propaga hacia
# atrás (ver `_chain_end`). Este acoplamiento con el descarte de
# `valores_multiples` en `_multi_value_parameters`/`_cases_for_reading` es
# deliberado: si la propagación uniera valores de un mismo parámetro que
# en realidad son distintos (no una lista homogénea), el descarte por
# "valores_multiples" evita quedarse con cualquiera de ellos en silencio.
_LIST_SEPARATOR_RE = re.compile(r"\A[\s,\$]*(?:and|y)?[\s,\$]*\Z", re.IGNORECASE)


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "."))


def _consume_gap(text: str) -> int:
    match = _GAP_RE.match(text)
    return match.end() if match else 0


def _consume_gap_and_wrappers(text: str) -> int:
    """Longitud del hueco trivial + envoltorios consumibles al principio de `text`.

    Alterna, cuantas veces haga falta, entre consumir una racha de hueco
    trivial (`_GAP_RE`) y consumir uno de `_WRAPPER_OPEN_TOKENS`, hasta que
    ninguno de los dos avance más. Es lo que permite reconocer tanto
    `M_{\\rm J}` (envoltorio DESPUÉS de M, ya lo consume el propio patrón
    de unidad) como `{\\rm M_\\oplus}` (envoltorio ANTES de M, que sin este
    consumo previo nunca se ve porque el patrón exige empezar por `M`/`R`).
    """
    pos = 0
    while True:
        gap_match = _GAP_RE.match(text, pos)
        if gap_match is not None and gap_match.end() > pos:
            pos = gap_match.end()
            continue
        for token in _WRAPPER_OPEN_TOKENS:
            if text.startswith(token, pos):
                pos += len(token)
                break
        else:
            break
    return pos


def _has_scale_factor(text_after_error: str) -> bool:
    """Factor de escala explícito justo tras el error (hueco trivial de por
    medio): `\\times`/`×`/`\\cdot`, una "x" seguida de una potencia de diez
    (`x 10`), notación científica (`e-3`) o una potencia de diez suelta sin
    operador (`10^{-3}`). El valor va multiplicado por ese factor y no es
    la medida real (bug B3 de la revisión de T71, sección 19.2)."""
    gap_len = _consume_gap(text_after_error)
    return _SCALE_FACTOR_RE.match(text_after_error[gap_len:]) is not None


def _preceded_by_inequality(text: str, start: int) -> bool:
    """Una desigualdad (`<`, `>`, `\\lesssim`, `\\gtrsim`, `\\la`, `\\ga`, o
    su variante unicode `≲`/`≳`/`≤`/`≥`, opcionalmente envuelta en `$...$`)
    inmediatamente antes del valor: es un límite, no una medida."""
    backward = text[max(0, start - _BACKWARD_CONTEXT_WINDOW) : start]
    return _INEQUALITY_TAIL_RE.search(backward) is not None


def _followed_by_inequality(text: str, pos: int) -> bool:
    """Igual que `_preceded_by_inequality`, pero mirando hacia delante desde
    justo después de la unidad ya reconocida (`M_J < upper bound`)."""
    forward = text[pos : pos + _BACKWARD_CONTEXT_WINDOW]
    match = _POST_UNIT_GAP_RE.match(forward)
    remaining = forward[match.end() :] if match else forward
    return _INEQUALITY_RE.match(remaining) is not None


def _valid_orbital_backward(backward: str) -> bool:
    """Contexto orbital inmediatamente antes del valor: la ÚLTIMA aparición
    de "orbital"/"período orbital"/"P =" en `backward`, sin que entre ese
    punto y el valor haya otro número o la palabra "rotation"/"rotación"/
    "spin" -- eso sería el periodo de rotación de la estrella, no el
    periodo orbital del planeta."""
    last_match = None
    for match in _ORBITAL_CONTEXT_RE.finditer(backward):
        last_match = match
    if last_match is None:
        return False
    between = backward[last_match.end() :]
    if _DIGIT_ANY_RE.search(between) or _ROTATION_BLOCK_RE.search(between):
        return False
    return True


_SYMBOLIC_UNIT_PATTERNS: tuple[tuple[re.Pattern[str], _ParameterName, float], ...] = (
    (_MASS_JUP_RE, "mass_earth", M_JUP_IN_M_EARTH),
    (_RADIUS_JUP_RE, "radius_earth", R_JUP_IN_R_EARTH),
    (_MASS_EARTH_SYMBOL_RE, "mass_earth", 1.0),
    (_RADIUS_EARTH_SYMBOL_RE, "radius_earth", 1.0),
)
_PHRASE_UNIT_PATTERNS: tuple[tuple[re.Pattern[str], _ParameterName, float], ...] = (
    (_EARTH_MASS_RE, "mass_earth", 1.0),
    (_EARTH_RADIUS_RE, "radius_earth", 1.0),
    (_JUPITER_MASS_PHRASE_RE, "mass_earth", M_JUP_IN_M_EARTH),
    (_JUPITER_RADIUS_PHRASE_RE, "radius_earth", R_JUP_IN_R_EARTH),
)


def _classify_immediate_unit(
    text: str, value_start: int, error_end: int
) -> tuple[_ParameterName, float, int] | None:
    """Unidad reconocida INMEDIATAMENTE tras el error de `text[error_end:]`.

    "Inmediatamente" admite entre el error y la unidad, en cualquier
    orden/repetición, solo hueco trivial y los envoltorios de
    `_WRAPPER_OPEN_TOKENS` (`_consume_gap_and_wrappers`). Cualquier otro
    carácter en medio -- una palabra, una coma suelta, un punto -- impide
    el match: contrato estricto de la sección 19, sin ventanas de
    búsqueda. Para el periodo, además, exige contexto orbital
    inmediatamente antes del VALOR (`_valid_orbital_backward` sobre
    `value_start`), nunca de rotación. Devuelve `(parametro, factor,
    fin_absoluto_de_la_unidad)` o `None`.
    """
    gap_len = _consume_gap_and_wrappers(text[error_end:])
    base = error_end + gap_len
    candidate = text[base:]

    for pattern, parameter, factor in _SYMBOLIC_UNIT_PATTERNS:
        found = pattern.match(candidate)
        if found is not None:
            return parameter, factor, base + found.end()

    day_found = _DAYS_RE.match(candidate)
    if day_found is not None:
        backward = text[max(0, value_start - _BACKWARD_CONTEXT_WINDOW) : value_start]
        if _valid_orbital_backward(backward):
            return "period_days", 1.0, base + day_found.end()

    for pattern, parameter, factor in _PHRASE_UNIT_PATTERNS:
        found = pattern.match(candidate)
        if found is not None:
            return parameter, factor, base + found.end()

    return None


def _list_gap_is_trivial(text: str, prev_end: int, next_start: int) -> bool:
    return _LIST_SEPARATOR_RE.match(text[prev_end:next_start]) is not None


def _chain_end(text: str, matches: list[tuple[int, int, float, float, float]], index: int) -> int:
    """Índice más lejano alcanzable desde `index` encadenando SOLO huecos
    que son separador de lista trivial entre cada par CONSECUTIVO de
    `matches` (ver `_LIST_SEPARATOR_RE`). Si el último elemento alcanzable
    tiene su propia unidad inmediata, esa unidad se propaga hacia atrás a
    `index` (y a todos los intermedios sin unidad propia) en
    `_parse_measurements_with_spans`."""
    j = index
    while j + 1 < len(matches) and _list_gap_is_trivial(text, matches[j][1], matches[j + 1][0]):
        j += 1
    return j


def _parse_measurements_with_spans(text: str) -> list[tuple[Measurement, int, int]]:
    """Como `parse_measurements`, pero devuelve también `(start, end)` del
    valor+error (no de la unidad) en `text`, para que `Case.source_snippet`
    pueda recortar un fragmento legible alrededor del valor de la vía (a)."""
    matches: list[tuple[int, int, float, float, float]] = []
    for m in _PM_PATTERN.finditer(text):
        value = _to_float(m.group("value"))
        error = _to_float(m.group("error"))
        matches.append((m.start(), m.end(), value, error, error))
    for m in _ASYM_PATTERN.finditer(text):
        value = _to_float(m.group("value"))
        err_plus = _to_float(m.group("err_plus"))
        err_minus = _to_float(m.group("err_minus"))
        matches.append((m.start(), m.end(), value, err_plus, err_minus))
    matches.sort(key=lambda item: item[0])

    results: list[tuple[Measurement, int, int]] = []
    for index, (start, end, value, err_plus, err_minus) in enumerate(matches):
        if err_plus == 0 or err_minus == 0:
            continue
        if _preceded_by_inequality(text, start):
            continue

        after_error = text[end:]
        if _has_scale_factor(after_error):
            continue

        classification = _classify_immediate_unit(text, start, end)
        if classification is None:
            chain_end = _chain_end(text, matches, index)
            if chain_end > index:
                tail_start, tail_end = matches[chain_end][0], matches[chain_end][1]
                classification = _classify_immediate_unit(text, tail_start, tail_end)

        if classification is None:
            continue
        parameter, factor, unit_end_abs = classification
        if _followed_by_inequality(text, unit_end_abs):
            continue

        results.append(
            (
                Measurement(
                    parameter=parameter,
                    value=value * factor,
                    err_plus=err_plus * factor,
                    err_minus=err_minus * factor,
                ),
                start,
                end,
            )
        )
    return results


def parse_measurements(text: str) -> list[Measurement]:
    """Parser determinista y ESTRICTO de medidas con error explícito (vía
    (a) de T71, contrato endurecido en la sección 19 tras el segundo
    rechazo de revisión).

    Solo produce una `Measurement` cuando hay un valor CON error explícito
    y DISTINTO de cero (simétrico `\\pm`/±/+/- o asimétrico `^{+x}_{-y}`) y
    una unidad reconocida de masa, radio o periodo ORBITAL (nunca de
    rotación, metalicidad, movimiento propio o porcentaje) INMEDIATAMENTE
    tras el error -- sin ventanas de búsqueda hacia delante, ver
    `_classify_immediate_unit`. Un valor sin error, con error cero, un
    límite superior/inferior ("< 5 M_J", "upper limit of...",
    "\\lesssim"/"\\gtrsim"/"\\la"/"\\ga"/"≲"/"≳"/"≤"/"≥") o multiplicado por
    un factor de escala explícito (`\\times`/`×`/`\\cdot`/`x 10`/`e-3`/
    `10^{...}`) nunca produce una `Measurement`.

    Excepción: en una lista de valores (`$a$, $b$, y $c$ UNIDAD), la
    unidad pegada al ÚLTIMO elemento se propaga hacia atrás a los
    anteriores, pero solo si todo lo que separa cada par de valores
    consecutivos es un separador de lista trivial (`_LIST_SEPARATOR_RE`) --
    ver `_chain_end`. Ninguna otra unidad "se ve" desde un valor vecino sin
    unidad propia (bug B2 de la revisión de T71).
    """
    return [measurement for measurement, _start, _end in _parse_measurements_with_spans(text)]


# ---------------------------------------------------------------------------
# arxiv_bibcode_fragment / CatalogSolution / own_solutions / sigma
# ---------------------------------------------------------------------------

_NEW_STYLE_ARXIV_ID_RE = re.compile(r"^(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?$")


def arxiv_bibcode_fragment(external_id: str) -> str | None:
    """Fragmento de bibcode ADS para un `external_id` de arXiv "nuevo estilo".

    `None` para identificadores "antiguo estilo" (`astro-ph/0601001`): esos
    no tienen el fragmento `arXivYYMMnnnnn` que usa `pl_refname` en el
    archivo, y no vale la pena reconstruirlo a mano.
    """
    match = _NEW_STYLE_ARXIV_ID_RE.match(external_id)
    if match is None:
        return None
    yy, mm, num = match.groups()
    return f"arXiv{yy}{mm}{num}"


@dataclass(frozen=True, slots=True)
class CatalogSolution:
    """Una fila de la tabla `ps` (soluciones múltiples), ya reducida a un parámetro."""

    pl_name: str
    refname: str
    default_flag: bool
    measurement: Measurement | None
    hostname: str | None = None


def own_solutions(solutions: list[CatalogSolution], external_id: str) -> list[CatalogSolution]:
    """Filas de `solutions` que son la propia solución del paper (vía (b)).

    Reconoce tres notaciones de `pl_refname` para el mismo preprint: el
    fragmento de bibcode ADS `arXivYYMMnnnnn` (el habitual en el archivo),
    un enlace `arxiv.org/abs/<id>` y una cita `arXiv:<id>` -- estas dos
    últimas con o sin sufijo de versión (`v2`), que el fragmento de
    bibcode ya ignora.
    """
    fragment = arxiv_bibcode_fragment(external_id)
    if fragment is None:
        return []
    core_id = re.sub(r"v\d+$", "", external_id)
    pattern = re.compile(
        rf"{re.escape(fragment)}"
        rf"|arxiv\.org/abs/{re.escape(core_id)}(?:v\d+)?"
        rf"|arXiv:{re.escape(core_id)}(?:v\d+)?",
        re.IGNORECASE,
    )
    return [s for s in solutions if pattern.search(s.refname)]


def sigma(paper: Measurement, prior: Measurement) -> float:
    """Discrepancia en sigma, fórmula PROVISIONAL (T73 decide la definitiva).

    Si los valores son iguales, 0.0 sin importar los errores. Si no, se usa
    en cada medida el error del lado que mira hacia la otra medida (el que
    reduce la distancia), no el que se aleja. Ambos parsers (`parse_
    measurements`, `parse_catalog_row`) ya descartan una medida con error
    cero antes de construir un `Measurement`, así que en el uso normal el
    denominador combinado nunca es cero con valores distintos; si se llama
    esta función directamente con un `Measurement` construido a mano cuyo
    lado relevante tiene error cero en las dos medidas, `sigma` lanza
    `ValueError` en vez de disfrazar ese dato inválido del catálogo como
    una medida de tensión (`distance` sin dividir).
    """
    if paper.value == prior.value:
        return 0.0
    if paper.value > prior.value:
        paper_err = paper.err_minus
        prior_err = prior.err_plus
    else:
        paper_err = paper.err_plus
        prior_err = prior.err_minus
    distance = abs(paper.value - prior.value)
    denominator = math.sqrt(paper_err**2 + prior_err**2)
    if denominator == 0:
        raise ValueError(
            "sigma: denominador combinado 0 con valores distintos "
            f"(paper.value={paper.value}, prior.value={prior.value}); no se puede "
            "expresar la discrepancia en unidades de un error que no existe"
        )
    return distance / denominator


# ---------------------------------------------------------------------------
# parse_catalog_row
# ---------------------------------------------------------------------------

# columna de valor, error+, error-, límite -- por parámetro.
_PS_PARAMETER_COLUMNS: dict[_ParameterName, tuple[str, str, str, str]] = {
    "mass_earth": ("pl_bmasse", "pl_bmasseerr1", "pl_bmasseerr2", "pl_bmasselim"),
    "radius_earth": ("pl_rade", "pl_radeerr1", "pl_radeerr2", "pl_radelim"),
    "period_days": ("pl_orbper", "pl_orbpererr1", "pl_orbpererr2", "pl_orbperlim"),
}


def _parse_one_measurement(
    row: dict[str, str | None], parameter: _ParameterName
) -> Measurement | None:
    value_col, err1_col, err2_col, lim_col = _PS_PARAMETER_COLUMNS[parameter]
    lim = row.get(lim_col)
    if lim is not None and lim != "0":
        return None
    value_raw = row.get(value_col)
    err1_raw = row.get(err1_col)
    err2_raw = row.get(err2_col)
    if value_raw is None or err1_raw is None or err2_raw is None:
        return None
    err_plus = abs(float(err1_raw))
    err_minus = abs(float(err2_raw))
    if err_plus == 0 or err_minus == 0:
        # Error 0 es un dato inválido (no una incertidumbre real de cero):
        # igual que si faltara el error, no produce una Measurement.
        return None
    return Measurement(
        parameter=parameter,
        value=float(value_raw),
        err_plus=err_plus,
        err_minus=err_minus,
    )


def parse_catalog_row(row: dict[str, str | None]) -> list[CatalogSolution]:
    """Convierte una fila de la tabla `ps` en tres `CatalogSolution` (una por parámetro).

    `measurement` sale `None` cuando ese parámetro no tiene valor, tiene el
    flag `*lim` distinto de `"0"`, o le falta alguno de los dos errores.
    """
    pl_name = row["pl_name"]
    assert pl_name is not None
    hostname = row.get("hostname")
    refname = row["pl_refname"]
    assert refname is not None
    default_flag = row.get("default_flag") == "1"

    return [
        CatalogSolution(
            pl_name=pl_name,
            hostname=hostname,
            refname=refname,
            default_flag=default_flag,
            measurement=_parse_one_measurement(row, parameter),
        )
        for parameter in _PS_PARAMETER_COLUMNS
    ]


# ---------------------------------------------------------------------------
# ArchiveClient
# ---------------------------------------------------------------------------


class ArchiveClientError(Exception):
    """Cualquier fallo al hablar con el NASA Exoplanet Archive."""


def _looks_like_votable_error(text: str) -> bool:
    stripped = text.lstrip()
    return stripped.startswith("<?xml") or "VOTABLE" in text or "QUERY_STATUS" in text


def _parse_csv_rows(text: str) -> list[dict[str, str | None]]:
    reader = csv.DictReader(io.StringIO(text))
    return [{key: (value if value != "" else None) for key, value in row.items()} for row in reader]


class ArchiveClient:
    """Cliente TAP + alias del NASA Exoplanet Archive, sin autenticación.

    `http` es un `httpx.Client` inyectado (permite `httpx.MockTransport` en
    los tests, sin salir a la red). `max_requests` es un tope INFRANQUEABLE:
    se comprueba y se cuenta ANTES de enviar la petición, así que la
    petición número `max_requests + 1` nunca sale. Sin reintentos: un fallo
    de transporte o un HTTP != 200 se propaga como `ArchiveClientError`.
    """

    def __init__(
        self,
        http: httpx.Client,
        *,
        max_requests: int,
        spacing_s: float,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http = http
        self._max_requests = max_requests
        self._spacing_s = spacing_s
        self._sleep = sleep
        self._requests_made = 0
        self._alias_requests_made = 0

    @property
    def requests_made(self) -> int:
        return self._requests_made

    def _throttle_and_count(self) -> None:
        if self._requests_made >= self._max_requests:
            raise ArchiveClientError(
                f"se alcanzó el límite de cortesía de max_requests={self._max_requests} "
                "peticiones al Exoplanet Archive en esta ejecución"
            )
        if self._requests_made > 0:
            self._sleep(self._spacing_s)
        self._requests_made += 1

    def _get(self, url: str, params: dict[str, str]) -> bytes:
        response = self._http.get(
            url,
            params=params,
            headers={"User-Agent": _USER_AGENT},
            timeout=_HTTP_TIMEOUT,
        )
        if response.status_code != 200:
            raise ArchiveClientError(
                f"el Exoplanet Archive respondió {response.status_code} para {url}: "
                f"{response.text[:500]!r}"
            )
        content = response.content
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ArchiveClientError(
                f"la respuesta de {len(content)} bytes supera el tope MAX_RESPONSE_BYTES "
                f"({_MAX_RESPONSE_BYTES}); respuesta demasiado grande para procesarla"
            )
        return content

    def query_csv(self, adql: str) -> list[dict[str, str | None]]:
        """TAP sync, `format=csv`. Lanza si HTTP != 200 o si el cuerpo es un VOTABLE de error."""
        self._throttle_and_count()
        content = self._get(_TAP_URL, {"query": adql, "format": "csv"})
        text = content.decode("utf-8")
        if _looks_like_votable_error(text):
            raise ArchiveClientError(
                "el Exoplanet Archive devolvió un VOTABLE de error en vez de CSV "
                f"(columna inexistente u otro fallo de la consulta): {text.strip()}"
            )
        return _parse_csv_rows(text)

    def lookup_alias(self, name: str) -> dict[str, object]:
        """Servicio de alias, para nombres CON dígitos que no emparejaron por TAP.

        Subtope propio de `_ALIAS_SUBCAP` peticiones, dentro del tope total
        de `max_requests` (comparten el mismo contador de cortesía).
        """
        if self._alias_requests_made >= _ALIAS_SUBCAP:
            raise ArchiveClientError(
                f"se alcanzó el subtope de {_ALIAS_SUBCAP} peticiones de alias en esta ejecución"
            )
        self._throttle_and_count()
        content = self._get(_ALIAS_URL, {"objname": name})
        self._alias_requests_made += 1
        result: dict[str, object] = json.loads(content.decode("utf-8"))
        return result


# ---------------------------------------------------------------------------
# EpReading / load_ep_readings (única función que toca PostgreSQL)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EpReading:
    """Un `Item` de `astro-ph.EP` ya leído, con lo que hace falta para el cruce."""

    external_id: str
    title: str
    abstract: str
    objects: tuple[str, ...]
    claims: tuple[str, ...]
    run_ids: frozenset[UUID]


def load_ep_readings(session: Session) -> list[EpReading]:
    """Ítems `astro-ph.EP` (cross-listados incluidos) con `Reading` ya persistido.

    Deja la transacción de `session` en modo `SET TRANSACTION READ ONLY`
    antes de leer nada más: ninguna escritura posterior en esta misma
    sesión debe llegar a PostgreSQL. Excluye `external_id` con el prefijo
    `"9999."` que usa `scripts/seed_demo.py`, para no contar hallazgos de
    mentira. `run_ids` agrupa los `AgentCall` del Reader (cualquier
    `status`) para ese ítem, cualquiera que sea el `run_id` en el que
    ocurrieron.
    """
    session.execute(sa.text("SET TRANSACTION READ ONLY"))

    stmt = (
        sa.select(ItemRow, ReadingRow)
        .join(ReadingRow, ReadingRow.item_id == ItemRow.id)
        .where(ItemRow.categories.contains(["astro-ph.EP"]))
        .where(sa.not_(ItemRow.external_id.like("9999.%")))
        .order_by(ItemRow.external_id)
    )
    rows = session.execute(stmt).all()

    readings: list[EpReading] = []
    for item_row, reading_row in rows:
        run_ids_stmt = sa.select(AgentCallRow.run_id).where(
            AgentCallRow.item_id == item_row.id,
            AgentCallRow.agent == AgentRole.READER,
        )
        run_ids = frozenset(session.execute(run_ids_stmt).scalars().all())
        readings.append(
            EpReading(
                external_id=item_row.external_id,
                title=item_row.title,
                abstract=item_row.abstract,
                objects=tuple(reading_row.objects),
                claims=tuple(reading_row.claims),
                run_ids=run_ids,
            )
        )
    return readings


# ---------------------------------------------------------------------------
# Case / Report / build_report
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Case:
    """Una fila por intento de emparejamiento (un nombre de `Reading.objects`).

    Un `external_id` sin `objects` produce un único `Case` con
    `raw_name=None` y `lost_reason="sin_objects"`. `sigma_default` es un
    campo auxiliar, no cubierto por el contrato mínimo de los tests: la
    discrepancia frente a la solución marcada `default_flag`, además de
    `sigma` (que es el máximo frente a todas las previas). `sigma_via_a`/
    `sigma_via_b` son la discrepancia calculada específicamente con el
    valor de SU PROPIA vía frente a las previas; quedan en `None` cuando
    esa vía no tiene valor (`via_a`/`via_b` es `None`) o cuando no hay
    ninguna previa con la que calcular sigma (`sigma` también es `None` en
    ese caso). `source_snippet` es un recorte del texto que ve la vía (a)
    (abstract + claims) alrededor del valor encontrado; solo los casos con
    `via_a is not None` lo llevan rellenado -- un valor que solo viene del
    catálogo (vía (b)) no tiene de dónde reconstruirlo y se queda en
    `None`.
    """

    external_id: str
    raw_name: str | None
    match: MatchResult | None
    prior_count: int
    via_a: Measurement | None
    via_b: Measurement | None
    sigma: float | None
    lost_reason: str | None
    sigma_default: float | None = None
    sigma_via_a: float | None = None
    sigma_via_b: float | None = None
    source_snippet: str | None = None


_SIGMA_BIN_KEYS = ("<1", "1-2", "2-3", "3-5", ">=5")


def _empty_sigma_bins() -> dict[str, int]:
    return dict.fromkeys(_SIGMA_BIN_KEYS, 0)


def _sigma_bin(value: float) -> str:
    if value < 1:
        return "<1"
    if value < 2:
        return "1-2"
    if value < 3:
        return "2-3"
    if value < 5:
        return "3-5"
    return ">=5"


@dataclass(frozen=True, slots=True)
class Report:
    """Recuentos que pide el plan de T71. Ver `build_report`."""

    items_ep: int
    items_with_objects: int
    names_total: int
    names_distinct: int
    match_by_kind: dict[str, int]
    planets_with_ge2_priors: int
    via_a_count: int
    via_b_count: int
    via_both_count: int
    lost_by_reason: dict[str, int]
    sigma_bins: dict[str, int]
    sigma_bins_via_a: dict[str, int]
    sigma_bins_via_b: dict[str, int]
    sigma_default_bins: dict[str, int]
    nights: int
    cases_per_night: float
    cases_per_night_ge2: float
    cases_per_night_ge3: float
    cases_per_night_ge5: float
    requests_made: int | None = None
    duration_s: float | None = None


def build_report(
    cases: list[Case],
    nights: int,
    *,
    requests_made: int | None = None,
    duration_s: float | None = None,
) -> Report:
    items_ep = len({c.external_id for c in cases})
    items_with_objects = len({c.external_id for c in cases if c.raw_name is not None})
    names_total = sum(1 for c in cases if c.raw_name is not None)
    names_distinct = len({normalize_name(c.raw_name) for c in cases if c.raw_name is not None})

    match_by_kind: dict[str, int] = {}
    for case in cases:
        if case.match is not None:
            match_by_kind[case.match.kind] = match_by_kind.get(case.match.kind, 0) + 1

    planets_with_ge2_priors = len(
        {
            case.match.pl_name
            for case in cases
            if case.match is not None and case.match.pl_name is not None and case.prior_count >= 2
        }
    )

    via_a_count = sum(1 for c in cases if c.via_a is not None)
    via_b_count = sum(1 for c in cases if c.via_b is not None)
    via_both_count = sum(1 for c in cases if c.via_a is not None and c.via_b is not None)

    lost_by_reason: dict[str, int] = {}
    for case in cases:
        if case.lost_reason is not None:
            lost_by_reason[case.lost_reason] = lost_by_reason.get(case.lost_reason, 0) + 1

    sigma_bins = _empty_sigma_bins()
    sigma_bins_via_a = _empty_sigma_bins()
    sigma_bins_via_b = _empty_sigma_bins()
    sigma_default_bins = _empty_sigma_bins()
    tension_case_count = 0
    ge2 = ge3 = ge5 = 0
    for case in cases:
        if case.sigma is None:
            continue
        tension_case_count += 1
        sigma_bins[_sigma_bin(case.sigma)] += 1
        if case.via_a is not None:
            sigma_a = case.sigma_via_a if case.sigma_via_a is not None else case.sigma
            sigma_bins_via_a[_sigma_bin(sigma_a)] += 1
        if case.via_b is not None:
            sigma_b = case.sigma_via_b if case.sigma_via_b is not None else case.sigma
            sigma_bins_via_b[_sigma_bin(sigma_b)] += 1
        if case.sigma_default is not None:
            sigma_default_bins[_sigma_bin(case.sigma_default)] += 1
        if case.sigma >= 2:
            ge2 += 1
        if case.sigma >= 3:
            ge3 += 1
        if case.sigma >= 5:
            ge5 += 1

    safe_nights = nights if nights > 0 else 1

    return Report(
        items_ep=items_ep,
        items_with_objects=items_with_objects,
        names_total=names_total,
        names_distinct=names_distinct,
        match_by_kind=match_by_kind,
        planets_with_ge2_priors=planets_with_ge2_priors,
        via_a_count=via_a_count,
        via_b_count=via_b_count,
        via_both_count=via_both_count,
        lost_by_reason=lost_by_reason,
        sigma_bins=sigma_bins,
        sigma_bins_via_a=sigma_bins_via_a,
        sigma_bins_via_b=sigma_bins_via_b,
        sigma_default_bins=sigma_default_bins,
        nights=nights,
        cases_per_night=tension_case_count / safe_nights,
        cases_per_night_ge2=ge2 / safe_nights,
        cases_per_night_ge3=ge3 / safe_nights,
        cases_per_night_ge5=ge5 / safe_nights,
        requests_made=requests_made,
        duration_s=duration_s,
    )


# ---------------------------------------------------------------------------
# render_text
# ---------------------------------------------------------------------------

# Referencia orientativa de coste, tomada de los humos reales de T41/T42
# (ver CLAUDE.md, "Estado actual"): NO es una decisión de presupuesto, solo
# un orden de magnitud para que el autor se haga una idea de qué implicaría
# un redactor de tensiones (T76) con este volumen de casos por noche.
_TOKENS_PER_CASE = 4560
_TOKENS_PER_CASE_WITH_RETRY = 9120


def _format_bins(bins: dict[str, int]) -> str:
    return " | ".join(f"{key}: {bins.get(key, 0)}" for key in _SIGMA_BIN_KEYS)


# Umbral de sigma a partir del cual un `Case` se lista individualmente en
# la sección final del informe (ver `render_text`): coincide con el corte
# inferior del tramo "2-3" de `_sigma_bin`, no con una decisión de umbral
# de T73 (esa sigue sin tomarse, ver el aviso PROVISIONAL más arriba).
_LISTED_CASE_SIGMA_THRESHOLD = 2.0


def _format_case_line(case: Case) -> str:
    measurement = case.via_a if case.via_a is not None else case.via_b
    parameter = measurement.parameter if measurement is not None else "?"
    value = f"{measurement.value:.4g}" if measurement is not None else "?"
    pl_name = case.match.pl_name if case.match is not None else None
    sigma_str = f"{case.sigma:.2f}" if case.sigma is not None else "?"
    snippet = case.source_snippet or "(sin fragmento de origen)"
    return (
        f"{case.external_id:<14}{pl_name!s:<18}{parameter:<14}valor={value:<12}"
        f"sigma={sigma_str:<7} -- {snippet}"
    )


def render_text(report: Report, cases: list[Case] | None = None) -> str:
    """Informe en castellano, texto plano, sin llamar a nada externo.

    `cases` es opcional (compatibilidad con las llamadas que solo tienen
    el `Report` agregado): cuando se pasa, añade tras los recuentos una
    sección final con una línea por cada `Case` con `sigma >= 2`
    (`_LISTED_CASE_SIGMA_THRESHOLD`), con su `external_id`, el planeta, el
    parámetro, el valor, el propio sigma y el fragmento de
    `source_snippet`.
    """
    lines: list[str] = []
    lines.append("=" * 78)
    lines.append("EXPERIMENTO DE VIABILIDAD DEL CRUCE CON EL NASA EXOPLANET ARCHIVE (T71)")
    lines.append("=" * 78)
    lines.append("")
    lines.append(
        "AVISO: la formula de sigma usada en este informe es PROVISIONAL. T73 "
        "decide la definitiva (errores asimetricos, medida de referencia, "
        "umbral). Estos tramos son para decidir si el experimento sigue, no "
        "un resultado cientifico."
    )
    lines.append("")
    lines.append("-- Recuentos de ingesta --")
    lines.append(f"{'Items astro-ph.EP con Reading':<48}{report.items_ep:>10}")
    lines.append(f"{'Items con al menos un objeto (objects)':<48}{report.items_with_objects:>10}")
    lines.append(f"{'Nombres totales (intentos de emparejamiento)':<48}{report.names_total:>10}")
    lines.append(f"{'Nombres distintos (normalizados)':<48}{report.names_distinct:>10}")
    lines.append("")
    lines.append("-- Emparejamiento por tipo --")
    for kind in ("planet", "host_single", "host_multi", "unmatched"):
        lines.append(f"{kind:<48}{report.match_by_kind.get(kind, 0):>10}")
    lines.append("")
    lines.append(f"{'Planetas con >= 2 medidas previas':<48}{report.planets_with_ge2_priors:>10}")
    lines.append("")
    lines.append("-- Valor del paper disponible, por via --")
    lines.append(f"{'Via (a): parser sobre abstract/claims':<48}{report.via_a_count:>10}")
    lines.append(f"{'Via (b): solucion propia ya ingerida':<48}{report.via_b_count:>10}")
    lines.append(f"{'Ambas vias':<48}{report.via_both_count:>10}")
    lines.append("")
    lines.append(
        "Aviso sobre la via (b): solo ve preprints de arXiv citados como tales "
        "en pl_refname (fragmento de bibcode arXiv, enlace arxiv.org/abs/ o cita "
        "'arXiv:'). Un paper ya publicado en revista se cita en el archivo por "
        "el bibcode de la revista, no por el de arXiv, y la via (b) no lo ve."
    )
    lines.append("")
    lines.append("-- Casos perdidos, por motivo --")
    if report.lost_by_reason:
        for reason, count in sorted(report.lost_by_reason.items()):
            lines.append(f"{reason:<48}{count:>10}")
    else:
        lines.append("(ninguno)")
    lines.append("")
    lines.append("-- Tramos de sigma, todas las vias --")
    lines.append(_format_bins(report.sigma_bins))
    lines.append("-- Tramos de sigma, via (a) --")
    lines.append(_format_bins(report.sigma_bins_via_a))
    lines.append("-- Tramos de sigma, via (b) --")
    lines.append(_format_bins(report.sigma_bins_via_b))
    lines.append("-- Tramos de sigma frente a la solucion default (no frente al maximo) --")
    lines.append(_format_bins(report.sigma_default_bins))
    lines.append("")
    lines.append("-- Casos por noche --")
    lines.append(f"{'Noches observadas':<48}{report.nights:>10}")
    lines.append(f"{'Casos con tension calculada, por noche':<48}{report.cases_per_night:>10.2f}")
    lines.append(f"{'... con sigma >= 2, por noche':<48}{report.cases_per_night_ge2:>10.2f}")
    lines.append(f"{'... con sigma >= 3, por noche':<48}{report.cases_per_night_ge3:>10.2f}")
    lines.append(f"{'... con sigma >= 5, por noche':<48}{report.cases_per_night_ge5:>10.2f}")
    lines.append("")
    tokens_low = report.cases_per_night * _TOKENS_PER_CASE
    tokens_high = report.cases_per_night * _TOKENS_PER_CASE_WITH_RETRY
    lines.append(
        "Referencia orientativa de tokens (orden de magnitud, NO una decision "
        f"de presupuesto): casos/noche x {_TOKENS_PER_CASE} tokens "
        f"({_TOKENS_PER_CASE_WITH_RETRY} con reintento) = {tokens_low:.0f} "
        f"tokens/noche ({tokens_high:.0f} en el peor caso con reintento)."
    )
    lines.append("")
    if report.requests_made is not None:
        lines.append(f"Peticiones HTTP realizadas al Exoplanet Archive: {report.requests_made}")
    if report.duration_s is not None:
        lines.append(f"Duracion del experimento: {report.duration_s:.1f} s")
    lines.append("")
    if cases is not None:
        lines.append(f"-- Casos con sigma >= {_LISTED_CASE_SIGMA_THRESHOLD:.0f} --")
        listed = [
            case
            for case in cases
            if case.sigma is not None and case.sigma >= _LISTED_CASE_SIGMA_THRESHOLD
        ]
        if listed:
            for case in sorted(listed, key=lambda c: c.sigma, reverse=True):
                lines.append(_format_case_line(case))
        else:
            lines.append("(ninguno)")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# run_experiment
# ---------------------------------------------------------------------------

_PS_BATCH_SIZE = 25
_PS_COLUMNS = (
    "pl_name,hostname,default_flag,pl_refname,"
    "pl_bmasse,pl_bmasseerr1,pl_bmasseerr2,pl_bmasselim,"
    "pl_rade,pl_radeerr1,pl_radeerr2,pl_radelim,"
    "pl_orbper,pl_orbpererr1,pl_orbpererr2,pl_orbperlim"
)
_PARAMETER_PRIORITY: tuple[_ParameterName, ...] = ("mass_earth", "radius_earth", "period_days")


def _escape_adql_literal(value: str) -> str:
    return value.replace("'", "''")


def _fetch_index(client: ArchiveClient) -> CatalogIndex:
    rows = client.query_csv(
        "select pl_name,hostname,hd_name,hip_name,tic_id,sy_pnum from pscomppars"
    )
    return CatalogIndex.from_rows(rows)


_DIGIT_RE = re.compile(r"\d")


def _has_digit(raw_name: str) -> bool:
    return _DIGIT_RE.search(raw_name) is not None


def _match_from_alias_payload(
    payload: dict[str, object], raw_name: str, index: CatalogIndex
) -> MatchResult:
    """Traduce la respuesta de `ArchiveClient.lookup_alias` a un `MatchResult`.

    Busca `raw_name` (normalizado) primero entre los alias de cada planeta
    del sistema devuelto y, si no aparece ahí, entre los alias de cada
    estrella; el nombre "canónico" (`default_name`) que lo contiene se
    resuelve contra el `CatalogIndex` LOCAL (el mismo que usa `match_object`
    para un nombre que empareja directamente por TAP), no contra la
    respuesta de alias en sí.
    """
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("lookup_status") != "OK":
        return MatchResult(kind="unmatched", pl_name=None)

    system = payload.get("system")
    objects = system.get("objects") if isinstance(system, dict) else None
    if not isinstance(objects, dict):
        return MatchResult(kind="unmatched", pl_name=None)

    norm_raw = normalize_name(raw_name)

    planet_set = objects.get("planet_set")
    planets = planet_set.get("planets") if isinstance(planet_set, dict) else None
    for planet_info in (planets or {}).values():
        alias_set = planet_info.get("alias_set") if isinstance(planet_info, dict) else None
        aliases = alias_set.get("aliases") if isinstance(alias_set, dict) else None
        if aliases and any(normalize_name(alias) == norm_raw for alias in aliases):
            default_name = alias_set.get("default_name") if isinstance(alias_set, dict) else None
            if isinstance(default_name, str):
                return match_object(default_name, index)

    stellar_set = objects.get("stellar_set")
    stars = stellar_set.get("stars") if isinstance(stellar_set, dict) else None
    for star_info in (stars or {}).values():
        alias_set = star_info.get("alias_set") if isinstance(star_info, dict) else None
        aliases = alias_set.get("aliases") if isinstance(alias_set, dict) else None
        if aliases and any(normalize_name(alias) == norm_raw for alias in aliases):
            default_name = alias_set.get("default_name") if isinstance(alias_set, dict) else None
            if isinstance(default_name, str):
                return match_object(default_name, index)

    return MatchResult(kind="unmatched", pl_name=None)


def _resolve_aliases(
    readings: list[EpReading], index: CatalogIndex, client: ArchiveClient
) -> tuple[dict[str, MatchResult], set[str]]:
    """Consulta el servicio de alias para nombres sin emparejar con dígito.

    Solo se consulta un nombre (`raw_name`) que no empareja por el
    `CatalogIndex` local y contiene al menos un dígito -- ver docstring del
    módulo. Deduplica por nombre normalizado: la primera aparición decide
    si se consulta, cualquier repetición reutiliza el resultado. Respeta el
    subtope `_ALIAS_SUBCAP` (dentro del tope total de `ArchiveClient`): los
    nombres que lo agotan quedan en el segundo elemento de la tupla
    devuelta, sin llegar a pedirse.

    Devuelve `(alias_results, alias_capped)`: `alias_results` mapea nombre
    normalizado -> `MatchResult` ya resuelto contra el `CatalogIndex` local
    (ver `_match_from_alias_payload`); `alias_capped` son los nombres
    normalizados que se quedaron sin consultar por el subtope.
    """
    seen: set[str] = set()
    candidates: list[str] = []
    for reading in readings:
        for raw_name in reading.objects:
            norm = normalize_name(raw_name)
            if norm in seen:
                continue
            seen.add(norm)
            if match_object(raw_name, index).kind != "unmatched" or not _has_digit(raw_name):
                continue
            candidates.append(raw_name)

    alias_results: dict[str, MatchResult] = {}
    alias_capped: set[str] = set()
    queries_made = 0
    for raw_name in candidates:
        norm = normalize_name(raw_name)
        if queries_made >= _ALIAS_SUBCAP:
            alias_capped.add(norm)
            continue
        payload = client.lookup_alias(raw_name)
        queries_made += 1
        alias_results[norm] = _match_from_alias_payload(payload, raw_name, index)
    return alias_results, alias_capped


def _fetch_solutions_by_planet(
    client: ArchiveClient, pl_names: list[str]
) -> dict[str, list[CatalogSolution]]:
    solutions_by_pl: dict[str, list[CatalogSolution]] = {}
    for start in range(0, len(pl_names), _PS_BATCH_SIZE):
        batch = pl_names[start : start + _PS_BATCH_SIZE]
        in_clause = ",".join(f"'{_escape_adql_literal(name)}'" for name in batch)
        adql = f"select {_PS_COLUMNS} from ps where pl_name in ({in_clause})"
        for row in client.query_csv(adql):
            for solution in parse_catalog_row(row):
                solutions_by_pl.setdefault(solution.pl_name, []).append(solution)
    return solutions_by_pl


def _pick_parameter(
    via_a: Measurement | None, own_pl_solutions: list[CatalogSolution]
) -> _ParameterName | None:
    if via_a is not None:
        return via_a.parameter
    own_params = {s.measurement.parameter for s in own_pl_solutions if s.measurement is not None}
    for parameter in _PARAMETER_PRIORITY:
        if parameter in own_params:
            return parameter
    return None


def _resolve_case(
    *,
    external_id: str,
    raw_name: str,
    match: MatchResult,
    pl_name: str,
    via_a: Measurement | None,
    all_pl_solutions: list[CatalogSolution],
    via_a_multi_discarded: bool = False,
    source_snippet: str | None = None,
) -> Case:
    own_pl_solutions = own_solutions(all_pl_solutions, external_id)
    previas_pool = [s for s in all_pl_solutions if s not in own_pl_solutions]

    parameter = _pick_parameter(via_a, own_pl_solutions)

    via_b: Measurement | None = None
    if parameter is not None:
        for solution in own_pl_solutions:
            if solution.measurement is not None and solution.measurement.parameter == parameter:
                via_b = solution.measurement
                break

    # Recuento de previas válidas por parámetro, INDEPENDIENTE de si hay
    # valor del papel: `planets_with_ge2_priors` se calcula sobre lo que ya
    # sabe el catálogo, no sobre si este paper aporta un valor nuevo.
    previas_counts_by_param: dict[_ParameterName, int] = {}
    for solution in previas_pool:
        if solution.pl_name == pl_name and solution.measurement is not None:
            param = solution.measurement.parameter
            previas_counts_by_param[param] = previas_counts_by_param.get(param, 0) + 1
    max_previas_count = max(previas_counts_by_param.values(), default=0)

    if via_a is None and via_b is None:
        return Case(
            external_id=external_id,
            raw_name=raw_name,
            match=match,
            prior_count=max_previas_count,
            via_a=None,
            via_b=None,
            sigma=None,
            lost_reason="valores_multiples" if via_a_multi_discarded else "sin_valor_de_papel",
            source_snippet=source_snippet,
        )

    paper_value = via_a if via_a is not None else via_b
    assert paper_value is not None

    previas = [
        s.measurement
        for s in previas_pool
        if s.pl_name == pl_name
        and s.measurement is not None
        and s.measurement.parameter == paper_value.parameter
    ]

    if not previas:
        return Case(
            external_id=external_id,
            raw_name=raw_name,
            match=match,
            prior_count=max_previas_count,
            via_a=via_a,
            via_b=via_b,
            sigma=None,
            lost_reason="sin_previas",
            source_snippet=source_snippet,
        )

    sigma_max = max(sigma(paper_value, prior) for prior in previas)
    # Con las dos vías presentes y valores distintos, cada tramo de sigma
    # por vía debe usar SU PROPIO valor, no siempre el de la vía (a)
    # (preferida por `_pick_parameter` para escoger `paper_value`).
    sigma_via_a = max(sigma(via_a, prior) for prior in previas) if via_a is not None else None
    sigma_via_b = max(sigma(via_b, prior) for prior in previas) if via_b is not None else None
    default_prior = next(
        (
            s.measurement
            for s in previas_pool
            if s.pl_name == pl_name
            and s.default_flag
            and s.measurement is not None
            and s.measurement.parameter == paper_value.parameter
        ),
        None,
    )
    sigma_default = sigma(paper_value, default_prior) if default_prior is not None else None

    return Case(
        external_id=external_id,
        raw_name=raw_name,
        match=match,
        prior_count=len(previas),
        via_a=via_a,
        via_b=via_b,
        sigma=sigma_max,
        lost_reason=None,
        sigma_default=sigma_default,
        sigma_via_a=sigma_via_a,
        sigma_via_b=sigma_via_b,
        source_snippet=source_snippet,
    )


_SOURCE_SNIPPET_RADIUS = 40


def _snippet_around(text: str, start: int, end: int, radius: int = _SOURCE_SNIPPET_RADIUS) -> str:
    """Recorte de `text` de `radius` caracteres a cada lado de `[start, end)`.

    Usado para `Case.source_snippet`: un fragmento legible del texto que ve
    la vía (a) alrededor del valor+error encontrado, no el abstract/claims
    completos.
    """
    return text[max(0, start - radius) : end + radius]


def _multi_value_parameters(candidates: list[Measurement]) -> set[_ParameterName]:
    """Parámetros con más de un VALOR distinto entre `candidates`.

    La vía (a) no puede saber, entre varios valores del mismo parámetro
    encontrados en el mismo abstract/claims, cuál es "el" valor del papel
    -- ver `test_run_experiment_descarta_via_a_con_valores_multiples_del_
    parametro` en `tests/test_exoplanet_viability_script.py`.
    """
    values_by_param: dict[_ParameterName, set[float]] = {}
    for measurement in candidates:
        values_by_param.setdefault(measurement.parameter, set()).add(measurement.value)
    return {parameter for parameter, values in values_by_param.items() if len(values) > 1}


def _cases_for_reading(
    reading: EpReading,
    index: CatalogIndex,
    solutions_by_pl: dict[str, list[CatalogSolution]],
    alias_results: dict[str, MatchResult],
    alias_capped: set[str],
) -> list[Case]:
    if not reading.objects:
        return [
            Case(
                external_id=reading.external_id,
                raw_name=None,
                match=None,
                prior_count=0,
                via_a=None,
                via_b=None,
                sigma=None,
                lost_reason="sin_objects",
            )
        ]

    # Resuelve cada nombre contra el índice local y, para los que sigan sin
    # emparejar, contra el resultado (o el subtope) del servicio de alias
    # -- ver `_resolve_aliases`. `forced_lost_reason` solo se aplica si el
    # nombre sigue sin emparejar tras el intento de alias.
    resolved: list[tuple[str, MatchResult, str | None]] = []
    for raw_name in reading.objects:
        base = match_object(raw_name, index)
        if base.kind != "unmatched":
            resolved.append((raw_name, base, None))
            continue
        norm = normalize_name(raw_name)
        if norm in alias_capped:
            resolved.append((raw_name, base, "tope_alias"))
        elif norm in alias_results:
            resolved.append((raw_name, alias_results[norm], None))
        else:
            resolved.append((raw_name, base, None))

    matches = [m for _, m, _ in resolved]
    resolved_pl_names = {m.pl_name for m in matches if m.pl_name is not None}
    # Via (a) solo se intenta cuando el item tiene EXACTAMENTE un planeta
    # emparejado: con varios, el parser sobre abstract/claims no puede saber
    # a cual de ellos atribuir el valor que encuentre.
    single_pl_name = next(iter(resolved_pl_names)) if len(resolved_pl_names) == 1 else None
    via_a_source_text = " ".join([reading.abstract, *reading.claims])
    via_a_candidates_with_span = (
        _parse_measurements_with_spans(via_a_source_text) if single_pl_name is not None else []
    )
    # Un parámetro con más de un valor distinto en el mismo texto se
    # descarta por completo de la vía (a): quedarse con el primero
    # encontrado en silencio sería arbitrario.
    multi_value_params = _multi_value_parameters(
        [measurement for measurement, _start, _end in via_a_candidates_with_span]
    )
    clean_via_a_candidates_with_span = [
        (measurement, start, end)
        for measurement, start, end in via_a_candidates_with_span
        if measurement.parameter not in multi_value_params
    ]
    clean_via_a_candidates = [
        measurement for measurement, _start, _end in clean_via_a_candidates_with_span
    ]

    cases: list[Case] = []
    for raw_name, match, forced_lost_reason in resolved:
        if match.kind == "unmatched":
            cases.append(
                Case(
                    external_id=reading.external_id,
                    raw_name=raw_name,
                    match=match,
                    prior_count=0,
                    via_a=None,
                    via_b=None,
                    sigma=None,
                    lost_reason=forced_lost_reason or "no_match",
                )
            )
            continue
        if match.kind == "host_multi":
            cases.append(
                Case(
                    external_id=reading.external_id,
                    raw_name=raw_name,
                    match=match,
                    prior_count=0,
                    via_a=None,
                    via_b=None,
                    sigma=None,
                    lost_reason="ambiguo",
                )
            )
            continue

        pl_name = match.pl_name
        assert pl_name is not None
        via_a = (
            clean_via_a_candidates[0]
            if (pl_name == single_pl_name and clean_via_a_candidates)
            else None
        )
        source_snippet: str | None = None
        if via_a is not None:
            _measurement, value_start, value_end = clean_via_a_candidates_with_span[0]
            source_snippet = _snippet_around(via_a_source_text, value_start, value_end)
        via_a_multi_discarded = (
            pl_name == single_pl_name and not clean_via_a_candidates and bool(multi_value_params)
        )
        cases.append(
            _resolve_case(
                external_id=reading.external_id,
                raw_name=raw_name,
                match=match,
                pl_name=pl_name,
                via_a=via_a,
                all_pl_solutions=solutions_by_pl.get(pl_name, []),
                via_a_multi_discarded=via_a_multi_discarded,
                source_snippet=source_snippet,
            )
        )
    return cases


def _run_experiment_full(
    readings: list[EpReading], client: ArchiveClient
) -> tuple[list[Case], Report]:
    started = time.monotonic()
    index = _fetch_index(client)

    matched_pl_names: set[str] = set()
    for reading in readings:
        for name in reading.objects:
            match = match_object(name, index)
            if match.kind in ("planet", "host_single") and match.pl_name is not None:
                matched_pl_names.add(match.pl_name)

    alias_results, alias_capped = _resolve_aliases(readings, index, client)
    for alias_match in alias_results.values():
        if alias_match.kind in ("planet", "host_single") and alias_match.pl_name is not None:
            matched_pl_names.add(alias_match.pl_name)

    solutions_by_pl = (
        _fetch_solutions_by_planet(client, sorted(matched_pl_names)) if matched_pl_names else {}
    )

    all_run_ids: set[UUID] = set()
    cases: list[Case] = []
    for reading in readings:
        all_run_ids.update(reading.run_ids)
        cases.extend(
            _cases_for_reading(reading, index, solutions_by_pl, alias_results, alias_capped)
        )

    nights = len(all_run_ids) or 1
    duration_s = time.monotonic() - started
    report = build_report(
        cases,
        nights=nights,
        requests_made=client.requests_made,
        duration_s=duration_s,
    )
    return cases, report


def run_experiment(readings: list[EpReading], client: ArchiveClient) -> Report:
    """Corre el experimento completo y devuelve solo el informe agregado."""
    _, report = _run_experiment_full(readings, client)
    return report


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


def _measurement_to_json(measurement: Measurement | None) -> dict[str, object] | None:
    if measurement is None:
        return None
    return {
        "parameter": measurement.parameter,
        "value": measurement.value,
        "err_plus": measurement.err_plus,
        "err_minus": measurement.err_minus,
    }


def _match_to_json(match: MatchResult | None) -> dict[str, object] | None:
    if match is None:
        return None
    return {"kind": match.kind, "pl_name": match.pl_name, "candidates": list(match.candidates)}


def _case_to_json(case: Case) -> dict[str, object]:
    return {
        "external_id": case.external_id,
        "raw_name": case.raw_name,
        "match": _match_to_json(case.match),
        "prior_count": case.prior_count,
        "via_a": _measurement_to_json(case.via_a),
        "via_b": _measurement_to_json(case.via_b),
        "sigma": case.sigma,
        "sigma_default": case.sigma_default,
        "sigma_via_a": case.sigma_via_a,
        "sigma_via_b": case.sigma_via_b,
        "source_snippet": case.source_snippet,
        "lost_reason": case.lost_reason,
    }


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Experimento de viabilidad del cruce con el NASA Exoplanet Archive (T71)."
    )
    parser.add_argument(
        "--json",
        type=Path,
        default=None,
        help="ruta donde volcar cada caso calculado, en JSON",
    )
    return parser.parse_args(argv)


def main() -> None:
    _require_opt_in()

    args = _parse_args(sys.argv[1:])

    settings = Settings()
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    session = session_factory()
    try:
        readings = load_ep_readings(session)
    finally:
        session.rollback()
        session.close()

    http = httpx.Client()
    try:
        client = ArchiveClient(
            http,
            max_requests=_TOTAL_REQUEST_CAP,
            spacing_s=_COURTESY_SPACING_S,
            sleep=time.sleep,
        )
        cases, report = _run_experiment_full(readings, client)
    finally:
        http.close()

    print(render_text(report, cases))

    if args.json is not None:
        args.json.write_text(
            json.dumps([_case_to_json(case) for case in cases], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
