"""T99: un fallo del servicio de alias es un fallo de resolución POR PLANETA.

`ExoplanetArchiveCatalog.resolve_planet` traduce `ExoplanetArchiveUnavailable`
(timeout, HTTP != 200, JSON inválido, tope de peticiones) del cliente de alias a
`PlanetResolutionFailed`, y recuerda el fallo durante la vida de la instancia
(sin nueva petición). El índice local vacío NO se traduce: sigue abortando.
Sin red: `httpx.MockTransport`; timeouts reducidos.
"""

import anyio
import httpx
import pytest
from helpers.archive import alias_handler, make_archive, make_catalog

from nocturna.domain.errors import PlanetResolutionFailed
from nocturna.infrastructure.exoplanet_archive.catalog import ExoplanetArchiveCatalog
from nocturna.infrastructure.exoplanet_archive.client import (
    ArchiveRequestCapReached,
    ExoplanetArchiveUnavailable,
)

pytestmark = pytest.mark.anyio

UNKNOWN = "TOI-210 b"
NOT_FOUND = '{"manifest": {"lookup_status": "System Not Found"}}'


async def _hang(request: httpx.Request) -> httpx.Response:
    await anyio.sleep(5)
    return httpx.Response(200, text=NOT_FOUND)  # pragma: no cover - cancelado antes


def _unavailable_503(request: httpx.Request) -> httpx.Response:
    return httpx.Response(503)


def _invalid_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>no es json</html>")


def _alias_requests(seen: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in seen if "aliaslookup" in r.url.path]


@pytest.mark.parametrize(
    ("handler", "extra"),
    [
        (_hang, {"request_timeout_s": 0.05}),
        (_unavailable_503, {}),
        (_invalid_json, {}),
    ],
    ids=["timeout", "http-503", "json-invalido"],
)
async def test_alias_no_disponible_es_fallo_de_resolucion_con_causa(handler, extra):
    catalog, _, seen = make_catalog(handler, **extra)

    with pytest.raises(PlanetResolutionFailed) as info:
        await catalog.resolve_planet(UNKNOWN)

    assert not isinstance(info.value, ExoplanetArchiveUnavailable)
    assert isinstance(info.value.__cause__, ExoplanetArchiveUnavailable)
    assert f"alias de {UNKNOWN!r}" in str(info.value)
    assert len(_alias_requests(seen)) == 1


async def test_el_fallo_de_alias_no_se_cachea_como_ausente():
    """Un fallo nunca devuelve `None` ("no existe"): ni la primera vez ni después,
    y una instancia nueva puede resolver cuando el servicio vuelve."""
    state = {"up": False}

    def handler(request: httpx.Request) -> httpx.Response:
        if not state["up"]:
            return httpx.Response(503)
        return httpx.Response(200, text=NOT_FOUND)

    catalog, client, seen = make_catalog(handler)
    with pytest.raises(PlanetResolutionFailed):
        await catalog.resolve_planet(UNKNOWN)

    state["up"] = True
    fresh = ExoplanetArchiveCatalog(make_archive(), client)

    assert await fresh.resolve_planet(UNKNOWN) is None  # ahora sí: "System Not Found"
    assert len(_alias_requests(seen)) == 2


async def test_mismo_nombre_en_la_misma_instancia_falla_sin_nueva_peticion():
    catalog, _, seen = make_catalog(_unavailable_503)
    with pytest.raises(PlanetResolutionFailed) as first:
        await catalog.resolve_planet(UNKNOWN)

    with pytest.raises(PlanetResolutionFailed) as second:
        await catalog.resolve_planet(UNKNOWN)

    assert len(_alias_requests(seen)) == 1
    assert str(second.value) == str(first.value)


async def test_instancia_nueva_vuelve_a_pedir_el_alias():
    catalog, client, seen = make_catalog(_unavailable_503)
    with pytest.raises(PlanetResolutionFailed):
        await catalog.resolve_planet(UNKNOWN)

    fresh = ExoplanetArchiveCatalog(make_archive(), client)
    with pytest.raises(PlanetResolutionFailed):
        await fresh.resolve_planet(UNKNOWN)

    assert len(_alias_requests(seen)) == 2


async def test_un_planeta_que_falla_no_impide_resolver_otro():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params["objname"] == UNKNOWN:
            return httpx.Response(503)
        return alias_handler(alias_body=NOT_FOUND)(request)

    catalog, _, _ = make_catalog(handler)

    with pytest.raises(PlanetResolutionFailed):
        await catalog.resolve_planet(UNKNOWN)

    assert await catalog.resolve_planet("Otro 7 b") is None
    assert await catalog.resolve_planet("V1298 Tau b") == "V1298 Tau b"  # índice local


async def test_tope_de_peticiones_alcanzado_es_fallo_de_resolucion_sin_enviar():
    catalog, client, seen = make_catalog(_unavailable_503, max_requests=1)
    with pytest.raises(PlanetResolutionFailed):
        await catalog.resolve_planet("Foo 1 b")  # consume la única petición
    assert len(_alias_requests(seen)) == 1

    with pytest.raises(PlanetResolutionFailed) as info:
        await catalog.resolve_planet("Bar 2 b")

    assert isinstance(info.value.__cause__, ArchiveRequestCapReached)
    assert "límite" in str(info.value)
    assert "Bar 2 b" in str(info.value)
    assert len(_alias_requests(seen)) == 1  # la segunda no salió
    assert client.requests_made == 1


async def test_indice_local_vacio_sigue_lanzando_unavailable_y_no_es_fallo_por_planeta():
    catalog, _, seen = make_catalog(archive=make_archive([]))

    with pytest.raises(ExoplanetArchiveUnavailable, match="vacío") as info:
        await catalog.resolve_planet(UNKNOWN)

    assert not isinstance(info.value, PlanetResolutionFailed)
    assert seen == []
