"""`ArchiveHttpClient` (T74): MockTransport, sin red y sin esperas reales."""

import httpx
import pytest
from helpers.archive import ALIAS_URL, TAP_URL, FakeSleep, fixture_text, make_client

from nocturna.infrastructure.exoplanet_archive import client as client_module
from nocturna.infrastructure.exoplanet_archive.client import (
    USER_AGENT,
    ArchiveRequestCapReached,
    ExoplanetArchiveUnavailable,
)

pytestmark = pytest.mark.anyio


def _ok_csv(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text='pl_name\n"WASP-12 b"\n')


async def test_envia_el_user_agent_neutro():
    client, seen = make_client(_ok_csv)

    await client.query_csv("select pl_name from pscomppars")

    assert seen[0].headers["User-Agent"] == USER_AGENT == "nocturna/0.1.0"


async def test_query_csv_pide_formato_csv_y_devuelve_none_en_vacios():
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).startswith(TAP_URL)
        assert request.url.params["format"] == "csv"
        return httpx.Response(200, text="a,b\n1,\n")

    client, _ = make_client(handler)

    assert await client.query_csv("select 1") == [{"a": "1", "b": None}]


async def test_la_peticion_max_requests_mas_uno_no_sale_y_lanza_cap_reached():
    client, seen = make_client(_ok_csv, max_requests=2)

    await client.query_csv("q1")
    await client.query_csv("q2")
    with pytest.raises(ArchiveRequestCapReached):
        await client.query_csv("q3")

    assert len(seen) == 2  # el transporte no vio la tercera
    assert client.requests_made == 2


async def test_cap_reached_es_un_unavailable():
    assert issubclass(ArchiveRequestCapReached, ExoplanetArchiveUnavailable)


async def test_el_techo_cuenta_tambien_las_peticiones_fallidas():
    def boom(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client, seen = make_client(boom, max_requests=1)

    with pytest.raises(ExoplanetArchiveUnavailable):
        await client.query_csv("q1")
    with pytest.raises(ArchiveRequestCapReached):
        await client.query_csv("q2")

    assert len(seen) == 1


async def test_requests_made_cuenta_tap_y_alias():
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url).startswith(ALIAS_URL):
            return httpx.Response(200, text="{}")
        return httpx.Response(200, text="a\n1\n")

    client, _ = make_client(handler)
    assert client.requests_made == 0

    await client.query_csv("q")
    await client.lookup_alias("WASP-12")

    assert client.requests_made == 2


async def test_el_sleep_inyectado_recibe_el_intervalo_entre_peticiones():
    sleep = FakeSleep()
    client, _ = make_client(_ok_csv, interval_s=2.0, sleep=sleep)

    await client.query_csv("q1")
    assert sleep.calls == []
    await client.query_csv("q2")

    assert sleep.calls == [2.0]


@pytest.mark.parametrize("status", [404, 429, 500, 503])
async def test_http_distinto_de_200_lanza_unavailable(status):
    client, _ = make_client(lambda r: httpx.Response(status, text="no"))

    with pytest.raises(ExoplanetArchiveUnavailable, match=str(status)):
        await client.query_csv("q")


async def test_votable_de_error_con_http_200_lanza_unavailable():
    body = fixture_text("pscomppars_error_gaia_id.xml")
    client, _ = make_client(lambda r: httpx.Response(200, text=body))

    with pytest.raises(ExoplanetArchiveUnavailable, match="VOTABLE"):
        await client.query_csv("select gaia_id from pscomppars")


async def test_csv_con_votable_en_una_celda_no_falla():
    body = "pl_name,note\nFoo b,see VOTABLE and QUERY_STATUS docs\n"
    client, _ = make_client(lambda r: httpx.Response(200, text=body))

    rows = await client.query_csv("q")

    assert rows == [{"pl_name": "Foo b", "note": "see VOTABLE and QUERY_STATUS docs"}]


async def test_respuesta_por_encima_del_tope_de_tamano_lanza_unavailable(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 100)
    client, _ = make_client(lambda r: httpx.Response(200, content=b"a\n" + b"x" * 101))

    with pytest.raises(ExoplanetArchiveUnavailable, match="tope"):
        await client.query_csv("q")


async def test_respuesta_exactamente_en_el_tope_se_acepta(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 10)
    client, _ = make_client(lambda r: httpx.Response(200, content=b"a\n1\n2\n3\n4\n"))

    assert len(await client.query_csv("q")) == 4


async def test_timeout_de_httpx_se_convierte_en_unavailable():
    def slow(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("lento", request=request)

    client, _ = make_client(slow)

    with pytest.raises(ExoplanetArchiveUnavailable):
        await client.query_csv("q")


async def test_error_de_conexion_se_convierte_en_unavailable():
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("sin red", request=request)

    client, _ = make_client(down)

    with pytest.raises(ExoplanetArchiveUnavailable):
        await client.lookup_alias("WASP-12")


async def test_lookup_alias_devuelve_el_json_del_fixture():
    client, seen = make_client(
        lambda r: httpx.Response(200, text=fixture_text("aliaslookup_wasp12.json"))
    )

    payload = await client.lookup_alias("WASP-12")

    assert payload["manifest"]["lookup_status"] == "OK"
    assert seen[0].url.params["objname"] == "WASP-12"


@pytest.mark.parametrize("body", ["no es json", "", "[1, 2]", '"cadena"'])
async def test_alias_con_json_invalido_o_no_objeto_lanza_unavailable(body):
    client, _ = make_client(lambda r: httpx.Response(200, text=body))

    with pytest.raises(ExoplanetArchiveUnavailable):
        await client.lookup_alias("WASP-12")


async def test_la_cota_de_duracion_total_convierte_una_respuesta_lenta_en_unavailable():
    import anyio

    async def stalled(request: httpx.Request) -> httpx.Response:
        await anyio.sleep(30)
        return httpx.Response(200, text="a\n1\n")

    client, _ = make_client(stalled, request_timeout_s=0.05)

    with pytest.raises(ExoplanetArchiveUnavailable, match="no completó"):
        await client.query_csv("q")
