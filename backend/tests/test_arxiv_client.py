"""Tests de `infrastructure/arxiv/client.py::ArxivClient`.

El transporte HTTP real se sustituye siempre por `httpx.MockTransport`,
servido con las fixtures grabadas en `tests/fixtures/arxiv/`. El
limitador de peticiones usa dobles de `sleep`/`monotonic` (igual que
`test_rate_limiter.py`): ningún test de este fichero duerme de verdad.
"""

import asyncio
import logging
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nocturna.infrastructure.arxiv.atom import ArxivFeedError
from nocturna.infrastructure.arxiv.client import (
    _MAX_RESPONSE_BYTES,
    ARXIV_API_URL,
    USER_AGENT,
    ArxivClient,
    ArxivUnavailable,
)
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.arxiv.retry import RetryPolicy

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


def _no_retry_policy() -> RetryPolicy:
    """Una `RetryPolicy` de un solo intento: reproduce el comportamiento de
    antes de T60.b (ningún reintento) para los tests que no están probando
    la política de reintento en sí."""
    return RetryPolicy(max_attempts=1, base_delay_s=1.0, max_elapsed_s=1.0)


def _retry_policy(*, max_attempts: int, base_delay_s: float = 0.001) -> RetryPolicy:
    """`RetryPolicy` para los tests que sí ejercitan el reintento.

    `ArxivClient._get` construye su propio `Retrier` internamente a partir
    de esta política, con `sleep`/`monotonic`/`jitter` reales -- ya no hay
    ninguna costura para inyectar dobles de esos tres desde fuera (T60.c:
    "para que dos llamadas concurrentes no se pisen los contadores", ver
    docstring de `client.py`). `base_delay_s` minúsculo por defecto es la
    única forma de mantener estos tests sin esperas reales: con jitter real
    (`uniform(0.5, 1.5)`) sobre un `base_delay_s` de 0.001 s, la espera más
    larga de cualquier secuencia de este fichero es de milisegundos.
    `max_elapsed_s` generoso para que ese backoff nominal, ya minúsculo,
    nunca dispare un corte por tiempo que ningún test de este fichero está
    buscando."""
    return RetryPolicy(max_attempts=max_attempts, base_delay_s=base_delay_s, max_elapsed_s=10.0)


def _build_client(
    recorder: _Recorder,
    *,
    monotonic: Callable[[], float] | None = None,
    sleep: _FakeSleep | None = None,
    page_size: int = 3,
    timeout: float = 10.0,
    retry_policy: RetryPolicy | None = None,
) -> tuple[ArxivClient, httpx.AsyncClient, _FakeSleep]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle), timeout=timeout)
    sleep = sleep or _FakeSleep()
    limiter = RateLimiter(3.0, sleep=sleep, monotonic=monotonic or _IncreasingClock())
    client = ArxivClient(
        http,
        limiter=limiter,
        page_size=page_size,
        now=lambda: _FETCHED_AT,
        retry_policy=retry_policy or _no_retry_policy(),
    )
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
async def test_http_406_lanza_arxivunavailable_con_el_codigo_en_el_mensaje_y_no_arxivfeederror():
    # Caso observado en producción (2026-09-18): un 406 llegaba con el
    # cuerpo vacío y `parse_feed` lo diagnosticaba como "XML inválido",
    # ocultando que el problema era el código de estado. `_get` debe cortar
    # antes de llegar a parsear.
    recorder = _Recorder([httpx.Response(406, content=b"")])
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        with pytest.raises(ArxivUnavailable, match="406") as excinfo:
            await client.fetch_new(
                since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
            )

    assert not isinstance(excinfo.value, ArxivFeedError)
    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_http_429_lanza_arxivunavailable_con_el_codigo_en_el_mensaje_y_no_arxivfeederror():
    recorder = _Recorder([httpx.Response(429, content=b"")])
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        with pytest.raises(ArxivUnavailable, match="429") as excinfo:
            await client.fetch_new(
                since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
            )

    assert not isinstance(excinfo.value, ArxivFeedError)
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


# --- T60.b: reintentos ante fallos transitorios ----------------------------


class _ExceptionThenResponses:
    """Sirve, en orden, una mezcla de `httpx.Response` y excepciones: si el
    siguiente elemento de `items` es una excepción, la levanta en vez de
    devolverla (para simular `httpx.TransportError`)."""

    def __init__(self, items: list[httpx.Response | Exception]) -> None:
        self._items = list(items)
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


@pytest.mark.anyio
async def test_406_seguido_de_200_devuelve_los_items_sin_error():
    recorder = _Recorder(
        [httpx.Response(406, content=b""), _xml_response("feed_three_entries.xml")]
    )
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )

    async with http:
        result = await client.fetch_new(
            since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3
        )

    assert len(recorder.requests) == 2
    assert len(result.items) == 3


