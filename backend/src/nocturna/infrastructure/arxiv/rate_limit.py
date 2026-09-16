"""Espaciado mínimo entre peticiones a la API de arXiv.

arXiv exige al menos 3 s entre peticiones consecutivas (ver
`client.py::MIN_REQUEST_INTERVAL_S`, la política vive ahí porque es una
constante del cliente, no un parámetro de este limitador genérico). El
reloj (`monotonic`) y la función de espera (`sleep`) son inyectables para
que los tests verifiquen el espaciado sin dormir de verdad.
"""

import time
from collections.abc import Awaitable, Callable

import anyio


class RateLimiter:
    """Garantiza que dos llamadas a `acquire()` consecutivas queden separadas
    por al menos `min_interval_s`, esperando solo si hace falta."""

    def __init__(
        self,
        min_interval_s: float,
        *,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval_s = min_interval_s
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_request_at: float | None = None

    async def acquire(self) -> None:
        """Espera lo que falte, si falta algo, para respetar `min_interval_s`."""
        now = self._monotonic()
        if self._last_request_at is not None:
            remaining = self._min_interval_s - (now - self._last_request_at)
            if remaining > 0:
                await self._sleep(remaining)
                now = self._monotonic()
        self._last_request_at = now
