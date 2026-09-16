"""Tests de `infrastructure/arxiv/client.py::ArxivClient`.

El transporte HTTP real se sustituye siempre por `httpx.MockTransport`,
servido con las fixtures grabadas en `tests/fixtures/arxiv/`. El
limitador de peticiones usa dobles de `sleep`/`monotonic` (igual que
`test_rate_limiter.py`): ningún test de este fichero duerme de verdad.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nocturna.infrastructure.arxiv.client import (
    ARXIV_API_URL,
    USER_AGENT,
    ArxivClient,
    ArxivUnavailable,
)
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "arxiv"

# Categorías inventadas para estos tests: deliberadamente distintas de las
# de `config/pipeline.toml` (astro-ph.EP, astro-ph.GA), para que una
# aserción que comparase contra la config real por accidente falle.
FAKE_CATEGORIES = ("astro-ph.ZZ", "astro-ph.YY")

_FETCHED_AT = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)
_VERY_OLD_SINCE = datetime(2000, 1, 1, tzinfo=UTC)


def _load(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


class _Recorder:
    """Sirve una respuesta por petición, en orden, y guarda cada `Request`
    recibido para poder afirmar sobre la URL y las cabeceras."""

    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self._responses.pop(0)


def _xml_response(fixture_name: str, *, status_code: int = 200) -> httpx.Response:
    return httpx.Response(status_code, content=_load(fixture_name))


class _IncreasingClock:
    """`monotonic()` que siempre salta mucho hacia delante: garantiza que
    `RateLimiter.acquire()` nunca tenga que esperar. Para los tests que no
    afirman nada sobre el espaciado entre peticiones."""

    def __init__(self) -> None:
        self._value = 0.0

    def __call__(self) -> float:
        value = self._value
        self._value += 1000.0
        return value


class _FakeClock:
    """`monotonic()` devuelve los valores de `readings`, en orden."""

    def __init__(self, *readings: float) -> None:
        self._readings = list(readings)

    def __call__(self) -> float:
        return self._readings.pop(0)


class _FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


def _build_client(
    recorder: _Recorder,
    *,
    monotonic: Callable[[], float] | None = None,
    sleep: _FakeSleep | None = None,
    page_size: int = 3,
    timeout: float = 10.0,
) -> tuple[ArxivClient, httpx.AsyncClient, _FakeSleep]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle), timeout=timeout)
    sleep = sleep or _FakeSleep()
    limiter = RateLimiter(3.0, sleep=sleep, monotonic=monotonic or _IncreasingClock())
    client = ArxivClient(http, limiter=limiter, page_size=page_size, now=lambda: _FETCHED_AT)
    return client, http, sleep


@pytest.mark.anyio
async def test_la_url_lleva_search_query_sortby_sortorder_start_y_max_results():
    recorder = _Recorder([_xml_response("feed_three_entries.xml")])
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        await client.fetch_new(since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3)

    assert len(recorder.requests) == 1
    request = recorder.requests[0]
    assert request.url.scheme == "https"
    assert str(request.url).startswith(ARXIV_API_URL)

    params = request.url.params
    assert params["search_query"] == "cat:astro-ph.ZZ OR cat:astro-ph.YY"
    assert params["sortBy"] == "submittedDate"
    assert params["sortOrder"] == "descending"
    assert params["start"] == "0"
    assert params["max_results"] == "3"


@pytest.mark.anyio
async def test_la_peticion_lleva_user_agent_propio_y_respeta_el_timeout_configurado():
    recorder = _Recorder([_xml_response("feed_three_entries.xml")])
    client, http, _ = _build_client(recorder, page_size=3, timeout=7.5)

    async with http:
        await client.fetch_new(since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3)

    request = recorder.requests[0]
    assert request.headers["user-agent"] == USER_AGENT
    assert USER_AGENT == "nocturna/0.1.0"
    assert request.extensions["timeout"] == {
        "connect": 7.5,
        "read": 7.5,
        "write": 7.5,
        "pool": 7.5,
    }


@pytest.mark.anyio
async def test_la_paginacion_corta_al_cruzar_since():
    # entry[0] publicado 17:57:06Z, entry[1] 17:44:39Z, entry[2] 16:25:10Z.
    # Con since=17:00:00Z solo entry[0] y entry[1] sobreviven; entry[2]
    # dispara el corte y ni siquiera se pide una segunda página.
    since = datetime(2026, 9, 15, 17, 0, 0, tzinfo=UTC)
    recorder = _Recorder([_xml_response("feed_three_entries.xml")])
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        result = await client.fetch_new(since=since, categories=FAKE_CATEGORIES, max_results=100)

    assert len(recorder.requests) == 1
    assert [item.external_id for item in result.items] == ["2609.17526", "2609.17505"]
    assert result.truncated is False
    assert result.skipped == 0


@pytest.mark.anyio
async def test_una_entrada_descartada_en_la_pagina_no_corta_la_paginacion():
    """Regresión: el corte de paginación por "página corta" comparaba
    `len(parsed.entries) < page_max`, pero `parsed.entries` excluye las
    entradas descartadas por venir incompletas (sin `<summary>`, sin
    categorías, con fecha ilegible). Con `page_size=3` y una primera página
    de 3 entradas de las que una llega sin `<summary>`, `parsed.entries`
    solo tenía 2 elementos: parecía una página corta y el cliente dejaba de
    pedir la segunda página, devolviendo la ingesta como completa
    (`truncated=False`) tras perder en silencio el resto de la noche. En
    producción, con `page_size=100`, un solo paper roto en la página habría
    dejado la noche entera en 99 ítems reportados como ingesta completa. El
    arreglo compara `len(parsed.entries) + parsed.skipped` contra
    `page_max`, así que una sola entrada descartada no debe bastar para
    cortar la paginación.
    """
    # `since` cae entre las dos primeras entradas de `feed_page_2.xml`
    # (16:21:52Z y 15:28:39Z): la segunda página sí se pide y se corta por
    # `since` a mitad, sin depender de `max_results` para terminar el test
    # en dos peticiones.
    since = datetime(2026, 9, 15, 15, 29, 0, tzinfo=UTC)
    recorder = _Recorder(
        [_xml_response("feed_malformed_entry.xml"), _xml_response("feed_page_2.xml")]
    )
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        result = await client.fetch_new(since=since, categories=FAKE_CATEGORIES, max_results=100)

    # La segunda página sí se pidió pese a la entrada descartada en la
    # primera: sin el arreglo, esta sería la única petición.
    assert len(recorder.requests) == 2
    assert recorder.requests[0].url.params["start"] == "0"
    assert recorder.requests[1].url.params["start"] == "3"

    assert [item.external_id for item in result.items] == [
        "2609.17526",
        "2609.17383",
        "2609.17379",
    ]
    assert result.skipped == 1
    assert result.truncated is False


@pytest.mark.anyio
async def test_la_paginacion_corta_al_recibir_una_pagina_corta_sin_descartes():
    # feed_page_1.xml solo trae 3 entradas, ninguna descartada; con
    # page_size=5 la página llega corta y la paginación se detiene sin
    # pedir una segunda página. Complementario de la regresión anterior:
    # sin este test, quitar el corte por página corta (en vez de arreglar
    # cómo cuenta las entradas) dejaría el test de regresión en verde igual.
    recorder = _Recorder([_xml_response("feed_page_1.xml")])
    client, http, _ = _build_client(recorder, page_size=5)

    async with http:
        result = await client.fetch_new(
            since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
        )

    assert len(recorder.requests) == 1
    assert len(result.items) == 3
    assert result.truncated is False
    assert result.skipped == 0


@pytest.mark.anyio
async def test_la_paginacion_corta_al_alcanzar_max_results_y_marca_truncated():
    recorder = _Recorder([_xml_response("feed_page_1.xml"), _xml_response("feed_page_2.xml")])
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        result = await client.fetch_new(
            since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=4
        )

    assert len(recorder.requests) == 2
    assert len(result.items) == 4
    assert result.truncated is True
    # El primer ítem de la segunda página es el que completa el tope.
    assert result.items[-1].external_id == "2609.17379"

    first_request, second_request = recorder.requests
    assert first_request.url.params["start"] == "0"
    assert first_request.url.params["max_results"] == "3"
    assert second_request.url.params["start"] == "3"
    assert second_request.url.params["max_results"] == "1"


@pytest.mark.anyio
async def test_hay_una_espera_de_3s_entre_paginas():
    recorder = _Recorder([_xml_response("feed_page_1.xml"), _xml_response("feed_page_2.xml")])
    # Primera acquire() (antes de pedir la página 1): monotonic -> 0.0, sin
    # espera. Segunda acquire() (antes de la página 2): monotonic -> 0.0 de
    # nuevo (no ha pasado nada de tiempo "real" en el test), así que hace
    # falta esperar 3.0 completos; tras dormir, monotonic -> 3.0.
    monotonic = _FakeClock(0.0, 0.0, 3.0)
    sleep = _FakeSleep()
    client, http, sleep = _build_client(recorder, page_size=3, monotonic=monotonic, sleep=sleep)

    async with http:
        await client.fetch_new(since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=4)

    assert sleep.calls == [3.0]


@pytest.mark.anyio
async def test_http_500_lanza_arxivunavailable_sin_reintentar():
    recorder = _Recorder([_xml_response("feed_three_entries.xml", status_code=500)])
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        with pytest.raises(ArxivUnavailable):
            await client.fetch_new(
                since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
            )

    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_el_filtro_since_usa_published_no_updated():
    # `2602.11270` (índice 3 del feed) se envió el 2026-02-11 pero se
    # revisó el 2026-09-15. Con since=2026-03-01 debe quedar excluido por
    # `published`, aunque su `updated` (2026-09-15) sea muy posterior a
    # since y lo colaría si el filtro mirase el campo equivocado.
    since = datetime(2026, 3, 1, tzinfo=UTC)
    recorder = _Recorder([_xml_response("feed_revised_entries.xml")])
    client, http, _ = _build_client(recorder, page_size=10)

    async with http:
        result = await client.fetch_new(since=since, categories=FAKE_CATEGORIES, max_results=100)

    external_ids = [item.external_id for item in result.items]
    assert "2602.11270" not in external_ids
    # Las tres primeras entradas del feed (publicadas después de since)
    # sí se ingieren, incluida `2609.00140` (una revisión cuyo `published`
    # de todas formas cae después de since).
    assert external_ids == ["2609.17526", "2609.00140", "2609.17505"]
    # El corte ocurre al llegar a la entrada de `published` antiguo: no se
    # pide una segunda página.
    assert len(recorder.requests) == 1