@pytest.mark.anyio
async def test_el_reintento_repite_la_misma_pagina():
    recorder = _Recorder(
        [httpx.Response(406, content=b""), _xml_response("feed_three_entries.xml")]
    )
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )

    async with http:
        await client.fetch_new(since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3)

    assert len(recorder.requests) == 2
    first, second = recorder.requests
    assert first.url.params["start"] == second.url.params["start"] == "0"
    assert first.url.params["max_results"] == second.url.params["max_results"] == "3"


@pytest.mark.anyio
async def test_406_persistente_agota_los_intentos_y_lanza_arxivunavailable_con_codigo_y_conteo():
    recorder = _Recorder([httpx.Response(406, content=b"") for _ in range(3)])
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )

    async with http:
        with pytest.raises(ArxivUnavailable) as excinfo:
            await client.fetch_new(
                since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
            )

    assert len(recorder.requests) == 3
    message = str(excinfo.value)
    assert "406" in message
    assert "3 intentos" in message
    # Mutante detectado en revisión: hoy solo se congelaba "406" y "3
    # intentos" -- el motivo de corte (`_STOP_REASON_TEXT[stop_reason]`) y
    # la página que falló (`(start=N)`) podían perderse del mensaje sin que
    # ningún test lo notara, y son justo lo que el autor lee en el log por
    # la mañana para diagnosticar sin tener que abrir el JSON estructurado.
    assert "se agotaron los intentos" in message
    assert "(start=0)" in message


@pytest.mark.anyio
async def test_http_429_se_reintenta():
    recorder = _Recorder(
        [httpx.Response(429, content=b""), _xml_response("feed_three_entries.xml")]
    )
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )

    async with http:
        result = await client.fetch_new(
            since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3
        )

    assert len(recorder.requests) == 2
    assert len(result.items) == 3


@pytest.mark.anyio
async def test_http_5xx_se_reintenta():
    recorder = _Recorder(
        [
            _xml_response("feed_three_entries.xml", status_code=503),
            _xml_response("feed_three_entries.xml"),
        ]
    )
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )

    async with http:
        result = await client.fetch_new(
            since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3
        )

    assert len(recorder.requests) == 2
    assert len(result.items) == 3


@pytest.mark.anyio
async def test_un_4xx_distinto_de_406_y_429_no_se_reintenta():
    recorder = _Recorder([httpx.Response(400, content=b"")])
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=4)
    )

    async with http:
        with pytest.raises(ArxivUnavailable, match="400"):
            await client.fetch_new(
                since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
            )

    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_un_error_de_transporte_se_reintenta():
    recorder = _ExceptionThenResponses(
        [httpx.ConnectError("fallo de conexión"), _xml_response("feed_three_entries.xml")]
    )
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )

    async with http:
        result = await client.fetch_new(
            since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3
        )

    assert len(recorder.requests) == 2
    assert len(result.items) == 3


@pytest.mark.anyio
async def test_una_respuesta_mayor_que_el_tope_no_se_reintenta():
    oversized = httpx.Response(200, content=b"x" * (_MAX_RESPONSE_BYTES + 1))
    recorder = _Recorder([oversized])
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=4)
    )

    async with http:
        with pytest.raises(ArxivUnavailable):
            await client.fetch_new(
                since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
            )

    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_el_reintento_pasa_por_el_limitador_de_cortesia():
    recorder = _Recorder(
        [httpx.Response(406, content=b""), _xml_response("feed_three_entries.xml")]
    )
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle), timeout=10.0)
    limiter_sleep = _FakeSleep()
    # Reloj de cortesía congelado: cada `acquire()` mide el mismo "ahora",
    # así que la segunda petición (el reintento) siempre tiene que esperar
    # los 3 s completos.
    limiter = RateLimiter(3.0, sleep=limiter_sleep, monotonic=lambda: 0.0)
    client = ArxivClient(
        http,
        limiter=limiter,
        page_size=3,
        now=lambda: _FETCHED_AT,
        retry_policy=_retry_policy(max_attempts=3),
    )

    async with http:
        await client.fetch_new(since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3)

    assert limiter_sleep.calls == [3.0]


