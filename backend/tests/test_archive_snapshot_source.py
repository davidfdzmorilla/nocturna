"""`ArchiveSnapshotSource` (T81): ADQL exacto, lotes, techo y tope de respuesta. Sin red."""

from datetime import date

import httpx
import pytest
from helpers.archive import adql_of, make_client, snapshot_handler

from nocturna import cli
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.exoplanet_archive.client import (
    ArchiveRequestCapReached,
    ExoplanetArchiveUnavailable,
)
from nocturna.infrastructure.exoplanet_archive.snapshot import ArchiveSnapshotSource

pytestmark = pytest.mark.anyio

# Escrito a mano a propósito: si alguien reordena SNAPSHOT_COLUMNS, este test falla.
EXPECTED_SELECT = (
    "select pl_name,hostname,default_flag,soltype,pl_refname,releasedate,pl_pubdate,"
    "pl_bmassprov,pl_bmasse,pl_bmasseerr1,pl_bmasseerr2,pl_bmasselim,"
    "pl_rade,pl_radeerr1,pl_radeerr2,pl_radelim,"
    "pl_orbper,pl_orbpererr1,pl_orbpererr2,pl_orbperlim,"
    "st_rad,st_raderr1,st_raderr2,st_mass,st_masserr1,st_masserr2,"
    "discoverymethod,ttv_flag,pl_controv_flag from ps"
)


def _source(batch: int = 75, **kw):
    client, seen = make_client(snapshot_handler(), **kw)
    return ArchiveSnapshotSource(client, planet_batch_size=batch), seen


def test_son_29_columnas_en_el_orden_esperado():
    cols = EXPECTED_SELECT.removeprefix("select ").removesuffix(" from ps").split(",")
    assert len(cols) == 29


async def test_adql_exacto_de_las_cuatro_consultas():
    source, seen = _source()
    await source.all_solutions()
    await source.released_since(date(2026, 9, 5))
    await source.default_solutions()
    await source.solutions_for_planets(["V1298 Tau b"])

    assert [adql_of(r) for r in seen] == [
        EXPECTED_SELECT,
        EXPECTED_SELECT + " where releasedate >= '2026-09-05'",
        EXPECTED_SELECT + " where default_flag=1",
        EXPECTED_SELECT + " where pl_name in ('V1298 Tau b')",
    ]
    assert source.requests_made == 4


async def test_la_fecha_de_released_since_va_en_formato_iso_con_ceros():
    source, seen = _source()
    await source.released_since(date(2026, 1, 2))
    assert adql_of(seen[0]).endswith("where releasedate >= '2026-01-02'")


async def test_las_peticiones_piden_csv():
    source, seen = _source()
    await source.default_solutions()
    assert seen[0].url.params["format"] == "csv"


async def test_lotes_segun_planet_batch_size_ordenados_y_sin_duplicados():
    source, seen = _source(batch=2)
    await source.solutions_for_planets(["E b", "A b", "C b", "A b", "B b", "D b"])
    assert source.requests_made == 3
    assert [adql_of(r).split(" where ")[1] for r in seen] == [
        "pl_name in ('A b','B b')",
        "pl_name in ('C b','D b')",
        "pl_name in ('E b')",
    ]


async def test_un_lote_exacto_no_genera_peticion_extra():
    source, seen = _source(batch=2)
    await source.solutions_for_planets(["A b", "B b"])
    assert len(seen) == 1


async def test_sin_planetas_no_hay_peticiones():
    source, seen = _source()
    assert list(await source.solutions_for_planets([])) == []
    assert seen == [] and source.requests_made == 0


async def test_comillas_simples_se_escapan_duplicandolas():
    source, seen = _source()
    await source.solutions_for_planets(["Barnard's star b"])
    assert adql_of(seen[0]).endswith("pl_name in ('Barnard''s star b')")


async def test_los_lotes_devuelven_la_union_de_las_respuestas():
    source, _ = _source(batch=1)
    sols = await source.solutions_for_planets(["V1298 Tau b", "HD 202206 c"])
    assert {s.pl_name for s in sols} == {"V1298 Tau b", "HD 202206 c"}
    assert source.requests_made == 2


