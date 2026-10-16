"""Caso de uso: ingesta de novedades de arXiv.

Recibe el puerto `ArxivSource` (interfaz de dominio, cumplida
estructuralmente por `infrastructure/arxiv/client.py` y `oai_client.py`) y `ItemRepository`
por constructor. No importa nada de `infrastructure/` ni lee configuración
global (`config/pipeline.toml`): `since`, `categories` y `max_results` los
decide quien lo invoca (el CLI en T20-paso7, más adelante `RunNight`).

No abre ni cierra transacción. `unit_of_work` (`infrastructure/db/session.py`)
es la única frontera transaccional del proyecto (ver ADR 0003); este caso de
uso asume que el `ItemRepository` que recibe ya vive dentro de una unidad de
trabajo abierta por el llamador, y no confirma ni deshace esa transacción por
su cuenta.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from nocturna.domain.entities import Item
from nocturna.domain.exoplanet_filter import ExoplanetFilter
from nocturna.domain.repositories import ItemRepository
from nocturna.domain.sources import ArxivSource


def default_since(
    *, now: datetime, last_ingest_at: datetime | None, max_catchup_days: int
) -> datetime:
    """Desde cuándo ingesta la noche cuando no hay `--since` (T93, ADR 0032).

    00:00 UTC de `max(min(ayer, fecha de la última ingesta), hoy - max_catchup_days)`:
    nunca más tarde de ayer (el suelo de siempre), retrocede hasta la última
    ingesta si hubo noches sin ejecutar o fallidas, y no más de
    `max_catchup_days` días. Sin marca previa, ayer.
    """
    today = now.astimezone(UTC).date()
    yesterday = today - timedelta(days=1)
    start = yesterday
    if last_ingest_at is not None:
        start = min(yesterday, last_ingest_at.astimezone(UTC).date())
    start = max(start, today - timedelta(days=max_catchup_days))
    return datetime.combine(start, time.min, tzinfo=UTC)


def since_was_capped(
    *, now: datetime, last_ingest_at: datetime | None, max_catchup_days: int
) -> bool:
    """Si `default_since` acotó el retroceso a `max_catchup_days` (T93).

    Misma aritmética de fechas UTC que `default_since`: la marca es anterior
    al día `hoy - max_catchup_days`. Estar justo en ese día no es acotar.
    """
    if last_ingest_at is None:
        return False
    today = now.astimezone(UTC).date()
    return last_ingest_at.astimezone(UTC).date() < today - timedelta(days=max_catchup_days)


@dataclass(frozen=True, slots=True)
class IngestResult:
    """Resultado de una ejecución de `IngestArxiv`, para que el CLI lo muestre."""

    fetched: int
    new: int
    duplicates: int
    skipped: int
    truncated: bool
    items: list[Item]


class IngestArxiv:
    """Ingesta ítems nuevos de arXiv y los persiste como `Item`.

    Deliberadamente **no aplica el tope de ítems procesados por noche**
    (`limits` en `config/pipeline.toml`). Ese tope es de la *selección* de
    qué se procesa esta noche (T44, vía `ItemRepository.next_unread`), no de
    la *ingesta*. Si esta clase recortara aquí, los papers sobrantes se
    perderían para siempre: `since` avanza en la siguiente ejecución y nadie
    vuelve a pedirlos, así que un recorte en la ingesta no ahorra
    presupuesto, borra datos. La ingesta cuesta una petición HTTP a arXiv, no
    tokens de la suscripción; el gasto que ese tope protege empieza en el
    Reader, no aquí. No "arreglar" esto añadiendo el tope en T30 ni en T44:
    el tope ya vive donde debe, en la selección.
    """

    def __init__(
        self, source: ArxivSource, items: ItemRepository, exoplanet_filter: ExoplanetFilter
    ) -> None:
        self._source = source
        self._items = items
        self._exoplanet_filter = exoplanet_filter

    async def __call__(
        self, *, since: datetime, categories: Sequence[str], max_results: int
    ) -> IngestResult:
        fetch = await self._source.fetch_new(
            since=since, categories=categories, max_results=max_results
        )
        # T79: marca persistida en la ingesta (decide reader-v3 vs v2 después).
        for item in fetch.items:
            item.exoplanet_match = self._exoplanet_filter.matches(item.title, item.abstract)
        new = self._items.add_many(fetch.items)
        duplicates = len(fetch.items) - new
        return IngestResult(
            fetched=len(fetch.items),
            new=new,
            duplicates=duplicates,
            skipped=fetch.skipped,
            truncated=fetch.truncated,
            items=fetch.items,
        )
