"""Tests de `infrastructure/arxiv/retry.py::Retrier` (unitario, sin HTTP).

`sleep`, `monotonic` y `jitter` se inyectan como dobles de prueba: ningún
test de este fichero duerme de verdad ni depende de `random.uniform`. Mismo
patrón que `test_rate_limiter.py`/`test_arxiv_client.py`.
"""

import pytest

from nocturna.infrastructure.arxiv.retry import Retrier, RetryPolicy


class _FakeSleep:
    """Registra cada espera pedida, sin dormir de verdad."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


class _ConstantClock:
    """`monotonic()` siempre devuelve el mismo valor: el tiempo "real" no
    avanza durante la secuencia, así que `elapsed_before_sleep` es siempre
    0.0 y nunca interfiere con el corte por `max_elapsed_s` salvo que el
    propio test lo busque."""

    def __init__(self, value: float = 0.0) -> None:
        self._value = value

    def __call__(self) -> float:
        return self._value


class _SequenceClock:
    """`monotonic()` devuelve los valores de `readings`, en orden, uno por
    llamada."""

    def __init__(self, *readings: float) -> None:
        self._readings = list(readings)

    def __call__(self) -> float:
        return self._readings.pop(0)


def _identity_jitter(delay_s: float) -> float:
    return delay_s


class _SequenceJitter:
    """`jitter(delay)` multiplica `delay` por el siguiente factor de
    `factors`, en orden."""

    def __init__(self, *factors: float) -> None:
        self._factors = list(factors)

    def __call__(self, delay_s: float) -> float:
        return delay_s * self._factors.pop(0)


async def _exhaust(retrier: Retrier) -> None:
    """Consume la secuencia entera sin que ningún intento tenga éxito
    (nunca hace `return` dentro del `async for`): fuerza el agotamiento por
    `max_attempts` o por `max_elapsed_s`, lo que ocurra primero."""
    async for _attempt in retrier.attempts():
        continue


@pytest.mark.anyio
async def test_la_primera_tentativa_no_duerme():
    sleep = _FakeSleep()
    policy = RetryPolicy(max_attempts=3, base_delay_s=5.0, max_elapsed_s=1000.0)
    retrier = Retrier(policy, sleep=sleep, monotonic=_ConstantClock(), jitter=_identity_jitter)

    async for attempt in retrier.attempts():
        assert attempt == 1
        break

    assert sleep.calls == []


@pytest.mark.anyio
async def test_backoff_exponencial_desde_base_delay_s_con_jitter_identidad():
    sleep = _FakeSleep()
    policy = RetryPolicy(max_attempts=4, base_delay_s=5.0, max_elapsed_s=1000.0)
    retrier = Retrier(policy, sleep=sleep, monotonic=_ConstantClock(), jitter=_identity_jitter)

    await _exhaust(retrier)

    assert sleep.calls == [5.0, 10.0, 20.0]
    assert retrier.attempts_made == 4
    assert retrier.stop_reason == "max_attempts"


@pytest.mark.anyio
async def test_el_jitter_escala_cada_espera_sin_hacerla_negativa():
    sleep = _FakeSleep()
    policy = RetryPolicy(max_attempts=3, base_delay_s=4.0, max_elapsed_s=1000.0)
    # Nominal sin jitter: intento 2 -> 4.0, intento 3 -> 8.0. Con los
    # factores inyectados (0.5, luego 1.5): 4.0*0.5=2.0, 8.0*1.5=12.0.
    jitter = _SequenceJitter(0.5, 1.5)
    retrier = Retrier(policy, sleep=sleep, monotonic=_ConstantClock(), jitter=jitter)

    await _exhaust(retrier)

    assert sleep.calls == [2.0, 12.0]
    assert all(delay >= 0 for delay in sleep.calls)


@pytest.mark.anyio
async def test_corta_al_agotar_max_attempts():
    sleep = _FakeSleep()
    policy = RetryPolicy(max_attempts=3, base_delay_s=1.0, max_elapsed_s=1000.0)
    retrier = Retrier(policy, sleep=sleep, monotonic=_ConstantClock(), jitter=_identity_jitter)

    await _exhaust(retrier)

    assert retrier.attempts_made == 3
    assert retrier.stop_reason == "max_attempts"
    assert retrier.last_delay_s is None


@pytest.mark.anyio
async def test_corta_por_max_elapsed_s_antes_de_agotar_intentos():
    sleep = _FakeSleep()
    policy = RetryPolicy(max_attempts=10, base_delay_s=5.0, max_elapsed_s=1.0)
    # start=0.0 (1ª lectura). Tras el intento 1 (elapsed_s=0.0, 2ª lectura),
    # al entrar en el intento 2 se mide elapsed_before_sleep (3ª lectura):
    # un salto grande hace que 100.0 + 5.0 > max_elapsed_s (1.0), así que la
    # secuencia se corta antes del segundo intento.
    monotonic = _SequenceClock(0.0, 0.0, 100.0)
    retrier = Retrier(policy, sleep=sleep, monotonic=monotonic, jitter=_identity_jitter)

    await _exhaust(retrier)

    assert retrier.attempts_made == 1
    assert retrier.stop_reason == "max_elapsed"


@pytest.mark.anyio
async def test_no_duerme_si_la_espera_no_cabe_en_max_elapsed_s():
    sleep = _FakeSleep()
    policy = RetryPolicy(max_attempts=10, base_delay_s=5.0, max_elapsed_s=1.0)
    monotonic = _SequenceClock(0.0, 0.0, 100.0)
    retrier = Retrier(policy, sleep=sleep, monotonic=monotonic, jitter=_identity_jitter)

    await _exhaust(retrier)

    # La comprobación del tope es previa al `sleep`: la espera que no cupo
    # no debe aparecer entre las registradas.
    assert sleep.calls == []


@pytest.mark.anyio
async def test_max_attempts_1_equivale_a_no_reintentar():
    sleep = _FakeSleep()
    policy = RetryPolicy(max_attempts=1, base_delay_s=5.0, max_elapsed_s=1000.0)
    retrier = Retrier(policy, sleep=sleep, monotonic=_ConstantClock(), jitter=_identity_jitter)

    await _exhaust(retrier)

    assert retrier.attempts_made == 1
    assert sleep.calls == []
    assert retrier.stop_reason == "max_attempts"