def test_batch_size_menor_que_uno_se_rechaza():
    client, _ = make_client(snapshot_handler())
    with pytest.raises(ValueError):
        ArchiveSnapshotSource(client, planet_batch_size=0)


async def test_el_techo_de_peticiones_se_respeta_y_la_siguiente_no_sale():
    source, seen = _source(max_requests=2)
    await source.all_solutions()
    await source.default_solutions()
    with pytest.raises(ArchiveRequestCapReached):
        await source.released_since(date(2026, 9, 5))
    assert len(seen) == 2
    assert source.requests_made == 2


async def test_el_techo_corta_a_mitad_de_los_lotes():
    source, seen = _source(batch=1, max_requests=2)
    with pytest.raises(ArchiveRequestCapReached):
        await source.solutions_for_planets(["A b", "B b", "C b"])
    assert len(seen) == 2


async def test_un_503_se_propaga_como_archivo_no_disponible():
    client, _ = make_client(lambda r: httpx.Response(503))
    source = ArchiveSnapshotSource(client, planet_batch_size=75)
    with pytest.raises(ExoplanetArchiveUnavailable, match="503"):
        await source.all_solutions()


# ---- tope de respuesta: el del snapshot se aplica, el del catalogo (T74) no cambia


def _real_config(**snapshot_updates):
    config = load_pipeline_config()
    snap = config.sources.exoplanet_archive.snapshot.model_copy(update=snapshot_updates)
    archive = config.sources.exoplanet_archive.model_copy(update={"snapshot": snap})
    sources = config.sources.model_copy(update={"exoplanet_archive": archive})
    return config.model_copy(update={"sources": sources})


async def test_max_response_bytes_del_snapshot_se_aplica_desde_la_config():
    config = _real_config(max_response_bytes=100)
    transport = httpx.MockTransport(
        lambda r: httpx.Response(200, content=b"pl_name\n" + b"x" * 500)
    )
    async with httpx.AsyncClient(transport=transport) as http:
        source = cli.archive_snapshot_source_from_config(http, config)
        with pytest.raises(ExoplanetArchiveUnavailable, match="supera el tope de 100"):
            await source.all_solutions()


_BIG_BODY = b"pl_name\n" + (b"x" * 100_000 + b"\n") * 210  # ~21 MB, filas < limite de campo de csv


async def test_el_snapshot_admite_respuestas_de_mas_de_20_mb_con_el_tope_real():
    config = load_pipeline_config()
    assert config.sources.exoplanet_archive.snapshot.max_response_bytes > 21 * 1024 * 1024
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=_BIG_BODY))
    async with httpx.AsyncClient(transport=transport) as http:
        source = cli.archive_snapshot_source_from_config(http, config)
        # Pasa el tope de tamano; falla despues, al mapear filas sin releasedate.
        with pytest.raises(ExoplanetArchiveUnavailable) as exc:
            await source.all_solutions()
    assert "supera el tope" not in str(exc.value)


async def test_el_cliente_del_catalogo_de_t74_conserva_su_tope_de_20_mb():
    config = load_pipeline_config()
    transport = httpx.MockTransport(lambda r: httpx.Response(200, content=_BIG_BODY))
    async with httpx.AsyncClient(transport=transport) as http:
        _catalog, client = cli.exoplanet_catalog_from_config(http, config)
        with pytest.raises(ExoplanetArchiveUnavailable, match=str(20 * 1024 * 1024)):
            await client.query_csv("select pl_name from pscomppars")


async def test_el_cliente_del_snapshot_usa_su_propio_techo_de_peticiones(monkeypatch):
    real_limiter = cli.RateLimiter

    async def _instant(_s: float) -> None:
        return None

    # El RateLimiter real espera 2 s entre peticiones: se hace instantaneo.
    monkeypatch.setattr(cli, "RateLimiter", lambda s, **kw: real_limiter(s, sleep=_instant))
    config = _real_config(max_requests=3)
    transport = httpx.MockTransport(lambda r: httpx.Response(200, text="pl_name\n"))
    async with httpx.AsyncClient(transport=transport) as http:
        source = cli.archive_snapshot_source_from_config(http, config)
        for _ in range(3):
            await source.default_solutions()
        with pytest.raises(ArchiveRequestCapReached):
            await source.default_solutions()
