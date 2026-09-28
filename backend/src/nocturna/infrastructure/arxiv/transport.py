"""Petición HTTP a arXiv con reintentos, compartida por las dos vías de ingesta.

Vive aparte de `client.py` desde T60.c: hay dos vías de ingesta (la API de
`/api/query` y el OAI-PMH de `oaipmh.arxiv.org`) y las dos necesitan la
misma clasificación de "qué código es transitorio", el mismo espaciado de
cortesía y el mismo mensaje de agotamiento. Duplicarlo daría dos tablas de
clasificación que pueden divergir sin que nadie lo note: el día que se
añada un código reintentable, o se arregle un mensaje, habría que acordarse
de tocarlo en los dos sitios.

La política de reintento (cuántos intentos, cuánto esperar, cuándo cortar)
sigue viviendo en `retry.py`, que no conoce HTTP. Aquí vive solo la
clasificación y el manejo de la respuesta.
"""

import logging
from collections.abc import Mapping

import anyio
import httpx

from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.arxiv.retry import Retrier, RetryPolicy

# Política de cortesía de arXiv (https://info.arxiv.org/help/api/tou.html):
# al menos 3 s entre peticiones. Constante de módulo, no clave de
# configuración: no es un parámetro que se deba poder relajar por error.
MIN_REQUEST_INTERVAL_S = 3.0
# Neutro a propósito: no expone correo ni URL del repositorio.
USER_AGENT = "nocturna/0.1.0"

# Cota de cortesía frente a una respuesta desproporcionada antes de parsear:
# entrada externa y `xml.etree` es susceptible a expansión de entidades.
MAX_RESPONSE_BYTES = 10 * 1024 * 1024

# Cota de duración TOTAL de una petición. No la da `httpx.Timeout`, que acota
# cada operación por separado (connect, read, write, pool): su timeout de
# lectura es el máximo entre dos trozos de datos, así que una respuesta que
# gotea un chunk cada 29 s mantiene un solo `get` vivo indefinidamente. Sin
# esta cota, el único freno de la ingesta era el vigía de `hard_stop`: la
# noche no se pasaba de las 04:45, pero se perdía entera y el autor no lo
# sabía hasta el informe de la mañana.
REQUEST_DEADLINE_S = 30.0

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
    """arXiv no respondió, respondió con un código distinto de 200 que agotó
    los reintentos (si era reintentable) o que se rechazó sin reintentar, o
    la respuesta supera el tope de tamaño admitido."""


def is_retryable_status(status_code: int) -> bool:
    """406 (el caso observado el 2026-09-21, ver docstring de `client.py`),
    429 y cualquier 5xx son transitorios; el resto de códigos que no son 200
    (400, 404, 414... y también 1xx, 2xx distinto de 200 y 3xx, ver
    `status_error_message`) no lo son: una consulta mal formada, o una
    respuesta que no es el 200 esperado, no se arregla esperando."""
    return status_code in (406, 429) or status_code >= 500


def status_error_message(status_code: int, *, service: str) -> str:
    # Se distingue 4xx de 5xx en el mensaje porque no significan lo mismo
    # para quien lea el log a las 3 de la mañana: un 5xx es un problema del
    # lado de arXiv (nada que ajustar aquí); un 4xx (406, 429...) sugiere
    # algo sobre *nuestra* petición -- cabecera, límite de cortesía, cambio
    # de política -- aunque, como el 406 observado, también puede ser
    # transitorio del lado de arXiv. El resto (1xx, 2xx distinto de 200,
    # 3xx -- httpx no sigue redirecciones por defecto, y el mismo CDN que
    # dio el 406 puede dar un 301) también se nombra explícitamente: sin
    # este caso, caía en el parser y producía el diagnóstico engañoso
    # ("XML inválido") que esta clasificación existe para evitar.
    if status_code >= 500:
        return f"{service} respondió {status_code} (error de servidor)"
    if status_code >= 400:
        return f"{service} respondió {status_code} (rechazo de la petición)"
    return f"{service} respondió {status_code} (código inesperado, no es 200)"


