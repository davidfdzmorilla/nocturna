"""Traducción DTO de arXiv (Atom) -> entidad de dominio `Item`.

Una función pura con argumentos nombrados, espejo de
`infrastructure/db/mappers.py`: nada de `**vars(entry)` ni de pasar el DTO
entero, para que un campo nuevo en `ArxivEntry` que se olvide aquí reviente
con un `TypeError` en el sitio exacto donde falta, no se cuele en silencio.
"""

from datetime import datetime

from nocturna.domain.entities import Item
from nocturna.infrastructure.arxiv.atom import ArxivEntry

SOURCE_ARXIV = "arxiv"


def entry_to_item(entry: ArxivEntry, *, fetched_at: datetime) -> Item:
    """Traduce una entrada Atom ya parseada a un `Item` nuevo (`status=NEW`).

    `external_id` es `entry.arxiv_id` SIN versión: la unicidad en base de
    datos es `(source, external_id)`, y conservar la versión haría que una
    revisión (`v2`) de un paper ya ingerido entrara como ítem nuevo,
    gastando una lectura del Reader sobre un paper ya analizado.

    `published_at` toma `entry.published_at`, no `entry.updated_at`: fase 1
    publica novedades, no revisiones (ver
    `tests/fixtures/arxiv/feed_revised_entries.xml`).
    """
    return Item(
        source=SOURCE_ARXIV,
        external_id=entry.arxiv_id,
        title=entry.title,
        abstract=entry.abstract,
        categories=list(entry.categories),
        published_at=entry.published_at,
        fetched_at=fetched_at,
    )
