"""Cliente HTTP de la API pública de arXiv.

Cumple `domain.sources.ArxivSource` estructuralmente (es un `Protocol`):
`ArxivClient` no hereda de él, es infraestructura quien satisface el
contrato del dominio, nunca al revés.

Con reintentos ante fallos transitorios (406, 429, 5xx y errores de
transporte): el 406 observado el 2026-09-21 llegaba con cuerpo vacío, sin
`Retry-After`, con cabeceras de Fastly/Varnish -- lo rechazaba el CDN
delante de arXiv, no la aplicación, y era transitorio (la misma consulta
devolvió 200 minutos después). La política de reintento (cuántos intentos,
cuánto esperar entre ellos, cuándo cortar) vive en
`infrastructure/arxiv/retry.py::RetryPolicy`/`Retrier`, que no conoce HTTP;
la clasificación de qué código o excepción es reintentable vive aquí. Un
fallo que agota los reintentos se convierte en `ArxivUnavailable` y el
ítem/la noche sigue su curso (decisión de `application/`, no de este
módulo); `max_elapsed_s` acota cuándo puede iniciarse un nuevo intento, no
la duración total de la secuencia (ver `retry.py`), así que evita que la
ingesta reintente indefinidamente sin ser una cota exacta del tiempo total
que puede consumir `_get` -- justo lo que la ventana de ejecución de
`CLAUDE.md` quiere evitar.

`_get` construye un `Retrier` nuevo en cada llamada a partir de
`self._retry_policy` en vez de guardar un único `Retrier` como atributo de
instancia: `ArxivClient` puede recibir llamadas concurrentes a
`fetch_new`/`get_abstract` (el Agent SDK puede paralelizar llamadas de
herramienta sobre el mismo cliente MCP, `infrastructure/mcp/arxiv_server.py`),
y un `Retrier` compartido mezclaría los contadores de intentos de dos
peticiones simultáneas.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime

import httpx

from nocturna.domain.entities import Item
from nocturna.domain.sources import SourceFetch
from nocturna.infrastructure.arxiv import transport
from nocturna.infrastructure.arxiv.atom import ArxivEntry, ArxivFeedError, parse_feed
from nocturna.infrastructure.arxiv.mappers import entry_to_item
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.arxiv.retry import RetryPolicy

_logger = logging.getLogger(__name__)

ARXIV_API_URL = "https://export.arxiv.org/api/query"
# Nombre del servicio en los mensajes de error, para distinguir esta vía de
# la de OAI-PMH (T60.c) en el log de la noche.
_SERVICE = "la API de arXiv"

# Reexportados desde `transport.py`, donde viven desde T60.c porque las dos
# vías de ingesta comparten clasificación, cortesía y mensajes. Se mantienen
# accesibles con estos nombres para no romper quien los importa de aquí.
MIN_REQUEST_INTERVAL_S = transport.MIN_REQUEST_INTERVAL_S
USER_AGENT = transport.USER_AGENT
_MAX_RESPONSE_BYTES = transport.MAX_RESPONSE_BYTES
ArxivUnavailable = transport.ArxivUnavailable
_is_retryable_status = transport.is_retryable_status


def _status_error_message(status_code: int) -> str:
    return transport.status_error_message(status_code, service=_SERVICE)


class ArxivClient:
    """Cliente de la API de arXiv. Cumple `domain.sources.ArxivSource`."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        limiter: RateLimiter,
        page_size: int,
        now: Callable[[], datetime],
        retry_policy: RetryPolicy,
    ) -> None:
        self._http = http
        self._limiter = limiter
        self._page_size = page_size
        self._now = now
        self._retry_policy = retry_policy

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
        """Delegado en `transport.fetch_with_retry` desde T60.c: la
        clasificación de códigos reintentables, el espaciado de cortesía y
        el mensaje de agotamiento son comunes a las dos vías de ingesta, y
        tenerlos duplicados aquí y en `oai_client.py` permitiría que
        divergieran sin que nadie lo notase.

        `start` se pasa como contexto solo para identificar en logs y
        mensajes qué página falló; `get_abstract` no pagina, así que vale
        `None` ahí.
        """
        return await transport.fetch_with_retry(
            http=self._http,
            url=ARXIV_API_URL,
            params=params,
            limiter=self._limiter,
            retry_policy=self._retry_policy,
            logger=_logger,
            service=_SERVICE,
            context=params.get("start"),
        )
