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
from nocturna.infrastructure.arxiv.atom import ArxivEntry, ArxivFeedError, parse_feed
from nocturna.infrastructure.arxiv.mappers import entry_to_item
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.arxiv.retry import Retrier, RetryPolicy

_logger = logging.getLogger(__name__)

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

# Texto en castellano de `Retrier.stop_reason`, para el mensaje de
# `ArxivUnavailable` que agota los reintentos: el identificador en
# snake_case del atributo (usado tal cual en los campos JSON de
# `arxiv.retry_exhausted`, donde sí es lo correcto) no encaja dentro de una
# frase en castellano dirigida a quien lea el resumen de la noche.
_STOP_REASON_TEXT = {
    "max_attempts": "se agotaron los intentos",
    "max_elapsed": "se agotó el tiempo límite de reintento",
}


class ArxivUnavailable(Exception):
    """La API de arXiv no respondió, respondió con un código distinto de 200
    que agotó los reintentos (si era reintentable) o que se rechazó sin
    reintentar, o la respuesta supera el tope de tamaño admitido."""


def _is_retryable_status(status_code: int) -> bool:
    """406 (el caso observado el 2026-09-21, ver docstring del módulo), 429
    y cualquier 5xx son transitorios; el resto de códigos que no son 200
    (400, 404, 414... y también 1xx, 2xx distinto de 200 y 3xx, ver
    `_status_error_message`) no lo son: una consulta mal formada, o una
    respuesta que no es el 200 esperado, no se arregla esperando."""
    return status_code in (406, 429) or status_code >= 500


def _status_error_message(status_code: int) -> str:
    # Se distingue 4xx de 5xx en el mensaje porque no significan lo mismo
    # para quien lea el log a las 3 de la mañana: un 5xx es un problema del
    # lado de arXiv (nada que ajustar aquí); un 4xx (406, 429...) sugiere
    # algo sobre *nuestra* petición -- cabecera, límite de cortesía, cambio
    # de política -- aunque, como el 406 observado, también puede ser
    # transitorio del lado de arXiv. El resto (1xx, 2xx distinto de 200,
    # 3xx -- httpx no sigue redirecciones por defecto, y el mismo CDN que
    # dio el 406 puede dar un 301) también se nombra explícitamente: sin
    # este caso, caía en `parse_feed` y producía el diagnóstico engañoso
    # ("XML inválido") que este módulo existe para evitar.
    if status_code >= 500:
        return f"la API de arXiv respondió {status_code} (error de servidor)"
    if status_code >= 400:
        return f"la API de arXiv respondió {status_code} (rechazo de la petición)"
    return f"la API de arXiv respondió {status_code} (código inesperado, no es 200)"


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
        """Ejecuta la petición dentro de la secuencia de un `Retrier` nuevo
        (ver docstring del módulo: por qué no se reutiliza uno solo entre
        llamadas), reintentando SIEMPRE la misma página (mismos `params`:
        `start` y `max_results` no avanzan entre reintentos). `start` se
        extrae de `params` solo para identificar en logs/mensajes qué
        página falló -- `get_abstract` no pagina, así que vale `None` ahí.
        `limiter.acquire()` se adquiere dentro de cada intento, no una sola
        vez fuera del bucle: así cada reintento hereda también el
        espaciado de cortesía de 3 s.

        Cualquier código distinto de 200 se trata como `ArxivUnavailable`
        *antes* de intentar parsear: un cuerpo que no es una respuesta 200
        del feed no es XML válido del feed, y dejarlo caer hasta
        `parse_feed` produce un diagnóstico engañoso ("XML inválido") que
        oculta la causa real (observado en producción: un 406 se registró
        como "no element found", cuando el problema era el propio código
        de estado).
        """
        retrier = Retrier(self._retry_policy)
        start = params.get("start")
        last_message = ""
        last_reason: str | None = None
        last_status: int | None = None
        last_exc: Exception | None = None

        async for attempt in retrier.attempts():
            if attempt > 1:
                _logger.warning(
                    "arxiv.retry",
                    extra={
                        "event": "arxiv.retry",
                        "attempt": attempt,
                        "max_attempts": retrier.policy.max_attempts,
                        "reason": last_reason,
                        "status_code": last_status,
                        "delay_s": retrier.last_delay_s,
                        "elapsed_s": retrier.elapsed_s,
                    },
                )

            await self._limiter.acquire()
            try:
                response = await self._http.get(
                    ARXIV_API_URL,
                    params=params,
                    headers={"User-Agent": USER_AGENT},
                )
            except httpx.TransportError as exc:
                # Timeout, corte de conexión, RemoteProtocolError...:
                # transitorio, reintentable.
                last_message = f"fallo consultando la API de arXiv: {exc}"
                last_reason = "transport_error"
                last_status = None
                last_exc = exc
                continue
            except httpx.HTTPError as exc:
                # httpx.HTTPError no-transporte (por ejemplo, una URL
                # inválida): no es transitorio, no se reintenta.
                raise ArxivUnavailable(f"fallo consultando la API de arXiv: {exc}") from exc

            if _is_retryable_status(response.status_code):
                last_message = _status_error_message(response.status_code)
                last_reason = f"http_{response.status_code}"
                last_status = response.status_code
                last_exc = ArxivUnavailable(last_message)
                continue

            if response.status_code != 200:
                # No solo 4xx/5xx no reintentables: también 1xx, 2xx
                # distinto de 200 y 3xx (httpx no sigue redirecciones por
                # defecto) -- ver docstring del método y de
                # `_status_error_message`.
                raise ArxivUnavailable(_status_error_message(response.status_code))

            content = response.content
            if len(content) > _MAX_RESPONSE_BYTES:
                raise ArxivUnavailable(
                    f"respuesta de arXiv de {len(content)} bytes supera el tope de "
                    f"{_MAX_RESPONSE_BYTES}"
                )

            if attempt > 1:
                _logger.info(
                    "arxiv.retry_recovered",
                    extra={
                        "event": "arxiv.retry_recovered",
                        "attempts_made": retrier.attempts_made,
                        "elapsed_s": retrier.elapsed_s,
                    },
                )
            return content

        # El `async for` solo termina sin haber hecho `return content` si
        # `retrier.attempts()` se agotó (por `max_attempts` o por
        # `max_elapsed_s`): todos los intentos fallaron de forma
        # reintentable. `last_message`/`last_reason`/`last_status`/`last_exc`
        # son los del último intento.
        _logger.error(
            "arxiv.retry_exhausted",
            extra={
                "event": "arxiv.retry_exhausted",
                "attempt": retrier.attempts_made,
                "max_attempts": retrier.policy.max_attempts,
                "reason": last_reason,
                "status_code": last_status,
                "delay_s": retrier.last_delay_s,
                "elapsed_s": retrier.elapsed_s,
                "stop_reason": retrier.stop_reason,
                "start": start,
            },
        )
        attempts_made = retrier.attempts_made
        intento_word = "intento" if attempts_made == 1 else "intentos"
        stop_reason_text = _STOP_REASON_TEXT[retrier.stop_reason]
        page_context = f" (start={start})" if start is not None else ""
        raise ArxivUnavailable(
            f"{last_message}, tras {attempts_made} {intento_word}{page_context} en "
            f"{retrier.elapsed_s:.1f} s: {stop_reason_text}"
        ) from last_exc