@pytest.mark.anyio
async def test_un_406_en_la_segunda_pagina_no_pierde_ni_duplica_items():
    # Paginación limpia de referencia, sin ningún fallo.
    clean_recorder = _Recorder([_xml_response("feed_page_1.xml"), _xml_response("feed_page_2.xml")])
    clean_client, clean_http, _ = _build_client(clean_recorder, page_size=3)
    async with clean_http:
        clean_result = await clean_client.fetch_new(
            since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=6
        )

    # Misma paginación, pero la segunda página falla una vez con 406 antes
    # de responder con éxito.
    flaky_recorder = _Recorder(
        [
            _xml_response("feed_page_1.xml"),
            httpx.Response(406, content=b""),
            _xml_response("feed_page_2.xml"),
        ]
    )
    flaky_client, flaky_http, _ = _build_client(
        flaky_recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )
    async with flaky_http:
        flaky_result = await flaky_client.fetch_new(
            since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=6
        )

    assert len(clean_recorder.requests) == 2
    assert len(flaky_recorder.requests) == 3
    assert [item.external_id for item in flaky_result.items] == [
        item.external_id for item in clean_result.items
    ]
    assert flaky_result.truncated == clean_result.truncated
    assert flaky_result.skipped == clean_result.skipped

    # Mutante detectado en revisión: `if attempt > 1: params = {**params,
    # 'start': 0}` deja pasar toda la suite si nada afirma sobre `start`
    # aquí -- las dos aserciones anteriores solo comparan el RESULTADO
    # final, y con una única página fallida ese resultado puede coincidir
    # por casualidad. La petición que falla (índice 1, la segunda página) y
    # su reintento (índice 2) deben llevar el MISMO `start`, y ese `start`
    # debe ser el de la segunda página (3), no el de la primera (0): un
    # reintento que reseteara `start` a 0 volvería a pedir la página 1 en
    # producción, la deduplicación taparía los duplicados, y la página 3
    # (si la hubiera) se perdería en silencio.
    failed_request, retried_request = flaky_recorder.requests[1], flaky_recorder.requests[2]
    assert failed_request.url.params["start"] == retried_request.url.params["start"] == "3"


@pytest.mark.anyio
async def test_los_eventos_de_reintento_quedan_en_el_log(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    # Al lanzar la suite completa, `tests/db/` corre antes (orden
    # alfabético) y su fixture de migraciones invoca
    # `alembic/env.py::fileConfig`, que deshabilita cualquier logger ya
    # existente y no declarado en `alembic.ini` -- incluido el de
    # `client.py`. Mismo parche, mismo motivo, que
    # `test_run_night.py::test_excepcion_inesperada_en_un_item_queda_aislada_y_la_noche_continua`.
    from nocturna.infrastructure.arxiv import client as arxiv_client_module

    monkeypatch.setattr(arxiv_client_module._logger, "disabled", False)

    recorder = _Recorder(
        [httpx.Response(406, content=b""), _xml_response("feed_three_entries.xml")]
    )
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )

    with caplog.at_level(logging.INFO, logger="nocturna.infrastructure.arxiv.client"):
        async with http:
            await client.fetch_new(since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=3)

    retry_records = [r for r in caplog.records if r.__dict__.get("event") == "arxiv.retry"]
    assert len(retry_records) == 1
    retry_fields = retry_records[0].__dict__
    assert retry_fields["attempt"] == 2
    assert retry_fields["reason"] == "http_406"
    assert retry_fields["status_code"] == 406
    assert retry_fields["delay_s"] is not None

    recovered_records = [
        r for r in caplog.records if r.__dict__.get("event") == "arxiv.retry_recovered"
    ]
    assert len(recovered_records) == 1
    # Mutante detectado en revisión: sustituir el diccionario de campos por
    # `{"foo": None}` deja pasar la suite si solo se comprueba el nombre del
    # evento. Es el evento que se acabará usando en `grep` para la tabla de
    # calibración (cuántos intentos hicieron falta, cuánto tardó la noche en
    # recuperarse), así que sus campos van congelados aquí.
    recovered_fields = recovered_records[0].__dict__
    assert recovered_fields["attempts_made"] == 2
    assert isinstance(recovered_fields["elapsed_s"], float)
    assert recovered_fields["elapsed_s"] >= 0


@pytest.mark.anyio
async def test_arxiv_retry_exhausted_queda_en_el_log_al_agotar_los_intentos(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
):
    """Mutante detectado en revisión: sustituir el `_logger.error(...)` de
    `arxiv.retry_exhausted` por un `_logger.debug("NO.EVENT", ...)` pasa la
    suite entera si ningún test busca este evento. Es el que dice que la
    ingesta se rindió del todo esa noche -- sin él, un 406 persistente solo
    deja la excepción y su traceback, no un campo estructurado consultable
    a las 3 de la mañana."""
    from nocturna.infrastructure.arxiv import client as arxiv_client_module

    monkeypatch.setattr(arxiv_client_module._logger, "disabled", False)

    recorder = _Recorder([httpx.Response(406, content=b"") for _ in range(3)])
    client, http, _ = _build_client(
        recorder, page_size=3, retry_policy=_retry_policy(max_attempts=3)
    )

    with caplog.at_level(logging.INFO, logger="nocturna.infrastructure.arxiv.client"):
        async with http:
            with pytest.raises(ArxivUnavailable):
                await client.fetch_new(
                    since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
                )

    exhausted_records = [
        r for r in caplog.records if r.__dict__.get("event") == "arxiv.retry_exhausted"
    ]
    assert len(exhausted_records) == 1
    fields = exhausted_records[0].__dict__
    assert fields["attempt"] == 3
    assert fields["max_attempts"] == 3
    assert fields["reason"] == "http_406"
    assert fields["status_code"] == 406
    assert fields["stop_reason"] == "max_attempts"
    assert fields["start"] == 0


