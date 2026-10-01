"""Normalización de nombres de planeta para compararlos con el archivo."""

import re

from nocturna.domain.exoplanet_filter import normalize_text

_DIGIT_LETTER_BOUNDARY = re.compile(r"(?<=[0-9])(?=[a-zA-Z])")


def clean_name(name: str) -> str:
    """Limpieza legible de un nombre, sin pasar a minúsculas.

    Unifica variantes unicode del guion y convierte `\\,`, `~` y las comas
    entre alfanuméricos en espacio (restos de LaTeX), colapsando espacios. La
    lógica vive en `domain.exoplanet_filter.normalize_text` (única fuente).
    """
    return normalize_text(name)


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
