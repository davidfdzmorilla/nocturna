"""`ExoplanetArchiveCatalog` (T74) con MockTransport y fixtures reales."""

import json

import httpx
import pytest
from helpers.archive import ALIAS_URL, adql_of, archive_handler, make_catalog
from helpers.exoplanet import make_item, make_measurement, make_reading

from nocturna.application.use_cases.compute_tensions import ComputeTensions, SkipReason
from nocturna.domain.entities import (
    MeasuredParameter,
    MeasurementLimit,
    MeasurementOrigin,
)
from nocturna.infrastructure.exoplanet_archive.client import ExoplanetArchiveUnavailable

pytestmark = pytest.mark.anyio

MASS = MeasuredParameter.MASS
RADIUS = MeasuredParameter.RADIUS
PERIOD = MeasuredParameter.PERIOD


def _alias_requests(seen: list[httpx.Request]) -> list[httpx.Request]:
    return [r for r in seen if str(r.url).startswith(ALIAS_URL)]


def _tap_queries(seen: list[httpx.Request]) -> list[str]:
    return [adql_of(r) for r in seen if not str(r.url).startswith(ALIAS_URL)]


async def test_coincidencia_exacta_no_consulta_alias():
    catalog, _, seen = make_catalog()

    assert await catalog.resolve_planet("V1298 Tau b") == "V1298 Tau b"

    assert _alias_requests(seen) == []


@pytest.mark.parametrize(
    ("raw", "canonical"),
    [
        ("WASP-12b", "WASP-12 b"),
        ("wasp-12 b", "WASP-12 b"),
        ("WASP–12 b", "WASP-12 b"),
        ("V1298,Tau b", "V1298 Tau b"),
        ("V1298~Tau  b", "V1298 Tau b"),
    ],
)
async def test_coincidencia_por_normalizacion_devuelve_el_nombre_del_archivo(raw, canonical):
    catalog, _, seen = make_catalog()

    assert await catalog.resolve_planet(raw) == canonical

    assert _alias_requests(seen) == []


async def test_alias_se_resuelve_por_la_rama_de_planeta():
    catalog, _, seen = make_catalog()

    assert await catalog.resolve_planet("TOI-1725 b") == "WASP-12 b"

    assert len(_alias_requests(seen)) == 1


def _synthetic_alias_body(default_name: str, aliases: list[str]) -> str:
    """JSON de alias SINTÉTICO (no grabado del archivo), con la misma
    estructura que `aliaslookup_wasp12.json`: planeta en `planet_set`."""
    return json.dumps(
        {
            "manifest": {"lookup_status": "OK"},
            "system": {
                "objects": {
                    "planet_set": {
                        "item_count": 1,
                        "planets": {
                            default_name: {
                                "alias_set": {
                                    "item_count": len(aliases),
                                    "default_name": default_name,
                                    "aliases": aliases,
                                }
                            }
                        },
                    }
                }
            },
        }
    )


async def test_nombre_sin_digitos_fuera_del_indice_consulta_alias_y_resuelve():
    body = _synthetic_alias_body("WASP-12 b", ["WASP-12 b", "Beta Pictoris b"])
    catalog, _, seen = make_catalog(archive_handler(alias_body=body))

    assert await catalog.resolve_planet("Beta Pictoris b") == "WASP-12 b"

    assert len(_alias_requests(seen)) == 1


async def test_nombre_sin_digitos_sin_alias_devuelve_none_con_una_sola_peticion():
    body = '{"manifest": {"lookup_status": "NOT_FOUND"}}'
    catalog, _, seen = make_catalog(archive_handler(alias_body=body))

    assert await catalog.resolve_planet("Proxima Centauri b") is None
    assert await catalog.resolve_planet("proxima centauri b") is None

    assert len(_alias_requests(seen)) == 1


async def test_el_alias_recibe_el_nombre_limpio_sin_restos_de_latex():
    catalog, _, seen = make_catalog()

    await catalog.resolve_planet("TOI-1725~b\\,x")

    (request,) = _alias_requests(seen)
    assert request.url.params["objname"] == "TOI-1725 b x"


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(200, text=""),
        lambda r: httpx.Response(200, text="pl_name\n"),
    ],
)
async def test_indice_vacio_lanza_unavailable(handler):
    catalog, _, _ = make_catalog(handler)

    with pytest.raises(ExoplanetArchiveUnavailable, match="vacío"):
        await catalog.resolve_planet("V1298 Tau b")


