"""Parser del OAI-PMH de arXiv (`https://oaipmh.arxiv.org/oai`).

Produce `ArxivEntry`, el MISMO DTO que `atom.py`, a propósito: así las dos
vías de ingesta convergen en `mappers.py::entry_to_item`, que es el único
sitio donde nace un `Item`. Si mañana se añade un campo al DTO, revienta en
los dos parsers a la vez en vez de que uno se quede atrás en silencio.

Se usa el formato `arXivRaw`, no `arXiv` ni `oai_dc`:

- `oai_dc` da las categorías en prosa ("Astrophysics - Earth and Planetary
  Astrophysics") en vez de `astro-ph.EP`, y mete abstract y comentarios en
  dos `dc:description` indistinguibles. Rompería `Item.categories`.
- `arXiv` trae `<created>` con fecha pero SIN hora, así que perderíamos la
  hora de envío que hoy sí tenemos y con ella el orden dentro de la noche.
- `arXivRaw` es el único que trae el historial de versiones con timestamp
  RFC-2822 completo, que es exactamente nuestro `published_at`.

El `<datestamp>` del encabezado es de anuncio/modificación, no de envío:
una cosecha de un día trae también revisiones de papers viejos. No se usa
para `published_at`; quien filtre por novedad debe mirar la fecha de la
`v1` (ver `oai_client.py`, que aplica el mismo `published_at >= since` que
la vía Atom).
"""

import re
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET

from nocturna.infrastructure.arxiv.atom import ArxivEntry, ArxivFeedError

_OAI_NS = "http://www.openarchives.org/OAI/2.0/"
_RAW_NS = "http://arxiv.org/OAI/arXivRaw/"

_OAI = f"{{{_OAI_NS}}}"
_RAW = f"{{{_RAW_NS}}}"

# "oai:arXiv.org:2609.24987" -> "2609.24987". También cubre los
# identificadores antiguos con barra ("astro-ph/0601001").
_IDENTIFIER_PREFIX = "oai:arXiv.org:"

# "v1", "v12" -> 1, 12.
_VERSION_PATTERN = re.compile(r"^v(?P<version>\d+)$")

# Códigos de `<error>` que NO son un fallo: "no hay nada que cosechar" es
# una respuesta legítima a un rango de fechas sin novedades. Cualquier otro
# código (badArgument, cannotDisseminateFormat, badResumptionToken...) sí lo
# es, y distinguirlos importa: un error de protocolo tratado como "vacío"
# dejaría la cola sin ítems sin que nadie se entere.
_EMPTY_ERROR_CODES = frozenset({"noRecordsMatch"})


class _IncompleteRecord(Exception):
    """Uso interno: un registro concreto no trae los campos mínimos.

    No cruza el límite de `parse_oai_response`: se captura ahí y se traduce
    en un incremento de `skipped`, nunca en una excepción que tumbe la
    cosecha completa por un solo paper roto.
    """


@dataclass(frozen=True, slots=True)
class ParsedOaiPage:
    """Una página de respuesta de `ListRecords`.

    `resumption_token` es `None` cuando no hay más páginas. arXiv lo
    devuelve vacío en la última, y su `completeListSize` no es de fiar
    (caduca a diario), así que el corte lo pone este campo y los topes del
    cliente, nunca un total anunciado.
    """

    entries: tuple[ArxivEntry, ...]
    resumption_token: str | None
    skipped: int


def parse_oai_response(payload: bytes) -> ParsedOaiPage:
    """Interpreta una respuesta `ListRecords` de OAI-PMH.

    Lanza `ArxivFeedError` si el XML no se puede interpretar o si la
    respuesta trae un `<error>` de protocolo. Un `noRecordsMatch` devuelve
    una página vacía, que no es un fallo. Un registro individual incompleto
    (sin abstract, sin categorías, con una fecha ilegible) no revienta el
    parseo: se descarta y se cuenta en `skipped`, igual que en `atom.py`.
    """
    try:
        root = ET.fromstring(payload)
    except ET.ParseError as exc:
        raise ArxivFeedError(f"la respuesta de arXiv no es XML válido: {exc}") from exc

    error = root.find(f"{_OAI}error")
    if error is not None:
        code = error.get("code") or "desconocido"
        if code in _EMPTY_ERROR_CODES:
            return ParsedOaiPage(entries=(), resumption_token=None, skipped=0)
        message = (error.text or "").strip() or "sin detalle"
        raise ArxivFeedError(f"el OAI-PMH de arXiv devolvió el error {code!r}: {message}")

    list_records = root.find(f"{_OAI}ListRecords")
    if list_records is None:
        raise ArxivFeedError("la respuesta de OAI-PMH no contiene ListRecords ni error")

    entries: list[ArxivEntry] = []
    skipped = 0
    for record in list_records.findall(f"{_OAI}record"):
        try:
            entries.append(_parse_record(record))
        except _IncompleteRecord:
            skipped += 1

    return ParsedOaiPage(
        entries=tuple(entries),
        resumption_token=_parse_resumption_token(list_records),
        skipped=skipped,
    )