async def fetch_with_retry(
    *,
    http: httpx.AsyncClient,
    url: str,
    params: Mapping[str, str | int],
    limiter: RateLimiter,
    retry_policy: RetryPolicy,
    logger: logging.Logger,
    service: str,
    context: str | int | None = None,
    context_key: str = "start",
) -> bytes:
    """Ejecuta la petición dentro de la secuencia de un `Retrier` nuevo,
    reintentando SIEMPRE lo mismo (mismos `params`: nada avanza entre
    reintentos).

    Se construye un `Retrier` por llamada en vez de compartir uno de
    instancia: un mismo cliente puede recibir llamadas concurrentes y un
    `Retrier` compartido mezclaría los contadores de intentos de dos
    peticiones simultáneas.

    `limiter.acquire()` se adquiere dentro de cada intento, no una sola vez
    fuera del bucle: así cada reintento hereda también el espaciado de
    cortesía de 3 s.

    `context` identifica en logs y mensajes *qué* petición falló, y
    `context_key` cómo se llama eso en la vía que llama: `start` para la
    página de la API, `set` para el set de OAI-PMH. El nombre lo aporta el
    llamador porque el log de la noche se lee a mano cada mañana
    (`docs/CALIBRACION.md`) y un campo `start` con un nombre de set dentro
    es peor que no tenerlo.

    Cualquier código distinto de 200 se trata como `ArxivUnavailable`
    *antes* de intentar parsear: un cuerpo que no es una respuesta 200 no es
    XML válido del feed, y dejarlo caer hasta el parser produce un
    diagnóstico engañoso ("XML inválido") que oculta la causa real
    (observado en producción: un 406 se registró como "no element found",
    cuando el problema era el propio código de estado).
    """
    retrier = Retrier(retry_policy)
    last_message = ""
    last_reason: str | None = None
    last_status: int | None = None
    last_exc: Exception | None = None

    async for attempt in retrier.attempts():
        if attempt > 1:
            logger.warning(
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

        await limiter.acquire()
        try:
            # `fail_after` es cancelación cooperativa de anyio, así que sigue
            # siendo interrumpible por el vigía de `hard_stop`: no compite con
            # él, lo complementa acotando una petición concreta.
            with anyio.fail_after(REQUEST_DEADLINE_S):
                response = await http.get(
                    url, params=dict(params), headers={"User-Agent": USER_AGENT}
                )
        except TimeoutError as exc:
            # Se agotó la cota total de la petición: transitorio, y del mismo
            # carácter que un timeout de httpx, así que se reintenta igual.
            last_message = f"{service} no completó la respuesta en {REQUEST_DEADLINE_S:.0f} s"
            last_reason = "request_deadline"
            last_status = None
            last_exc = exc
            continue
        except httpx.TransportError as exc:
            # transitorio, reintentable.
            last_message = f"fallo consultando {service}: {exc}"
            last_reason = "transport_error"
            last_status = None
            last_exc = exc
            continue
        except httpx.HTTPError as exc:
            # httpx.HTTPError no-transporte (por ejemplo, una URL
            # inválida): no es transitorio, no se reintenta.
            raise ArxivUnavailable(f"fallo consultando {service}: {exc}") from exc

        if is_retryable_status(response.status_code):
            last_message = status_error_message(response.status_code, service=service)
            last_reason = f"http_{response.status_code}"
            last_status = response.status_code
            last_exc = ArxivUnavailable(last_message)
            continue

        if response.status_code != 200:
            raise ArxivUnavailable(status_error_message(response.status_code, service=service))

        content = response.content
        if len(content) > MAX_RESPONSE_BYTES:
            raise ArxivUnavailable(
                f"respuesta de arXiv de {len(content)} bytes supera el tope de {MAX_RESPONSE_BYTES}"
            )

        if attempt > 1:
            logger.info(
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
    # `max_elapsed_s`): todos los intentos fallaron de forma reintentable.
    logger.error(
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
            context_key: context,
        },
    )
    attempts_made = retrier.attempts_made
    intento_word = "intento" if attempts_made == 1 else "intentos"
    stop_reason_text = _STOP_REASON_TEXT[retrier.stop_reason]
    page_context = f" ({context_key}={context})" if context is not None else ""
    raise ArxivUnavailable(
        f"{last_message}, tras {attempts_made} {intento_word}{page_context} en "
        f"{retrier.elapsed_s:.1f} s: {stop_reason_text}"
    ) from last_exc
