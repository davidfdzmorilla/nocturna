"""Filtro de exoplanetas para decidir qué ítems leen con `reader-v3` (T79).

Dominio puro (solo `re`). Las listas llegan de `[exoplanet_filter]` en
`config/pipeline.toml`; aquí no hay patrones por defecto.

Normalización previa (coherente con `exoplanet_archive/names.py`, T74): `~`,
`\\,` y las comas entre alfanuméricos pasan a espacio, las variantes unicode
del guion a `-` y los espacios se colapsan. Así `HD~189733~b` o
`V1298\\,Tau\\,b` casan igual que su forma limpia.

- `keywords`: palabra completa (`\\b...\\b`), sin distinguir mayúsculas.
- `designation_patterns`: regex que SÍ distinguen mayúsculas (la letra de
  planeta es minúscula; `HD 189733 A` es una componente estelar y no casa).
  Se buscan con `re.search` sobre el texto normalizado.
"""

import re
from dataclasses import dataclass, field

from nocturna.domain.errors import InvariantViolation

_DASH_VARIANTS = "‐‑‒–—−"
_ALNUM_COMMA = re.compile(r"(?<=[0-9A-Za-z]),(?=[0-9A-Za-z])")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normaliza título/abstract para buscar (ver docstring del módulo)."""
    for ch in _DASH_VARIANTS:
        text = text.replace(ch, "-")
    text = text.replace("\\,", " ").replace("~", " ")
    text = _ALNUM_COMMA.sub(" ", text)
    return _WHITESPACE_RE.sub(" ", text).strip()


@dataclass(frozen=True, slots=True)
class ExoplanetFilter:
    """Decide si un título+abstract trata de exoplanetas.

    Listas vacías: nunca casa. Un patrón de designación que no compila lanza
    `InvariantViolation` al construir.
    """

    keywords: tuple[str, ...]
    designation_patterns: tuple[str, ...]
    _keywords_re: re.Pattern[str] | None = field(init=False, repr=False, compare=False)
    _designation_res: tuple[re.Pattern[str], ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        keywords = tuple(k for k in self.keywords if k)
        if keywords:
            alternation = "|".join(re.escape(k) for k in keywords)
            keywords_re: re.Pattern[str] | None = re.compile(
                rf"\b(?:{alternation})\b", re.IGNORECASE
            )
        else:
            keywords_re = None
        compiled: list[re.Pattern[str]] = []
        for pattern in self.designation_patterns:
            try:
                compiled.append(re.compile(pattern))
            except re.error as exc:
                raise InvariantViolation(
                    f"designation_pattern no compila: {pattern!r} ({exc})"
                ) from exc
        object.__setattr__(self, "_keywords_re", keywords_re)
        object.__setattr__(self, "_designation_res", tuple(compiled))

    def matches(self, title: str, abstract: str) -> bool:
        text = normalize_text(f"{title}\n{abstract}")
        if self._keywords_re is not None and self._keywords_re.search(text):
            return True
        return any(rx.search(text) for rx in self._designation_res)