async def test_nombre_que_solo_es_de_la_estrella_devuelve_none():
    catalog, _, _ = make_catalog()

    # "WASP-12" es alias del sistema y de la estrella, no de ningún planeta.
    assert await catalog.resolve_planet("WASP-12") is None


async def test_alias_sin_resolver_devuelve_none():
    body = '{"manifest": {"lookup_status": "NOT_FOUND"}}'
    catalog, _, _ = make_catalog(archive_handler(alias_body=body))

    assert await catalog.resolve_planet("XYZ-999 b") is None


async def test_el_indice_se_pide_una_sola_vez():
    catalog, _, seen = make_catalog()

    for name in ("V1298 Tau b", "V1298 Tau e", "WASP-12b", "Foo Bar b"):
        await catalog.resolve_planet(name)

    assert _tap_queries(seen) == ["select pl_name from pscomppars"]


async def test_el_alias_se_pide_una_vez_por_nombre_normalizado():
    catalog, _, seen = make_catalog()

    for name in ("TOI-1725 b", "TOI-1725b", "toi-1725  b", "TOI–1725 b"):
        assert await catalog.resolve_planet(name) == "WASP-12 b"

    assert len(_alias_requests(seen)) == 1


async def test_alias_negativo_tambien_se_cachea():
    catalog, _, seen = make_catalog(archive_handler(alias_body='{"manifest": {}}'))

    await catalog.resolve_planet("XYZ-999 b")
    await catalog.resolve_planet("xyz-999 b")

    assert len(_alias_requests(seen)) == 1


async def test_una_peticion_ps_por_planeta_para_los_tres_parametros():
    catalog, client, seen = make_catalog()

    mass = await catalog.solutions("V1298 Tau b", MASS)
    radius = await catalog.solutions("V1298 Tau b", RADIUS)
    period = await catalog.solutions("V1298 Tau b", PERIOD)

    assert (len(mass), len(radius), len(period)) == (2, 5, 6)
    ps_queries = [q for q in _tap_queries(seen) if "from ps where" in q]
    assert len(ps_queries) == 1
    assert "pl_name='V1298 Tau b'" in ps_queries[0]
    assert client.requests_made == 1


async def test_dos_planetas_son_dos_peticiones_ps():
    catalog, client, _ = make_catalog()

    await catalog.solutions("V1298 Tau b", MASS)
    await catalog.solutions("V1298 Tau e", MASS)

    assert client.requests_made == 2


async def test_la_comilla_simple_se_escapa_en_el_adql():
    catalog, _, seen = make_catalog(lambda r: httpx.Response(200, text="pl_name\n"))

    await catalog.solutions("Foo's b", MASS)

    assert "pl_name='Foo''s b'" in adql_of(seen[0])


async def test_solo_masa_verdadera_llega_como_solucion_de_masa():
    catalog, _, _ = make_catalog()

    solutions = await catalog.solutions("V1298 Tau e", MASS)

    assert {s.reference for s in solutions} == {
        "Suárez Mascareño et al. 2022",
        "Finociety et al. 2023",
        "Livingston et al. 2026",
    }
    assert [s.reference for s in solutions if s.is_default] == ["Livingston et al. 2026"]


async def test_sin_medidas_utilizables_no_sale_ninguna_peticion():
    catalog, client, seen = make_catalog()
    measurements = (
        make_measurement(0.5, 0.1, 0.1, limit=MeasurementLimit.UPPER),
        make_measurement(0.5, 0.1, 0.1, origin=MeasurementOrigin.LITERATURE),
        make_measurement(0.5, None, 0.1),
    )
    item = make_item()

    report = await ComputeTensions(catalog)([(item, make_reading(item.id, measurements))])

    assert report.results == ()
    assert {s.reason for s in report.skipped} == {SkipReason.NOT_USABLE}
    assert seen == []
    assert client.requests_made == 0


async def test_lecturas_sin_medidas_o_con_lista_vacia_no_sacan_peticiones():
    catalog, _, seen = make_catalog()
    first, second = make_item(), make_item("2601.00002")

    await ComputeTensions(catalog)(
        [(first, make_reading(first.id, None)), (second, make_reading(second.id, ()))]
    )

    assert seen == []
