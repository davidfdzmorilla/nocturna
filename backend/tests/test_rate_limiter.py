"""Tests de `infrastructure/arxiv/rate_limit.py::RateLimiter`.

`sleep` y `monotonic` se inyectan como dobles de prueba: la suite no puede
tardar los 3 s reales que exige la política de cortesía de arXiv. Todas las
aserciones son sobre las esperas *registradas*, nunca sobre el reloj real.
Al final del fichero se comprueba que, en conjunto, corrió en milisegundos.
"""

import time

import pytest

from nocturna.infrastructure.arxiv.rate_limit import RateLimiter


@pytest.fixture(scope="module", autouse=True)
def _module_started_at() -> float:
    # Autouse y de módulo: se calcula una sola vez, en el setup del primer
    # test que corre en este fichero. Medir desde la importación del
    # módulo (en tiempo de recolección) mediría también lo que tardan
    # otros ficheros de la suite, no el espaciado real de este.
    return time.perf_counter()


class _FakeClock:
    """`monotonic()` devuelve los valores de `readings`, en orden, uno por
    llamada. `RateLimiter.acquire()` puede llamarlo hasta dos veces por
    invocación (antes y después de dormir), así que cada test declara
    tantas lecturas como necesite."""

    def __init__(self, *readings: float) -> None:
        self._readings = list(readings)

    def __call__(self) -> float:
        return self._readings.pop(0)


class _FakeSleep:
    """Registra cada espera pedida sin dormir de verdad."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


@pytest.mark.anyio
async def test_la_primera_llamada_no_espera():
    sleep = _FakeSleep()
    monotonic = _FakeClock(100.0)
    limiter = RateLimiter(3.0, sleep=sleep, monotonic=monotonic)

    await limiter.acquire()

    assert sleep.calls == []


@pytest.mark.anyio
async def test_la_segunda_llamada_espera_exactamente_lo_que_falta():
    sleep = _FakeSleep()
    # Primera acquire(): monotonic() -> 100.0 (sin espera).
    # Segunda acquire(): monotonic() -> 100.5 (han pasado 0.5 s, faltan 2.5
    # s), espera, y tras dormir vuelve a leer monotonic() -> 103.0.
    monotonic = _FakeClock(100.0, 100.5, 103.0)
    limiter = RateLimiter(3.0, sleep=sleep, monotonic=monotonic)

    await limiter.acquire()
    await limiter.acquire()

    assert sleep.calls == [2.5]


@pytest.mark.anyio
async def test_espera_exactamente_3_segundos_cuando_no_ha_pasado_nada_de_tiempo():
    sleep = _FakeSleep()
    monotonic = _FakeClock(200.0, 200.0, 203.0)
    limiter = RateLimiter(3.0, sleep=sleep, monotonic=monotonic)

    await limiter.acquire()
    await limiter.acquire()

    assert sleep.calls == [3.0]


@pytest.mark.anyio
async def test_no_espera_si_ya_ha_pasado_mas_del_intervalo_minimo():
    sleep = _FakeSleep()
    monotonic = _FakeClock(300.0, 305.0)
    limiter = RateLimiter(3.0, sleep=sleep, monotonic=monotonic)

    await limiter.acquire()
    await limiter.acquire()

    assert sleep.calls == []


def test_el_fichero_entero_corre_en_milisegundos(_module_started_at: float):
    # Última en orden de definición: pytest ejecuta los tests de un módulo
    # en el orden en que aparecen, así que para cuando se llega aquí ya
    # corrieron todos los `acquire()` de este fichero. Si alguno hubiera
    # dormido de verdad (3 s reales x varias llamadas), esto lo delataría.
    elapsed = time.perf_counter() - _module_started_at
    assert elapsed < 1.0, f"el fichero tardó {elapsed:.3f}s; alguna espera no se ha simulado"
