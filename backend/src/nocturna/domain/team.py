"""Primer autor de una referencia del archivo frente a los autores de un paper (T100, D1).

Funciones puras. La independencia de una `confirmacion_independiente` se
comprueba solo como «el apellido del primer autor de la referencia NO figura
entre los autores del artículo»; con cualquier dato ausente o no reconocible,
`shares_first_author` devuelve `None` y no hay confirmación.

Tras normalizar (TeX, acentos, letras especiales) se aplica un pliegue por
palabra, igual en el apellido y en los autores: en palabras de al menos
`_FOLD_MIN_LETTERS` (5) letras, `ue->u`, `oe->o`, `ae->a`, `aa->a`, `dj->d`
(Müller = Mueller = Muller; Xue y Xu, de menos de 5 letras, no se pliegan).
Propiedad: el pliegue es una función de cada palabra aplicada a ambos lados y
la comparación es por secuencia de palabras, así que solo puede pasar un
resultado de False a True, nunca de True a False; una palabra de 5 o más
letras no queda vacía, así que no crea `None` nuevos. Límites: Baer/Bär y
Loeb/Löb no quedan cubiertos (palabras de menos de 5 letras). Falsos
aceptados conocidos: Rafael/Rafał, Michael/Michał, Aerts/Arts.
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
_TEX_SPECIAL_RE = re.compile(r"\\(ss|aa|AA|ae|AE|oe|OE|dj|DJ|o|O|l|L)(?![A-Za-z])(?:\{\}|\s+)?")
_TEX_SPECIALS = {
    "ss": "ss",
    "aa": "a",
    "AA": "A",
    "ae": "ae",
    "AE": "AE",
    "oe": "oe",
    "OE": "OE",
    "dj": "d",
    "DJ": "D",
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
        "ẞ": "SS",
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
_FOLD_MIN_LETTERS = 5
_FOLDS: tuple[tuple[str, str], ...] = (
    ("ue", "u"),
    ("oe", "o"),
    ("ae", "a"),
    ("aa", "a"),
    ("dj", "d"),
)


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


def _fold(token: str) -> str:
    if len(token) < _FOLD_MIN_LETTERS:
        return token
    for old, new in _FOLDS:
        token = token.replace(old, new)
    return token


def _canonical(text: str) -> str:
    return " ".join(_fold(t) for t in _normalize(text).split())


def shares_first_author(refname: str, paper_authors: str | None) -> bool | None:
    """¿Figura el apellido del primer autor de `refname` como palabra(s)
    completa(s) entre `paper_authors`? `None` si falta un dato."""
    if paper_authors is None:
        return None
    surname = reference_first_author(refname)
    if surname is None:
        return None
    wanted = _canonical(surname)
    authors = _canonical(paper_authors)
    if not wanted or not authors:
        return None
    return re.search(rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])", authors) is not None
