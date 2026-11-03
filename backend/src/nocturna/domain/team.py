"""Primer autor de una referencia del archivo frente a los autores de un paper (T100, D1).

Funciones puras. La independencia de una `confirmacion_independiente` se
comprueba solo como «el apellido del primer autor de la referencia NO figura
entre los autores del artículo»; con cualquier dato ausente o no reconocible,
`shares_first_author` devuelve `None` y no hay confirmación.
"""

import re
import unicodedata

#: `<apellido(s)> [et al.| & X| and X] <año de 4 cifras>`. El apellido empieza
#: por letra y no lleva cifras (descarta «ExoFOP-TESS TOI», que no tiene año).
_REFERENCE_RE = re.compile(
    r"^\s*(?P<surname>[^\W\d_][^\d,&]*?)"
    r"(?:\s+et\s+al\.?|\s*&\s*[^\d]+?|\s+and\s+[^\d]+?)?"
    r"\s+(?P<year>(?:19|20)\d{2})[a-z]?\s*$"
)
_TEX_DOTLESS_RE = re.compile(r"\\([ij])(?![A-Za-z])(?:\{\}|\s+)?")
_TEX_ACCENT_RE = re.compile(r"\\['`^\"~=.]\s*\{?([A-Za-z])\}?")
_TEX_LETTER_ACCENT_RE = re.compile(r"\\[vcHukrdb](?![A-Za-z])\s*\{?([A-Za-z])\}?")
_TEX_SPECIAL_RE = re.compile(r"\\(ss|aa|AA|ae|AE|oe|OE|o|O|l|L)(?![A-Za-z])(?:\{\}|\s+)?")
_TEX_SPECIALS = {
    "ss": "ss",
    "aa": "a",
    "AA": "A",
    "ae": "ae",
    "AE": "AE",
    "oe": "oe",
    "OE": "OE",
    "o": "o",
    "O": "O",
    "l": "l",
    "L": "L",
}
#: Letras que NFKD no descompone, llevadas a ASCII.
_SPECIAL_LETTERS = str.maketrans(
    {
        "ø": "o",
        "Ø": "O",
        "ł": "l",
        "Ł": "L",
        "ß": "ss",
        "æ": "ae",
        "Æ": "AE",
        "œ": "oe",
        "Œ": "OE",
        "đ": "d",
        "Đ": "D",
        "ð": "d",
        "Ð": "D",
        "þ": "th",
        "Þ": "Th",
    }
)
_NON_WORD_RE = re.compile(r"[^a-z0-9]+")


def reference_first_author(refname: str) -> str | None:
    """Apellido(s) del primer autor de `refname` («del Burgo et al. 2026» ->
    «del Burgo»); `None` si no tiene la forma `<apellidos> [...] <año>`."""
    match = _REFERENCE_RE.match(refname)
    if match is None:
        return None
    surname = match.group("surname").strip()
    if not surname or surname.lower() in {"et", "al", "and"}:
        return None
    return surname


def _normalize(text: str) -> str:
    text = _TEX_DOTLESS_RE.sub(r"\1", text)
    text = _TEX_ACCENT_RE.sub(r"\1", text)
    text = _TEX_LETTER_ACCENT_RE.sub(r"\1", text)
    text = _TEX_SPECIAL_RE.sub(lambda m: _TEX_SPECIALS[m.group(1)], text)
    text = text.translate(_SPECIAL_LETTERS)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("{", "").replace("}", "").lower()
    return _NON_WORD_RE.sub(" ", text).strip()


def shares_first_author(refname: str, paper_authors: str | None) -> bool | None:
    """¿Figura el apellido del primer autor de `refname` como palabra(s)
    completa(s) entre `paper_authors`? `None` si falta un dato."""
    if paper_authors is None:
        return None
    surname = reference_first_author(refname)
    if surname is None:
        return None
    wanted = _normalize(surname)
    authors = _normalize(paper_authors)
    if not wanted or not authors:
        return None
    return re.search(rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])", authors) is not None
