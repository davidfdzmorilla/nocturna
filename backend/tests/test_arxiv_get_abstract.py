"""Tests de `ArxivClient.get_abstract`.

Sirve para T44 (releer un ítem concreto) y para depurar `nocturna
run-item <id>`. `id_list` va sin versión: se quiere siempre la última.
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nocturna.infrastructure.arxiv.client import ArxivClient
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "arxiv"
_FETCHED_AT = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)


def _load(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


class _Recorder:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._response


class _IncreasingClock:
    def __init__(self) -> None:
        self._value = 0.0

    def __call__(self) -> float:
        value = self._value
        self._value += 1000.0
        return value


class _FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _build_client(recorder: _Recorder) -> tuple[ArxivClient, httpx.AsyncClient, _FakeSleep]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle), timeout=10.0)
    sleep = _FakeSleep()
    limiter = RateLimiter(3.0, sleep=sleep, monotonic=_IncreasingClock())
    client = ArxivClient(http, limiter=limiter, page_size=1, now=lambda: _FETCHED_AT)
    return client, http, sleep


@pytest.mark.anyio
async def test_consulta_con_id_list_sin_version():
    recorder = _Recorder(httpx.Response(200, content=_load("feed_single.xml")))
    client, http, _ = _build_client(recorder)

    async with http:
        await client.get_abstract("2301.00001")

    assert len(recorder.requests) == 1
    params = recorder.requests[0].url.params
    assert params["id_list"] == "2301.00001"
    assert "search_query" not in params


@pytest.mark.anyio
async def test_devuelve_la_entrada_del_feed_single():
    recorder = _Recorder(httpx.Response(200, content=_load("feed_single.xml")))
    client, http, _ = _build_client(recorder)

    async with http:
        entry = await client.get_abstract("2301.00001")

    assert entry is not None
    assert entry.arxiv_id == "2301.00001"
    assert entry.version == 1
    assert entry.title == "NFTrig"
    assert entry.categories == ("cs.HC",)


@pytest.mark.anyio
async def test_id_desconocido_devuelve_none_no_lanza_excepcion():
    # feed_error.xml es lo que arXiv devuelve (con HTTP 200) para un id
    # inválido/desconocido: no encontrarlo no es un fallo de la ingesta.
    recorder = _Recorder(httpx.Response(200, content=_load("feed_error.xml")))
    client, http, _ = _build_client(recorder)

    async with http:
        entry = await client.get_abstract("id_invalido_xyz")

    assert entry is None


@pytest.mark.anyio
async def test_get_abstract_tambien_pasa_por_el_limitador():
    recorder = _Recorder(httpx.Response(200, content=_load("feed_single.xml")))
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle), timeout=10.0)
    sleep = _FakeSleep()

    calls: list[None] = []

    class _CountingLimiter(RateLimiter):
        async def acquire(self) -> None:
            calls.append(None)
            await super().acquire()

    limiter = _CountingLimiter(3.0, sleep=sleep, monotonic=_IncreasingClock())
    client = ArxivClient(http, limiter=limiter, page_size=1, now=lambda: _FETCHED_AT)

    async with http:
        await client.get_abstract("2301.00001")

    assert len(calls) == 1