def _parse_resumption_token(list_records: ET.Element) -> str | None:
    element = list_records.find(f"{_OAI}resumptionToken")
    if element is None or element.text is None:
        return None
    token = element.text.strip()
    return token or None


def _text(element: ET.Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    stripped = element.text.strip()
    return stripped or None


def _normalize_whitespace(text: str) -> str:
    # arXiv rellena title/abstract a 80 columnas: colapsa saltos de línea y
    # espacios de relleno a un único espacio, igual que `atom.py`.
    return " ".join(text.split())


def _require_text(element: ET.Element, tag: str) -> str:
    text = _text(element.find(tag))
    if text is None:
        raise _IncompleteRecord(f"falta '{tag}' o está vacío")
    return text


def _parse_identifier(header: ET.Element) -> str:
    raw = _text(header.find(f"{_OAI}identifier"))
    if not raw or not raw.startswith(_IDENTIFIER_PREFIX):
        raise _IncompleteRecord(f"identificador OAI ilegible: {raw!r}")
    arxiv_id = raw[len(_IDENTIFIER_PREFIX) :]
    if not arxiv_id:
        raise _IncompleteRecord(f"identificador OAI sin id de arXiv: {raw!r}")
    return arxiv_id


def _parse_rfc2822(text: str) -> datetime:
    """RFC-2822 -> `datetime` con zona horaria, o `_IncompleteRecord`.

    `parsedate_to_datetime` devuelve un `datetime` **naive** (sin lanzar)
    cuando el offset es `-0000` ("zona desconocida" según RFC 2822) o falta.
    Dejarlo pasar hacía que el registro superara el parser y reventara más
    tarde al comparar con `since`: un `TypeError` que tumbaba la cosecha
    entera de la noche por un solo paper con la fecha mal puesta, justo lo
    que el canal `skipped` existe para evitar.
    """
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError) as exc:
        raise _IncompleteRecord(f"fecha ilegible: {text!r}") from exc
    if parsed.tzinfo is None:
        raise _IncompleteRecord(f"fecha sin zona horaria: {text!r}")
    return parsed


def _parse_versions(metadata: ET.Element) -> tuple[int, datetime, datetime]:
    """Devuelve `(version_mas_alta, fecha_de_v1, fecha_de_la_ultima)`.

    `published_at` sale de la `v1` porque es la fecha de envío original, la
    misma semántica que el `<published>` del Atom; `updated_at` sale de la
    versión más reciente. Un registro sin `v1` legible se descarta: sin
    fecha de envío no se puede decidir si es novedad.
    """
    by_version: dict[int, datetime] = {}
    for element in metadata.findall(f"{_RAW}version"):
        raw_version = element.get("version") or ""
        match = _VERSION_PATTERN.match(raw_version)
        date_text = _text(element.find(f"{_RAW}date"))
        if match is None or date_text is None:
            continue
        by_version[int(match.group("version"))] = _parse_rfc2822(date_text)

    if 1 not in by_version:
        raise _IncompleteRecord("registro sin versión v1 legible")

    latest = max(by_version)
    return latest, by_version[1], by_version[latest]


def _parse_categories(metadata: ET.Element) -> tuple[str, ...]:
    """`<categories>` viene separado por espacios y con la primaria primero,
    que es el mismo orden que produce `atom.py`."""
    raw = _text(metadata.find(f"{_RAW}categories"))
    if not raw:
        raise _IncompleteRecord("registro sin categorías")

    ordered: list[str] = []
    for term in raw.split():
        if term not in ordered:
            ordered.append(term)
    return tuple(ordered)


def _parse_record(record: ET.Element) -> ArxivEntry:
    header = record.find(f"{_OAI}header")
    if header is None:
        raise _IncompleteRecord("registro sin header")

    # `deletedRecord = persistent`: arXiv anuncia las retiradas con un
    # header marcado y SIN bloque <metadata>. Se descarta contándolo en
    # `skipped`, el canal que `SourceFetch` ya tiene para que nada
    # desaparezca en silencio.
    if header.get("status") == "deleted":
        raise _IncompleteRecord("registro borrado")

    arxiv_id = _parse_identifier(header)

    metadata = record.find(f"{_OAI}metadata")
    raw = metadata.find(f"{_RAW}arXivRaw") if metadata is not None else None
    if raw is None:
        raise _IncompleteRecord("registro sin metadatos arXivRaw")

    version, published_at, updated_at = _parse_versions(raw)
    return ArxivEntry(
        arxiv_id=arxiv_id,
        version=version,
        title=_normalize_whitespace(_require_text(raw, f"{_RAW}title")),
        abstract=_normalize_whitespace(_require_text(raw, f"{_RAW}abstract")),
        categories=_parse_categories(raw),
        published_at=published_at,
        updated_at=updated_at,
    )
