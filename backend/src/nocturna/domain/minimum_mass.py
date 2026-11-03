"""Detección de masa mínima (`M sin i`) en una medida de masa (T100, D2).

Función pura sobre el texto: sin IO. El Reader v3 a veces devuelve como
`mass` una masa mínima (`Msin(i)`), que no es la masa del planeta y no se
compara con las masas del archivo. La regla mira la cita literal
(`evidence`) y su contexto izquierdo en el abstract, desde el último límite
de frase o cláusula (`. `, `; `, `, `) y como máximo `_MAX_CONTEXT` caracteres.
"""

import re

from nocturna.domain.entities import MeasuredParameter, Measurement

_MAX_CONTEXT = 80
_BOUNDARIES = (". ", "; ", ", ")
_WHITESPACE_RE = re.compile(r"\s+")
_TEX_SPACING_RE = re.compile(r"\\[,;! ]|[~_^]")
_TEX_NOISE_RE = re.compile(r"[$\\{}]")
_MINIMUM_MASS_RE = re.compile(r"sin\s*\(?\s*i(?:p?\b)|\bminimum[\s-]masse?s?\b|\bprojected mass")
#: Tras `, ` una cifra, un signo o `$`+cifra sigue una lista o una aposición
#: numérica, no una cláusula nueva.
_NUMERIC_CONTINUATION_RE = re.compile(r"[+\-\u2212]|\$?\d")


def _collapse(text: str) -> str:
    return _WHITESPACE_RE.sub(" ", text).strip()


def _normalize(text: str) -> str:
    return _collapse(_TEX_NOISE_RE.sub("", _TEX_SPACING_RE.sub(" ", text))).lower()


def _left_context(evidence: str, abstract: str) -> str:
    """Texto previo a `evidence` en el abstract (ambos con espacios
    colapsados), acotado por el último límite y por `_MAX_CONTEXT`; vacío si
    `evidence` no está en el abstract."""
    index = abstract.find(evidence)
    if index < 0:
        return ""
    left = abstract[:index]
    start = 0
    for boundary in _BOUNDARIES:
        found = left.rfind(boundary)
        while found >= 0:
            after = found + len(boundary)
            if boundary == ", " and _NUMERIC_CONTINUATION_RE.match(abstract, after):
                found = left.rfind(boundary, 0, found)
                continue
            start = max(start, after)
            break
    start = max(start, index - _MAX_CONTEXT)
    return left[start:]


def states_minimum_mass(measurement: Measurement, abstract: str) -> bool:
    """¿Presenta el abstract esta medida de masa como masa mínima / `m sin i`?

    Solo `parameter == mass`; radio y periodo nunca."""
    if measurement.parameter != MeasuredParameter.MASS:
        return False
    evidence = _collapse(measurement.evidence)
    context = _left_context(evidence, _collapse(abstract))
    return _MINIMUM_MASS_RE.search(_normalize(f"{context} {evidence}")) is not None
