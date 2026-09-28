"""Cliente OAI-PMH de arXiv: la vía de ingesta por defecto desde T60.c.

Cumple `domain.sources.ArxivSource` estructuralmente, igual que
`ArxivClient`: es infraestructura quien satisface el contrato del dominio.

Por qué existe: entre el 2026-09-21 y el 2026-09-25 la API de `/api/query`
devolvió 406 de forma sistemática (cuerpo vacío, sin `Retry-After`,
cabeceras de Fastly/Varnish: lo rechaza el CDN, no la aplicación), y los
reintentos de T60.b no bastaron porque los episodios duraban más que la
ventana de reintento. OAI-PMH es el endpoint que arXiv documenta para
cosecha programada como la nuestra, vive en otro host
(`oaipmh.arxiv.org`, migrado en marzo de 2025) y respondía 200 en el mismo
rato en que la API rechazaba. No es una forma de esquivar el CDN: es la
puerta que arXiv señaliza para este uso. Ver ADR 0010.

Lo que NO se hace aquí, y es deliberado: no se imita la huella TLS de
`curl`, no se falsean cabeceras de navegador, no se rota el `User-Agent` y
no se cambia de biblioteca HTTP "porque otra pasa". Si esta vía también
fallara, la respuesta correcta es aceptar menos material, no buscar otra
llave para la misma puerta.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, time, timedelta

import httpx

from nocturna.domain.entities import Item
from nocturna.domain.sources import SourceFetch
from nocturna.infrastructure.arxiv import transport
from nocturna.infrastructure.arxiv.mappers import entry_to_item
from nocturna.infrastructure.arxiv.oai import parse_oai_response
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.arxiv.retry import RetryPolicy

_logger = logging.getLogger(__name__)

OAI_BASE_URL = "https://oaipmh.arxiv.org/oai"

# Desfase máximo entre el `datestamp` de anuncio de un lote y la fecha de
# envío de lo que contiene. arXiv anuncia a las 00:00 UTC los envíos hasta
# las 18:00 UTC del día anterior, así que un lote con `datestamp = D` puede
# traer envíos de `D-2 18:00`. Dos días cubre ese intervalo con margen; es
# el suelo del filtro fino, no un solape de cortesía.
_DATESTAMP_LAG_DAYS = 2
_SERVICE = "el OAI-PMH de arXiv"

# Los sets de OAI-PMH son jerárquicos: `grupo:archivo:CATEGORÍA`. El prefijo
# de grupo NO se deriva por algoritmo a propósito: `astro-ph.EP` es
# `physics:astro-ph:EP`, pero `math.AG` es `math:math:AG` y `gr-qc` es
# `physics:gr-qc` (sin tercer nivel). Adivinarlo se colaría en silencio y
# cosecharíamos el set equivocado -- o ninguno -- sin más síntoma que una
# noche con pocos ítems. El mapa cubre solo lo que fase 1 usa; una
# categoría desconocida falla ruidosamente.
_ARCHIVE_TO_GROUP = {
    "astro-ph": "physics",
}


class UnknownArxivSet(Exception):
    """La categoría configurada no tiene una traducción conocida a set de
    OAI-PMH.

    `ArxivConfig` la valida al cargar la configuración cuando
    `ingest_via = "oai"`, así que una errata en `categories` falla de día. Si
    aun así llegara aquí, se lanza antes de hacer una sola petición.
    """


def category_to_set(category: str) -> str:
    """`astro-ph.EP` -> `physics:astro-ph:EP`; `gr-qc` -> `physics:gr-qc`."""
    archive, _, subcategory = category.partition(".")
    group = _ARCHIVE_TO_GROUP.get(archive)
    if group is None:
        raise UnknownArxivSet(
            f"categoría {category!r} sin traducción conocida a set de OAI-PMH; "
            f"añádela a _ARCHIVE_TO_GROUP en oai_client.py tras comprobarla con ListSets"
        )
    return f"{group}:{archive}:{subcategory}" if subcategory else f"{group}:{archive}"


class ArxivOaiClient:
    """Cosecha novedades de arXiv por OAI-PMH, un `set` por categoría."""

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        limiter: RateLimiter,
        now: Callable[[], datetime],
        retry_policy: RetryPolicy,
        base_url: str = OAI_BASE_URL,
        metadata_prefix: str = "arXivRaw",
        lookback_days: int = 2,
        max_requests_per_fetch: int = 6,
    ) -> None:
        self._http = http
        self._limiter = limiter
        self._now = now
        self._retry_policy = retry_policy
        self._base_url = base_url
        self._metadata_prefix = metadata_prefix
        self._lookback_days = lookback_days
        self._max_requests_per_fetch = max_requests_per_fetch

    async def fetch_new(
        self, *, since: datetime, categories: Sequence[str], max_results: int
    ) -> SourceFetch:
        """Ítems nuevos de `categories` publicados desde `since`, hasta `max_results`.

        La ventana `from`/`until` se pide sobre el `datestamp` de ANUNCIO, con
        granularidad de día: OAI-PMH de arXiv no admite cosecha selectiva por
        fecha de envío. `since` gobierna su extremo inferior, así que un
        `--since` antiguo (un relleno) ensancha la ventana en vez de quedarse
        sin lotes que filtrar.

        El suelo del filtro fino NO es `since`, y esto es lo más delicado de
        este método. Un lote con `datestamp = D` contiene envíos de
        `(D-2 18:00 UTC, D-1 18:00 UTC]`: arXiv anuncia a las 00:00 UTC lo
        que se envió hasta las 18:00 UTC del día anterior. Usar `since` (que
        con el valor por defecto es *ayer a medianoche*) como suelo cortaba
        ese intervalo por la mitad y **descartaba la franja de envíos de la
        tarde anterior, que es la más poblada del día**: medido sobre una
        cosecha real, 8 de 18 novedades, un 44 %. Y se perdían para siempre,
        porque la noche anterior no pidió ese `datestamp` y la siguiente lo
        pide con el `since` ya por delante.

        Así que el suelo baja `_DATESTAMP_LAG_DAYS` días respecto a `from_`:
        acepta todo lo que los lotes de la ventana traen legítimamente como
        novedad, y sigue descartando las reediciones de papers viejos que la
        cosecha arrastra (el `datestamp` es de modificación, no de envío).
        Los papers que se repiten entre noches por el solape los absorbe
        `add_many` con `ON CONFLICT DO NOTHING`, que es gratis en base de
        datos; lo que NO es gratis es perderlos.

        Un mismo paper puede aparecer en los sets de dos categorías
        (cross-list): se deduplica aquí, antes de devolver, para no inflar
        los contadores de `IngestResult` y ensuciar la tabla de calibración.

        `filtered_out` cuenta los descartes por el suelo, y se registra en el
        log: sin ese contador, un suelo mal calculado descarta media cosecha
        cada noche sin dejar rastro, que es exactamente cómo se colaba el
        defecto de arriba.
        """
        sets = [category_to_set(category) for category in categories]
        fetched_at = self._now()
        until = fetched_at.date()
        window_start = until - timedelta(days=max(self._lookback_days - 1, 0))
        # `since` manda si pide más atrás que la ventana nominal: así un
        # relleno con `--since` funciona sin tocar la configuración.
        from_ = min(since.date(), window_start)
        floor = min(
            since,
            datetime.combine(from_ - timedelta(days=_DATESTAMP_LAG_DAYS), time.min, tzinfo=UTC),
        )

        items: list[Item] = []
        seen: set[str] = set()
        skipped = 0
        filtered_out = 0
        truncated = False
        requests_made = 0

        for set_spec in sets:
            token: str | None = None
            while True:
                if requests_made >= self._max_requests_per_fetch:
                    # Techo duro: una cadena de `resumptionToken` inesperada
                    # no puede encadenar peticiones sin límite a las 00:05.
                    truncated = True
                    break

                params: dict[str, str | int] = (
                    {"verb": "ListRecords", "resumptionToken": token}
                    if token
                    else {
                        "verb": "ListRecords",
                        "metadataPrefix": self._metadata_prefix,
                        "set": set_spec,
                        "from": from_.isoformat(),
                        "until": until.isoformat(),
                    }
                )

                # Se cobra la cuota ANTES de pedir: el techo acota
                # peticiones INTENTADAS, no servidas. Contarlas después
                # dejaría que cada página que agota reintentos consumiera
                # ~93 s sin gastar cuota, y el peor caso real superaría el
                # documentado.
                requests_made += 1
                payload = await transport.fetch_with_retry(
                    http=self._http,
                    url=self._base_url,
                    params=params,
                    limiter=self._limiter,
                    retry_policy=self._retry_policy,
                    logger=_logger,
                    service=_SERVICE,
                    context=token or set_spec,
                    context_key="token" if token else "set",
                )
                page = parse_oai_response(payload)
                skipped += page.skipped

                for entry in page.entries:
                    if entry.published_at < floor:
                        filtered_out += 1
                        continue
                    if entry.arxiv_id in seen:
                        continue
                    if len(items) >= max_results:
                        truncated = True
                        break
                    seen.add(entry.arxiv_id)
                    items.append(entry_to_item(entry, fetched_at=fetched_at))

                token = page.resumption_token
                if truncated or token is None:
                    break

            if truncated:
                break

        _logger.info(
            "arxiv.oai_harvest",
            extra={
                "event": "arxiv.oai_harvest",
                "sets": sets,
                "from": from_.isoformat(),
                "until": until.isoformat(),
                "floor": floor.isoformat(),
                "requests": requests_made,
                "items": len(items),
                "filtered_out": filtered_out,
                "skipped": skipped,
                "truncated": truncated,
            },
        )
        return SourceFetch(items=items, truncated=truncated, skipped=skipped)
