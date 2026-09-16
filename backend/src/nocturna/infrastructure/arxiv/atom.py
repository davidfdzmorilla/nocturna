"""DTO y parser del feed Atom de la API de arXiv.

Se usa `xml.etree.ElementTree` de la biblioteca estándar, nada de
`feedparser`: el feed es Atom simple con tres espacios de nombres
(Atom, `arxiv` y `opensearch`) y no hace falta más.

`ArxivEntry` y `ParsedFeed` son DTOs de infraestructura, no entidades de
dominio: no cruzan hacia `domain/` ni `application/`. La traducción a
`Item` vive en `mappers.py`.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from xml.etree import ElementTree as ET

_ATOM_NS = "http://www.w3.org/2005/Atom"
_ARXIV_NS = "http://arxiv.org/schemas/atom"
_OPENSEARCH_NS = "http://a9.com/-/spec/opensearch/1.1/"

_ATOM = f"{{{_ATOM_NS}}}"
_ARXIV = f"{{{_ARXIV_NS}}}"
_OPENSEARCH = f"{{{_OPENSEARCH_NS}}}"

# "http://arxiv.org/abs/2609.17526v1" -> arxiv_id="2609.17526", version=1.
_ID_PATTERN = re.compile(r"/abs/(?P<arxiv_id>[^/]+?)v(?P<version>\d+)$")

# Título de la única entrada que trae el feed de error de arXiv (ver
# `tests/fixtures/arxiv/feed_error.xml`). Llega con HTTP 200: sin esta
# detección, un id inválido parecería una respuesta vacía normal.
_ERROR_ENTRY_TITLE = "Error"


class ArxivFeedError(Exception):
    """El feed de arXiv señala un error (id inválido, consulta malformada) o
    el XML recibido no se puede interpretar."""


class _IncompleteEntry(Exception):
    """Uso interno: una entrada concreta no trae los campos mínimos.

    No cruza el límite de `parse_feed`: se captura ahí y se traduce en un
    incremento de `skipped`, nunca en una excepción que tumbe la ingesta
    completa por un solo paper roto.
    """


@dataclass(frozen=True, slots=True)
class ArxivEntry:
    """Una entrada del feed Atom de arXiv, ya validada y normalizada."""

    arxiv_id: str
    version: int
    title: str
    abstract: str
    categories: tuple[str, ...]
    published_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ParsedFeed:
    """Resultado de interpretar un feed Atom completo."""

    entries: tuple[ArxivEntry, ...]
    total_results: int
    skipped: int


def parse_feed(payload: bytes) -> ParsedFeed:
    """Interpreta un feed Atom de arXiv.

    Lanza `ArxivFeedError` si el XML no se puede interpretar o si el feed es
    el feed de error de arXiv (una única entrada de título `Error`). Una
    entrada individual incompleta (sin `summary`, sin categorías, con una
    fecha ilegible) no revienta el parseo: se descarta y se cuenta en
    `skipped`.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ArxivFeedError(f"la respuesta de arXiv no es XML válido: {exc}") from exc

    total_results = _parse_total_results(root)
    entry_elements = root.findall(f"{_ATOM}entry")

    if len(entry_elements) == 1 and _text(entry_elements[0].find(f"{_ATOM}title")) == (
        _ERROR_ENTRY_TITLE
    ):
        message = _text(entry_elements[0].find(f"{_ATOM}summary")) or "feed de error de arXiv"
        raise ArxivFeedError(message)

    entries: list[ArxivEntry] = []
    skipped = 0
    for element in entry_elements:
        try:
            entries.append(_parse_entry(element))
        except _IncompleteEntry:
            skipped += 1

    return ParsedFeed(entries=tuple(entries), total_results=total_results, skipped=skipped)


def _parse_total_results(root: ET.Element) -> int:
    element = root.find(f"{_OPENSEARCH}totalResults")
    text = _text(element)
    if not text:
        return 0
    return int(text)


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None


def _normalize_whitespace(text: str) -> str:
    # arXiv rellena title/summary a 80 columnas: colapsa saltos de línea y
    # espacios de relleno a un único espacio.
    return " ".join(text.split())


def _require_text(element: ET.Element, tag: str) -> str:
    text = _text(element.find(tag))
    if text is None:
        raise _IncompleteEntry(f"falta '{tag}' o está vacío")
    return text


def _parse_id(element: ET.Element) -> tuple[str, int]:
    raw_id = _text(element.find(f"{_ATOM}id"))
    match = _ID_PATTERN.search(raw_id) if raw_id else None
    if match is None:
        raise _IncompleteEntry(f"id de arXiv ilegible: {raw_id!r}")
    return match.group("arxiv_id"), int(match.group("version"))


def _parse_categories(element: ET.Element) -> tuple[str, ...]:
    primary_element = element.find(f"{_ARXIV}primary_category")
    primary = primary_element.get("term") if primary_element is not None else None
    terms = [
        term for category in element.findall(f"{_ATOM}category") if (term := category.get("term"))
    ]
    if not terms and not primary:
        raise _IncompleteEntry("entrada sin categorías")

    ordered: list[str] = []
    if primary:
        ordered.append(primary)
    for term in terms:
        if term not in ordered:
            ordered.append(term)
    return tuple(ordered)


def _parse_datetime(text: str) -> datetime:
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise _IncompleteEntry(f"fecha ilegible: {text!r}") from exc


def _parse_entry(element: ET.Element) -> ArxivEntry:
    arxiv_id, version = _parse_id(element)
    title = _normalize_whitespace(_require_text(element, f"{_ATOM}title"))
    abstract = _normalize_whitespace(_require_text(element, f"{_ATOM}summary"))
    categories = _parse_categories(element)
    published_at = _parse_datetime(_require_text(element, f"{_ATOM}published"))
    updated_at = _parse_datetime(_require_text(element, f"{_ATOM}updated"))
    return ArxivEntry(
        arxiv_id=arxiv_id,
        version=version,
        title=title,
        abstract=abstract,
        categories=categories,
        published_at=published_at,
        updated_at=updated_at,
    )
