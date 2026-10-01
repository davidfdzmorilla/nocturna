"""Cliente HTTP del NASA Exoplanet Archive (TAP sync y servicio de alias).

Fuente consumida solo por código Python, sin servidor MCP (ADR 0012). Sigue
el patrón de `infrastructure/arxiv/transport.py`: espaciado de cortesía con
el `RateLimiter` de arXiv (importado, no movido), cota de duración TOTAL con
`anyio.fail_after` (el `Timeout` de httpx acota cada operación por separado)
y tope de tamaño de respuesta. Sin reintentos: un fallo se propaga y quien
llama decide; el techo de peticiones se comprueba y se cuenta ANTES de
enviar, así que la petición `max_requests + 1` nunca sale.
"""

import csv
import io
import json
import logging
from collections.abc import Mapping

import anyio
import httpx

from nocturna.infrastructure.arxiv.rate_limit import RateLimiter

# Neutro a propósito, igual que el de arXiv: no expone correo ni URL.
USER_AGENT = "nocturna/0.1.0"
MAX_RESPONSE_BYTES = 20 * 1024 * 1024

_logger = logging.getLogger(__name__)


class ExoplanetArchiveUnavailable(Exception):
    """El archivo no respondió, respondió algo distinto de una respuesta 200
    válida o la respuesta no se pudo interpretar."""


class ArchiveRequestCapReached(ExoplanetArchiveUnavailable):
    """Se alcanzó `max_requests_per_night`; la petición no se envió."""


def adql_string(value: str) -> str:
    """Literal de cadena ADQL: comillas simples duplicadas."""
    return "'" + value.replace("'", "''") + "'"


def _looks_like_votable_error(text: str) -> bool:
    # El TAP responde HTTP 200 con un VOTABLE de error aunque se pida CSV
    # (fixture `pscomppars_error_gaia_id.xml`). Solo se mira el principio: un
    # CSV legítimo puede contener "VOTABLE" en una celda.
    return text.lstrip().startswith(("<?xml", "<VOTABLE"))


class ArchiveHttpClient:
    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        tap_url: str,
        alias_url: str,
        limiter: RateLimiter,
        request_timeout_s: float,
        max_requests: int,
        max_response_bytes: int | None = None,
    ) -> None:
        self._http = http
        self._tap_url = tap_url
        self._alias_url = alias_url
        self._limiter = limiter
        self._request_timeout_s = request_timeout_s
        self._max_requests = max_requests
        self._max_response_bytes = max_response_bytes
        self._requests_made = 0

    @property
    def requests_made(self) -> int:
        return self._requests_made

    async def _get(self, url: str, params: Mapping[str, str]) -> bytes:
        if self._requests_made >= self._max_requests:
            raise ArchiveRequestCapReached(
                f"se alcanzó el límite de {self._max_requests} peticiones al "
                "Exoplanet Archive en esta ejecución"
            )
        self._requests_made += 1
        await self._limiter.acquire()
        try:
            with anyio.fail_after(self._request_timeout_s):
                response = await self._http.get(
                    url, params=dict(params), headers={"User-Agent": USER_AGENT}
                )
        except TimeoutError as exc:
            raise ExoplanetArchiveUnavailable(
                f"el Exoplanet Archive no completó la respuesta en {self._request_timeout_s:.0f} s"
            ) from exc
        except httpx.HTTPError as exc:
            raise ExoplanetArchiveUnavailable(
                f"fallo consultando el Exoplanet Archive: {exc}"
            ) from exc
        if response.status_code != 200:
            raise ExoplanetArchiveUnavailable(
                f"el Exoplanet Archive respondió {response.status_code} para {url}"
            )
        content = response.content
        limit = MAX_RESPONSE_BYTES if self._max_response_bytes is None else self._max_response_bytes
        if len(content) > limit:
            raise ExoplanetArchiveUnavailable(
                f"respuesta del Exoplanet Archive de {len(content)} bytes supera el tope de {limit}"
            )
        _logger.info(
            "exoplanet_archive.request",
            extra={
                "event": "exoplanet_archive.request",
                "requests_made": self._requests_made,
                "bytes": len(content),
            },
        )
        return content

    async def query_csv(self, adql: str) -> list[dict[str, str | None]]:
        content = await self._get(self._tap_url, {"query": adql, "format": "csv"})
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ExoplanetArchiveUnavailable(
                "respuesta del Exoplanet Archive no decodificable como UTF-8"
            ) from exc
        if _looks_like_votable_error(text):
            raise ExoplanetArchiveUnavailable(
                f"el Exoplanet Archive devolvió un error VOTABLE con HTTP 200: {text[:300]!r}"
            )
        reader = csv.DictReader(io.StringIO(text))
        return [
            {key: (value if value != "" else None) for key, value in row.items()} for row in reader
        ]

    async def lookup_alias(self, name: str) -> Mapping[str, object]:
        content = await self._get(self._alias_url, {"objname": name})
        try:
            payload = json.loads(content.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ExoplanetArchiveUnavailable(
                "respuesta de alias del Exoplanet Archive no es JSON válido"
            ) from exc
        if not isinstance(payload, dict):
            raise ExoplanetArchiveUnavailable(
                "respuesta de alias del Exoplanet Archive no es un objeto JSON"
            )
        return payload
