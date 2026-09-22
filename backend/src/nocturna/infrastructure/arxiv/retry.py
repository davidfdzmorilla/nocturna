"""Política de reintento genérica y su ejecución.

Motivado por el 406 observado el 2026-09-21 al consultar la API de arXiv:
cuerpo vacío, sin `Retry-After`, cabeceras de Fastly/Varnish -- lo rechaza el
CDN delante de arXiv, no la aplicación, y es transitorio (la misma consulta
devolvió 200 minutos después). `Retrier` no conoce HTTP ni arXiv: solo
cuenta intentos, tiempo transcurrido y esperas entre intentos. La
clasificación de qué es reintentable vive en `infrastructure/arxiv/client.py`,
que es quien sabe qué significa un 406 o un `TransportError` de arXiv.

**Lo que `max_elapsed_s` NO acota** (revisión posterior a la primera
versión de este módulo, que sí lo daba a entender): acota cuándo puede
INICIARSE un nuevo intento -- la comprobación es anterior a dormir, ver
`Retrier.attempts` -- no la duración total de la secuencia. El intento que
sí se llega a iniciar todavía tiene por delante `limiter.acquire()` y la
petición HTTP en curso, que puede tardar hasta el timeout del cliente; el
techo real de una página queda por encima de `max_elapsed_s` en esa
cantidad, y `Retrier` no puede saber cuánto porque no conoce HTTP. Por el
mismo motivo, `max_attempts` es un TOPE, no una garantía: si `max_elapsed_s`
corta antes (fallos lentos que consumen el presupuesto de tiempo más rápido
de lo que consumen intentos), se sirven menos intentos de los que
`max_attempts` permitiría. Es el comportamiento deliberado, no un defecto:
en una noche con corte duro, que el tiempo gane a los intentos es la
prioridad correcta.

Reloj (`monotonic`), espera (`sleep`) y aleatoriedad del jitter (`jitter`)
son inyectables, mismo patrón que `rate_limit.py::RateLimiter`, para que los
tests verifiquen el backoff sin dormir de verdad ni depender de
`random.uniform`. La espera es siempre `anyio.sleep`, nunca `time.sleep`: la
ingesta corre dentro de la tarea que el vigía cancela en `hard_stop` (ADR
0005), y una espera síncrona no sería cancelable.
"""

import random
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

import anyio

# Cota superior del jitter multiplicativo de `_default_jitter`
# (`uniform(0.5, MAX_JITTER_FACTOR)`): el peor caso de cada espera es
# `base_delay_s * 2**n * MAX_JITTER_FACTOR`. Pública (no `_MAX_...`) porque
# `infrastructure/config.py::ArxivConfig` la importa para validar
# `retry_max_elapsed_s` contra el peor caso real, no contra el backoff
# nominal sin jitter -- ver el validador allí.
MAX_JITTER_FACTOR = 1.5


def _default_jitter(delay_s: float) -> float:
    """`jitter(d) = d * uniform(0.5, MAX_JITTER_FACTOR)`: +/-50% sobre el
    backoff nominal, para que reintentos de varias ingestas concurrentes (o
    de una misma secuencia) no se agrupen en el mismo instante."""
    return delay_s * random.uniform(0.5, MAX_JITTER_FACTOR)


@dataclass(frozen=True)
class RetryPolicy:
    """Parámetros de una secuencia de reintentos.

    `max_attempts` incluye el primer intento (1 = sin reintentos) y es un
    TOPE, no una garantía: ver el docstring del módulo -- `max_elapsed_s`
    puede cortar la secuencia antes de alcanzarlo.

    `base_delay_s` es la espera antes del 2.º intento; se duplica en cada
    intento sucesivo (backoff exponencial, antes de aplicar jitter).

    `max_elapsed_s` acota CUÁNDO PUEDE INICIARSE un nuevo intento (la
    comprobación es anterior a dormir, ver `Retrier.attempts`), no cuánto
    dura la secuencia completa: ver el docstring del módulo para el porqué.
    """

    max_attempts: int
    base_delay_s: float
    max_elapsed_s: float


class Retrier:
    """Itera los intentos de una secuencia de reintentos, durmiendo entre
    ellos según `policy` (backoff exponencial con jitter) y cortando la
    secuencia si se agotan los intentos o el tiempo disponible.

    Uso: `async for attempt in retrier.attempts(): ...`, con un `return`
    dentro del cuerpo cuando un intento tiene éxito y un simple paso a la
    siguiente iteración (sin `break`) cuando falla de forma reintentable.
    Si el `async for` termina sin que el cuerpo haya devuelto nada, la
    secuencia se agotó: `stop_reason` dice por qué.
    """

    def __init__(
        self,
        policy: RetryPolicy,
        *,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        jitter: Callable[[float], float] = _default_jitter,
    ) -> None:
        self.policy = policy
        self._sleep = sleep
        self._monotonic = monotonic
        self._jitter = jitter

        #: Número de intentos efectivamente comenzados (yielded) hasta ahora.
        self.attempts_made: int = 0
        #: Segundos transcurridos desde el primer intento, medidos en los
        #: puntos de control de esta clase (antes de cada intento y al
        #: agotar la secuencia) -- no incluye la duración del intento en
        #: curso mientras este no ha terminado, ver docstring de la clase.
        self.elapsed_s: float = 0.0
        #: Por qué se agotó la secuencia, o `None` mientras no lo ha hecho
        #: (incluido el caso de éxito, que nunca fija esta propiedad).
        self.stop_reason: Literal["max_attempts", "max_elapsed"] | None = None
        #: Espera aplicada (con jitter) antes del intento que se acaba de
        #: producir, o la que habría hecho falta antes del intento cortado
        #: por `max_elapsed_s`. `None` antes del primer intento y tras un
        #: agotamiento por `max_attempts` (no hay "siguiente espera" que
        #: reportar).
        self.last_delay_s: float | None = None

    async def attempts(self) -> AsyncIterator[int]:
        start = self._monotonic()
        self.attempts_made = 0
        self.elapsed_s = 0.0
        self.stop_reason = None
        self.last_delay_s = None

        for attempt in range(1, self.policy.max_attempts + 1):
            if attempt > 1:
                delay = self._jitter(self.policy.base_delay_s * 2 ** (attempt - 2))
                elapsed_before_sleep = self._monotonic() - start
                self.last_delay_s = delay
                # El tope se comprueba ANTES de dormir: si la siguiente
                # espera no cabe en max_elapsed_s, se corta sin dormir.
                if elapsed_before_sleep + delay > self.policy.max_elapsed_s:
                    self.elapsed_s = elapsed_before_sleep
                    self.stop_reason = "max_elapsed"
                    return
                await self._sleep(delay)

            self.attempts_made = attempt
            self.elapsed_s = self._monotonic() - start
            yield attempt

        self.last_delay_s = None
        self.elapsed_s = self._monotonic() - start
        self.stop_reason = "max_attempts"
