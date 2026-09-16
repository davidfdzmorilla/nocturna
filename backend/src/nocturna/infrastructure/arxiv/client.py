"""Cliente HTTP de la API pública de arXiv.

Cumple `domain.sources.ArxivSource` estructuralmente (es un `Protocol`):
`ArxivClient` no hereda de él, es infraestructura quien satisface el
contrato del dominio, nunca al revés.

Sin reintentos: un fallo de arXiv a las 3 de la mañana se convierte en
`ArxivUnavailable` y el ítem/la noche sigue su curso (decisión de
`application/`, no de este módulo); un bucle de reintentos colgado es
justo lo que la ventana de ejecución de `CLAUDE.md` quiere evitar.
"""

from collections.abc import Callable, Sequence
from datetime import datetime

import httpx

from nocturna.domain.entities import Item
from nocturna.domain.sources import SourceFetch
from nocturna.infrastructure.arxiv.atom import ArxivEntry, ArxivFeedError, parse_feed
from nocturna.infrastructure.arxiv.mappers import entry_to_item
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter

ARXIV_API_URL = "https://export.arxiv.org/api/query"
# Política de cortesía de arXiv (https://info.arxiv.org/help/api/tou.html):
# al menos 3 s entre peticiones. Constante de módulo, no clave de
# configuración: no es un parámetro que se deba poder relajar por error.
MIN_REQUEST_INTERVAL_S = 3.0
# Neutro a propósito: no expone correo ni URL del repositorio.
USER_AGENT = "nocturna/0.1.0"

# Cota de cortesía frente a una respuesta desproporcionada antes de parsear:
# entrada externa y `xml.etree` es susceptible a expansión de entidades.
_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


class ArxivUnavailable(Exception):
    """La API de arXiv no respondió, respondió con error de servidor o la
    respuesta supera el tope de tamaño admitido."""


class ArxivClient:
    """Cliente de la API de arXiv. Cumple `domain.sources.ArxivSource`."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        limiter: RateLimiter,
        page_size: int,
        now: Callable[[], datetime],
    ) -> None:
        self._http = http
        self._limiter = limiter
        self._page_size = page_size
        self._now = now

    async def fetch_new(
        self, *, since: datetime, categories: Sequence[str], max_results: int
    ) -> SourceFetch:
        """Ítems nuevos de `categories` publicados desde `since`, hasta `max_results`.

        El filtro por `since` se aplica en cliente sobre `published`
        (determinista y testeable con fixture), no en `search_query`. La
        paginación corta en el primer motivo que aparezca: una página con
        menos de `page_size` entradas (no hay más en arXiv), la primera
        entrada con `published < since` (el orden descendente garantiza que
        lo siguiente también es antiguo), o el tope `max_results`
        (`truncated=True`, nada se pierde en silencio).
        """
        search_query = " OR ".join(f"cat:{category}" for category in categories)
        fetched_at = self._now()
        items: list[Item] = []
        skipped = 0
        truncated = False
        start = 0

        while True:
            page_max = min(self._page_size, max_results - len(items))
            if page_max <= 0:
                truncated = True
                break

            payload = await self._get(
                {
                    "search_query": search_query,
                    "sortBy": "submittedDate",
                    "sortOrder": "descending",
                    "start": start,
                    "max_results": page_max,
                }
            )
            parsed = parse_feed(payload)
            skipped += parsed.skipped

            reached_since = False
            for entry in parsed.entries:
                if entry.published_at < since:
                    reached_since = True
                    break
                items.append(entry_to_item(entry, fetched_at=fetched_at))
                if len(items) >= max_results:
                    truncated = True
                    break

            if reached_since or truncated:
                break
            # Corte por "página corta": hay que contar las entradas *recibidas*
            # de arXiv (válidas + descartadas), no solo las válidas. Si se
            # cuentan solo `parsed.entries`, una sola entrada malformada en la
            # página hace parecer que arXiv devolvió menos de lo pedido y la
            # paginación corta ahí, perdiendo en silencio el resto de páginas.
            if len(parsed.entries) + parsed.skipped < page_max:
                break

            start += page_max

        return SourceFetch(items=items, truncated=truncated, skipped=skipped)

    async def get_abstract(self, arxiv_id: str) -> ArxivEntry | None:
        """Última versión de una entrada por `arxiv_id` (sin versión).

        `None` si el id es desconocido para arXiv: no es un error, es una
        respuesta válida de "no existe".
        """
        payload = await self._get({"id_list": arxiv_id})
        try:
            parsed = parse_feed(payload)
        except ArxivFeedError:
            return None
        if not parsed.entries:
            return None
        return parsed.entries[0]

    async def _get(self, params: dict[str, str | int]) -> bytes:
        await self._limiter.acquire()
        try:
            response = await self._http.get(
                ARXIV_API_URL,
                params=params,
                headers={"User-Agent": USER_AGENT},
            )
        except httpx.HTTPError as exc:
            raise ArxivUnavailable(f"fallo consultando la API de arXiv: {exc}") from exc

        if response.status_code >= 500:
            raise ArxivUnavailable(f"la API de arXiv respondió {response.status_code}")

        content = response.content
        if len(content) > _MAX_RESPONSE_BYTES:
            raise ArxivUnavailable(
                f"respuesta de arXiv de {len(content)} bytes supera el tope de "
                f"{_MAX_RESPONSE_BYTES}"
            )
        return content
