"""Filtrado en Python de las medidas crudas del Reader v3 (T71.c).

Puro: sin IO, sin acceso a base de datos ni a `LLMProvider`. Cada elemento
crudo de `ReaderV3Output.measurements` pasa, en este orden, por cinco
comprobaciones: (1) forma (`parse_measurement`), (2) invariantes de dominio
(`Measurement(...)`), (3) `evidence` como cita literal del abstract, (4) la
anfitriona del nombre completado reconocible, con límite de palabra y tras
descartar anfitrionas genéricas, en el `abstract` o en el `title` del ítem
-- **nunca** en `objects` (decisión del autor, revisión 2 de T71.c:
`objects` es salida del mismo modelo que completó `planet_name`, así que
comparar la anfitriona contra `objects` no es una verificación
independiente de esa completación, solo el mismo modelo corroborándose a
sí mismo), (5) `evidence` cita el número de `value` (comparación tolerante
a ceros finales/iniciales equivalentes). Cualquier fallo en cualquiera de
las cinco descarta **solo esa medida**: esta función nunca lanza por una
medida individual (decisión del autor, T71.c) -- lanzar reintentaría el
ítem entero por culpa de un solo elemento de la lista, y una medida mal
atribuida no es un motivo para gastar otra llamada real.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from nocturna.application.agents.reader_output import (
    MalformedMeasurement,
    MeasurementOut,
    parse_measurement,
)
from nocturna.domain.entities import (
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
)
from nocturna.domain.errors import InvariantViolation

_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_whitespace(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def evidence_in_abstract(evidence: str, abstract: str) -> bool:
    """¿Es `evidence` una subcadena literal de `abstract`, salvo espacios?

    Normaliza espacios (colapsa repetidos, recorta extremos) en ambos lados
    antes de comparar como subcadena -- un modelo que reproduce la cita
    exacta pero con un salto de línea distinto al del JSON de origen no debe
    contar como "no literal". No normaliza mayúsculas, acentos ni
    puntuación: eso ya sería tolerar una paráfrasis, que es justo lo que
    `evidence` no puede ser (`prompts/reader-v3.md`).
    """
    return _normalize_whitespace(evidence) in _normalize_whitespace(abstract)


_NUMBER_RE = re.compile(r"-?\d+\.?\d*")


def _canonicalize_number(raw: str) -> str:
    """Normaliza una representación numérica para comparar tolerando ceros
    finales/iniciales equivalentes (`"0.520"` == `"0.52"`, `"5.0"` ==
    `"5"`).

    NO cubre `".52"` frente a `"0.52"`: aunque esta función, aplicada
    directamente a la cadena `".52"`, normaliza a `"0.52"`, `_NUMBER_RE`
    exige `\\d+` (una cifra) antes del punto opcional, así que nunca
    extrae `".52"` de `evidence` como número completo -- como mucho
    extraería `"52"` sin el punto, que canoniza a `"52"`, distinto de
    `"0.52"`. El fallo es conservador (una `evidence` que solo escribe
    `.52` se descarta por `VALUE_NOT_IN_EVIDENCE` en vez de conservarse),
    no al revés, así que no justifica un reintento ni se corrige aquí."""
    text = raw.strip()
    sign = "-" if text.startswith("-") else ""
    text = text[1:] if sign else text
    int_part, _, frac_part = text.partition(".")
    int_part = int_part.lstrip("0") or "0"
    frac_part = frac_part.rstrip("0")
    canonical = int_part if not frac_part else f"{int_part}.{frac_part}"
    return f"{sign}{canonical}" if canonical != "0" else "0"


def value_in_evidence(value: float, evidence: str) -> bool:
    """¿Cita `evidence` el número `value`, en cualquier forma reconocible?

    Extrae todos los números de `evidence` y compara cada uno, canonizado
    (ceros finales/iniciales equivalentes), contra `value` canonizado. No
    comprueba unidades ni signo de error: solo que el propio valor esté
    citado, no inventado por el modelo a partir de una `evidence` real pero
    que no lo menciona.
    """
    target = _canonicalize_number(repr(value))
    return any(_canonicalize_number(match) == target for match in _NUMBER_RE.findall(evidence))


#: `<host> <letra>`, con espacio -- la forma habitual ("V1298 Tau b").
_HOST_RE_SPACED = re.compile(r"^(?P<host>.*\S)\s+[b-z]$")
#: `<host><letra>` sin espacio, cuando `<host>` termina en una cifra
#: ("HD12345b") -- sin la cifra final en el grupo no distinguiría el
#: dígito de la letra de planeta.
_HOST_RE_JOINED = re.compile(r"^(?P<host>.*\d)[b-z]$")
#: `<host> <letra-de-componente><letra-de-planeta>`, con espacio antes del
#: componente estelar pero sin espacio entre el componente y la letra de
#: planeta ("KOI-5 Ab" -> "KOI-5", "GJ 667 Cc" -> "GJ 667").
_HOST_RE_STELLAR_COMPONENT_SPACED = re.compile(r"^(?P<host>.*\S)\s+[A-Z][b-z]$")
#: `<host>)<letra>` -- la anfitriona termina en un componente entre
#: paréntesis y la letra de planeta va pegada, sin espacio
#: ("Kepler-16 (AB)b" -> "Kepler-16 (AB)").
_HOST_RE_STELLAR_COMPONENT_PAREN = re.compile(r"^(?P<host>.*\))[b-z]$")

_HOST_PATTERNS = (
    _HOST_RE_STELLAR_COMPONENT_SPACED,
    _HOST_RE_STELLAR_COMPONENT_PAREN,
    _HOST_RE_SPACED,
    _HOST_RE_JOINED,
)


def planet_host(planet_name: str) -> str | None:
    """Extrae el nombre de la anfitriona de un nombre completo de planeta.

    `strip()` primero -- un espacio sobrante al final (`"TOI-700 d "`) no
    debe impedir el reconocimiento. Formas reconocidas, en este orden:
    `<host> <letra-componente><letra-planeta>` (componente estelar con
    espacio, "KOI-5 Ab"), `<host>)<letra>` (componente entre paréntesis
    pegado a la letra, "Kepler-16 (AB)b"), `<host> <letra>` (con espacio) o
    `<host><letra>` sin espacio cuando `<host>` termina en una cifra.
    `None` si `planet_name` no encaja en ninguna -- por ejemplo, un nombre
    que ya es solo la letra, o un nombre que no completó correctamente la
    anfitriona.
    """
    name = planet_name.strip()
    for pattern in _HOST_PATTERNS:
        match = pattern.match(name)
        if match is not None:
            return match.group("host")
    return None


#: Caracteres que, adyacentes a un carácter alfanumérico, se tratan como
#: espacio en la comparación de anfitriona: coma, virgulilla (espacio no
#: separable de LaTeX) y coma escapada de LaTeX (espacio fino).
_HOST_SEPARATOR_RE = re.compile(r"\\,|~|,")


def _normalize_for_host_match(text: str) -> str:
    def _replace(match: re.Match[str]) -> str:
        start, end = match.span()
        before_alnum = start > 0 and text[start - 1].isalnum()
        after_alnum = end < len(text) and text[end].isalnum()
        if before_alnum or after_alnum:
            return " "
        return match.group(0)

    return _normalize_whitespace(_HOST_SEPARATOR_RE.sub(_replace, text))


#: Anfitrionas genéricas: ni siquiera se comprueba si aparecen en el
#: abstract o el título -- un modelo que complete `planet_name` con estas
#: palabras no ha identificado ninguna anfitriona real.
_GENERIC_HOST_WORDS = frozenset({"planet", "star", "system", "companion", "host"})
#: Determinantes/pronombres genéricos (T71.c, correcciones tras la pasada 2
#: de revisión): solos (`"We"`, `"Here"`, `"I"`, `"A"`) o combinados con
#: una palabra de `_GENERIC_HOST_WORDS` (`"This planet"`, `"The host
#: star"`), nunca identifican una anfitriona real.
_GENERIC_HOST_DETERMINERS = frozenset(
    {
        "this",
        "that",
        "these",
        "those",
        "our",
        "its",
        "their",
        "the",
        "a",
        "an",
        "new",
        "we",
        "here",
        "i",
    }
)
#: Prefijos de misión/catálogo que, SOLOS y sin ningún número adjunto
#: (`"TESS"`, `"Kepler"`, no `"Kepler-10"`), no identifican una anfitriona
#: real: son el nombre de la misión o el catálogo, no una designación de
#: estrella concreta (T71.c, correcciones tras la pasada 2 de revisión).
_GENERIC_HOST_MISSION_PREFIXES = frozenset(
    {"tess", "kepler", "k2", "hd", "gj", "toi", "koi", "wasp", "hat-p"}
)
_HAS_DIGIT_OR_UPPER_RE = re.compile(r"[0-9A-Z]")


def _is_generic_host(normalized_host: str) -> bool:
    if _HAS_DIGIT_OR_UPPER_RE.search(normalized_host) is None:
        return True
    words = normalized_host.lower().split()
    if len(words) == 1 and words[0] in _GENERIC_HOST_MISSION_PREFIXES:
        return True
    generic_words = _GENERIC_HOST_WORDS | _GENERIC_HOST_DETERMINERS
    return all(word in generic_words for word in words)


class DiscardReason(StrEnum):
    """Por qué se descartó una medida cruda del Reader v3 (T71.c)."""

    #: `parse_measurement` lanzó `MalformedMeasurement`: la forma del JSON
    #: no encaja con `MeasurementOut` (campo ausente, tipo laxo, valor de
    #: enum desconocido...).
    MALFORMED = "malformed"
    #: `Measurement(...)` lanzó `InvariantViolation`: la forma es correcta
    #: pero el valor no cumple una invariante de dominio (unidad
    #: incoherente con el parámetro, error negativo...).
    INVARIANT = "invariant"
    #: `evidence` no es una cita literal (salvo espacios) del abstract.
    EVIDENCE_NOT_LITERAL = "evidence_not_literal"
    #: No se pudo extraer la anfitriona de `planet_name`, la anfitriona
    #: extraída es genérica (sin cifra ni mayúscula; compuesta solo por
    #: palabras de "planet"/"star"/"system"/"companion"/"host" y
    #: determinantes/pronombres como "this"/"the"/"our"/"we"/"here"/"i"; o
    #: un prefijo de misión/catálogo sin número como "TESS"/"Kepler"/"K2"/
    #: "HD"/"GJ"/"TOI"/"KOI"/"WASP"/"HAT-P"), o la anfitriona no aparece,
    #: con límite de palabra, ni en el `abstract` ni en el `title` del
    #: ítem -- nunca en `objects` (salvaguarda del nombre completado,
    #: T71.c; decisión del autor, revisión 2: `objects` es salida del
    #: mismo modelo que completó `planet_name`).
    HOST_NOT_FOUND = "host_not_found"
    #: `evidence` es cita literal del abstract y la anfitriona es
    #: reconocible, pero `evidence` no contiene el número de `value` en
    #: ninguna forma reconocible (T71.c, corrección tras la pasada 1 de
    #: revisión).
    VALUE_NOT_IN_EVIDENCE = "value_not_in_evidence"


@dataclass(frozen=True, slots=True)
class DiscardedMeasurement:
    """Una medida cruda descartada, con lo que hace falta para el log de
    `ReadItem` (`reader.measurement_discarded`): `planet_name`, `parameter`,
    `value` y `evidence` son `None` cuando el descarte es `MALFORMED` -- si
    la forma no valida, no hay campos fiables que mostrar."""

    reason: DiscardReason
    detail: str
    planet_name: str | None
    parameter: str | None
    value: float | None
    evidence: str | None


@dataclass(frozen=True, slots=True)
class MeasurementFilterResult:
    """Resultado de `filter_measurements`: las medidas utilizables y el
    detalle de las descartadas, en el mismo orden que llegaron en `raw`."""

    kept: tuple[Measurement, ...]
    discarded: tuple[DiscardedMeasurement, ...]


def _discard_malformed(exc: MalformedMeasurement) -> DiscardedMeasurement:
    return DiscardedMeasurement(
        reason=DiscardReason.MALFORMED,
        detail=str(exc),
        planet_name=None,
        parameter=None,
        value=None,
        evidence=None,
    )


def _discard_invariant(candidate: MeasurementOut, exc: InvariantViolation) -> DiscardedMeasurement:
    return DiscardedMeasurement(
        reason=DiscardReason.INVARIANT,
        detail=str(exc),
        planet_name=candidate.planet_name,
        parameter=candidate.parameter,
        value=candidate.value,
        evidence=candidate.evidence,
    )


def _discard_from_measurement(
    measurement: Measurement, *, reason: DiscardReason, detail: str
) -> DiscardedMeasurement:
    return DiscardedMeasurement(
        reason=reason,
        detail=detail,
        planet_name=measurement.planet_name,
        parameter=measurement.parameter.value,
        value=measurement.value,
        evidence=measurement.evidence,
    )


def filter_measurements(raw: list[object], *, abstract: str, title: str) -> MeasurementFilterResult:
    """Filtra las medidas crudas de `ReaderV3Output.measurements` para un ítem.

    Orden de comprobaciones, por medida, cada una posterior solo si la
    anterior pasó: `parse_measurement` (forma) -> `Measurement(...)`
    (invariantes de dominio) -> `evidence_in_abstract` (cita literal) ->
    anfitriona del nombre completado, con límite de palabra y tras
    descartar anfitrionas genéricas, en el `abstract` o en el `title` del
    ítem -> `value_in_evidence` (la cita contiene el número de `value`).
    Nunca lanza por una medida individual: cualquier fallo la descarta y
    sigue con la siguiente.

    `title`, no `objects`, es la segunda fuente admitida para la
    salvaguarda de anfitriona (decisión del autor, revisión 2 de T71.c):
    `objects` es una lista que el propio Reader v3 devuelve en la misma
    llamada que completó `planet_name`, así que comprobar la anfitriona
    contra `objects` solo verificaría que el modelo es consistente con su
    propia salida, no que la anfitriona sea real -- el título del ítem, en
    cambio, es un dato del `Item`, independiente de lo que el modelo haya
    inventado o completado en esta llamada.
    """
    kept: list[Measurement] = []
    discarded: list[DiscardedMeasurement] = []

    for item in raw:
        try:
            candidate = parse_measurement(item)
        except MalformedMeasurement as exc:
            discarded.append(_discard_malformed(exc))
            continue

        try:
            measurement = Measurement(
                planet_name=candidate.planet_name,
                parameter=MeasuredParameter(candidate.parameter),
                value=candidate.value,
                err_plus=candidate.err_plus,
                err_minus=candidate.err_minus,
                unit=MeasurementUnit(candidate.unit),
                limit=MeasurementLimit(candidate.limit),
                origin=MeasurementOrigin(candidate.origin),
                evidence=candidate.evidence,
            )
        except InvariantViolation as exc:
            discarded.append(_discard_invariant(candidate, exc))
            continue

        if not evidence_in_abstract(measurement.evidence, abstract):
            discarded.append(
                _discard_from_measurement(
                    measurement,
                    reason=DiscardReason.EVIDENCE_NOT_LITERAL,
                    detail="'evidence' no es una cita literal (salvo espacios) del abstract",
                )
            )
            continue

        host = planet_host(measurement.planet_name)
        if host is None:
            discarded.append(
                _discard_from_measurement(
                    measurement,
                    reason=DiscardReason.HOST_NOT_FOUND,
                    detail=(
                        f"no se pudo extraer la anfitriona de 'planet_name' "
                        f"({measurement.planet_name!r})"
                    ),
                )
            )
            continue

        normalized_host = _normalize_for_host_match(host)
        if _is_generic_host(normalized_host):
            discarded.append(
                _discard_from_measurement(
                    measurement,
                    reason=DiscardReason.HOST_NOT_FOUND,
                    detail=f"la anfitriona completada ({host!r}) es genérica",
                )
            )
            continue

        host_pattern = re.compile(rf"(?<![\w-]){re.escape(normalized_host)}(?![\w-])")
        host_in_abstract = host_pattern.search(_normalize_for_host_match(abstract)) is not None
        host_in_title = host_pattern.search(_normalize_for_host_match(title)) is not None
        if not host_in_abstract and not host_in_title:
            discarded.append(
                _discard_from_measurement(
                    measurement,
                    reason=DiscardReason.HOST_NOT_FOUND,
                    detail=(
                        f"la anfitriona completada ({host!r}) no aparece, con límite de "
                        "palabra, en el abstract ni en el título"
                    ),
                )
            )
            continue

        if not value_in_evidence(measurement.value, measurement.evidence):
            discarded.append(
                _discard_from_measurement(
                    measurement,
                    reason=DiscardReason.VALUE_NOT_IN_EVIDENCE,
                    detail=(
                        f"'evidence' no cita el número de 'value' ({measurement.value!r}) "
                        "en ninguna forma reconocible"
                    ),
                )
            )
            continue

        kept.append(measurement)

    return MeasurementFilterResult(kept=tuple(kept), discarded=tuple(discarded))