@pytest.mark.anyio
async def test_cancelar_mientras_el_retrier_duerme_propaga_cancelledeerror():
    """El código es correcto por construcción -- ningún `except
    BaseException` de este módulo puede tragarse una cancelación, y la
    espera del backoff es siempre `anyio.sleep`, nunca `time.sleep` síncrono
    (ver docstring de `retry.py`) -- pero lo es SOLO por construcción: sin
    este test, un futuro `time.sleep` colado en `_get`, o un `except
    Exception` demasiado ancho alrededor de la petición, lo rompería en
    silencio. La ventana de ejecución (`window.hard_stop`, ADR 0005) confía
    en que cancelar la tarea de la noche corta de verdad una ingesta a
    mitad de un reintento."""
    recorder = _Recorder([httpx.Response(500, content=b"") for _ in range(5)])
    # `base_delay_s` no minúsculo a propósito, a diferencia del resto de
    # este fichero: hace falta una ventana real, aunque breve (jitter
    # 0.5x-1.5x sobre 0.2 s: 0.1-0.3 s), en la que el `Retrier` esté de
    # verdad dormido cuando se cancela la tarea.
    retry_policy = RetryPolicy(max_attempts=5, base_delay_s=0.2, max_elapsed_s=10.0)
    client, http, _ = _build_client(recorder, page_size=3, retry_policy=retry_policy)

    async with http:
        task = asyncio.ensure_future(
            client.fetch_new(since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100)
        )
        # Deja que la tarea complete el primer intento (falla con 500,
        # rápido de verdad vía `httpx.MockTransport`) y entre en la espera
        # real antes del segundo. 0.05 s cabe con margen dentro de la
        # ventana de 0.1-0.3 s sin acercarse a ella.
        await asyncio.sleep(0.05)
        assert not task.done(), "la tarea debería seguir dormida en el backoff del reintento"
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task


@pytest.mark.anyio
async def test_un_302_no_llega_a_parse_feed():
    # httpx no sigue redirecciones por defecto: un 302 llega tal cual a
    # `_get`. Antes de esta clasificación caía en `parse_feed` y producía
    # el diagnóstico engañoso "XML inválido" que el módulo dice haber
    # eliminado -- ver docstring de `_status_error_message`.
    recorder = _Recorder([httpx.Response(302, content=b"")])
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        with pytest.raises(ArxivUnavailable, match="302") as excinfo:
            await client.fetch_new(
                since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
            )

    assert not isinstance(excinfo.value, ArxivFeedError)
    assert len(recorder.requests) == 1


@pytest.mark.anyio
async def test_un_204_no_llega_a_parse_feed():
    # 204 No Content: cuerpo vacío con un código de éxito HTTP que, sin la
    # clasificación explícita "distinto de 200", también caería en
    # `parse_feed` y produciría el mismo diagnóstico engañoso.
    recorder = _Recorder([httpx.Response(204, content=b"")])
    client, http, _ = _build_client(recorder, page_size=3)

    async with http:
        with pytest.raises(ArxivUnavailable, match="204") as excinfo:
            await client.fetch_new(
                since=_VERY_OLD_SINCE, categories=FAKE_CATEGORIES, max_results=100
            )

    assert not isinstance(excinfo.value, ArxivFeedError)
    assert len(recorder.requests) == 1


def test_el_texto_de_error_distingue_5xx_de_4xx():
    """Hueco preexistente a T60.b, cerrado ahora que hay tests de verdad
    sobre 5xx: nada impedía que `_status_error_message` devolviera "(rechazo
    de la petición)" también para un 5xx, un texto que le echa la culpa a
    *nuestra* petición cuando el problema es del lado de arXiv -- ver el
    docstring del propio `_status_error_message`, que distingue los dos
    casos a propósito."""
    from nocturna.infrastructure.arxiv.client import _status_error_message

    assert "(error de servidor)" in _status_error_message(500)
    assert "(error de servidor)" in _status_error_message(503)
    assert "(rechazo de la petición)" not in _status_error_message(500)
    assert "(rechazo de la petición)" in _status_error_message(406)
    assert "(error de servidor)" not in _status_error_message(406)
