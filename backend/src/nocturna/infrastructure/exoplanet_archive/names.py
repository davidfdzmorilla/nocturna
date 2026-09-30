"""Normalización de nombres de planeta para compararlos con el archivo."""

import re

_DASH_VARIANTS = "‐‑‒–—−"
_DIGIT_LETTER_BOUNDARY = re.compile(r"(?<=[0-9])(?=[a-zA-Z])")
_ALNUM_COMMA = re.compile(r"(?<=[0-9A-Za-z]),(?=[0-9A-Za-z])")
_WHITESPACE_RE = re.compile(r"\s+")


def clean_name(name: str) -> str:
    """Limpieza legible de un nombre, sin pasar a minúsculas.

    Unifica variantes unicode del guion y convierte `\\,`, `~` y las comas
    entre alfanuméricos en espacio (restos de LaTeX), colapsando espacios.
    """
    text = name.strip()
    for ch in _DASH_VARIANTS:
        text = text.replace(ch, "-")
    text = text.replace("\\,", " ").replace("~", " ")
    text = _ALNUM_COMMA.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


def normalize_name(name: str) -> str:
    """Normaliza un nombre de objeto para comparar de forma robusta.

    Unifica variantes unicode del guion, convierte `\\,`, `~` y las comas
    entre alfanuméricos en espacio (restos de LaTeX, "V1298,Tau b"), colapsa
    espacios repetidos e inserta un espacio entre un dígito y la letra que lo
    sigue sin espacio ("WASP-12b" -> "wasp-12 b"). Las comas que no van entre
    alfanuméricos no se tocan. Todo en minúsculas.
    """
    text = _DIGIT_LETTER_BOUNDARY.sub(" ", clean_name(name))
    return text.strip().lower()
