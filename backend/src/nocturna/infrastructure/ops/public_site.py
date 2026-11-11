"""Comprobación de la web pública por HTTP (T110.a, ADR 0044).

Una sola petición, sin redirecciones, con la verificación TLS por defecto de
httpx. De un fallo solo se conserva el nombre de la clase de la excepción: el
mensaje puede contener la URL. Cumple `application.use_cases.phase3.PublicSite`.

Tope total: el timeout de httpx acota cada fase, no el conjunto (el de lectura se
reinicia con cada fragmento). Por eso el cuerpo se lee en streaming con un tope de
reloj (`time.monotonic`, comprobado entre fragmentos) y un corte de tamaño. La
resolución DNS no se acota aquí: la cubre el vigilante de los envoltorios.

Semántica de `SiteResponse.tls_ok` (línea `tls=ok|fail`): `ok` solo si hubo
respuesta por https, es decir, el handshake verificado pasó. Sin respuesta, TLS no
se pudo comprobar y se anota `fail`; lo que distingue la causa es `error_class`:
`SSLCertVerificationError` (u otra subclase de `ssl.SSLError`) si falló TLS,
`ProbeTimeout` si venció el tope total, `ProbeBodyTooLarge` si el cuerpo pasó el
corte, y el nombre de la clase de httpx en el resto (`ConnectError` por DNS o
conexión, `ReadTimeout`...).
"""

from __future__ import annotations

import ssl
import time

import httpx

from nocturna.application.use_cases.phase3 import SiteResponse

_TIMEOUT_S = 30.0
_PHASE_TIMEOUT_S = 10.0
_MAX_BYTES = 2_000_000

TIMEOUT_ERROR = "ProbeTimeout"
TOO_LARGE_ERROR = "ProbeBodyTooLarge"


def _tls_cause(exc: BaseException) -> ssl.SSLError | None:
    """El `ssl.SSLError` de la cadena de causas de `exc`, si lo hay."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        if isinstance(current, ssl.SSLError):
            return current
        seen.add(id(current))
        current = current.__cause__ or current.__context__
    return None


def _failure(exc: BaseException) -> SiteResponse:
    cause = _tls_cause(exc)
    name = type(cause).__name__ if cause is not None else type(exc).__name__
    return SiteResponse(None, False, name, None)


class HttpxPublicSite:
    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        total_timeout_s: float = _TIMEOUT_S,
        max_bytes: int = _MAX_BYTES,
    ) -> None:
        self._transport = transport
        self._total_timeout_s = total_timeout_s
        self._max_bytes = max_bytes

    def fetch(self, url: str) -> SiteResponse:
        deadline = time.monotonic() + self._total_timeout_s
        try:
            with httpx.Client(
                timeout=min(_PHASE_TIMEOUT_S, self._total_timeout_s),
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                with client.stream("GET", url) as response:
                    # TLS verificado solo si el esquema es https y hubo respuesta.
                    tls_ok = response.url.scheme == "https"
                    robots = ", ".join(response.headers.get_list("x-robots-tag")) or None
                    chunks: list[bytes] = []
                    size = 0
                    for chunk in response.iter_bytes():
                        if time.monotonic() > deadline:
                            return SiteResponse(None, False, TIMEOUT_ERROR, None)
                        size += len(chunk)
                        if size > self._max_bytes:
                            return SiteResponse(
                                response.status_code, tls_ok, TOO_LARGE_ERROR, None, robots
                            )
                        chunks.append(chunk)
                    if time.monotonic() > deadline:
                        return SiteResponse(None, False, TIMEOUT_ERROR, None)
                    encoding = response.encoding or "utf-8"
                    status = response.status_code
        except httpx.HTTPError as exc:
            return _failure(exc)
        except (ValueError, OSError) as exc:  # URL inválida, fallo de sistema
            return _failure(exc)
        html = b"".join(chunks).decode(encoding, errors="replace")
        return SiteResponse(status, tls_ok, None, html, robots)
