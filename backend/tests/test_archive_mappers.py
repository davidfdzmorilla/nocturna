"""Mappers de T81 (`archive_solution_from_ps_row`, `reference_key`) y cliente."""

from datetime import date

import httpx
import pytest
from helpers.archive import ALIAS_URL, TAP_URL, FakeSleep, fixture_rows

from nocturna.domain.archive import ArchiveSolution
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.exoplanet_archive import client as client_module
from nocturna.infrastructure.exoplanet_archive.client import (
    ArchiveHttpClient,
    ExoplanetArchiveUnavailable,
    adql_string,
)
from nocturna.infrastructure.exoplanet_archive.mappers import (
    archive_solution_from_ps_row,
    reference_key,
)

PLANETS = "ps_t81_planets.csv"
RELEASED = "ps_t81_released_since_7d.csv"
BASE = fixture_rows(PLANETS)[0]


def row(**kw):
    r = dict(BASE)
    r.update(kw)
    return r


# ---------------------------------------------------------------- filas reales


@pytest.mark.parametrize("name,n", [(PLANETS, 15), (RELEASED, 32)])
def test_todas_las_filas_reales_mapean(name, n):
    rows = fixture_rows(name)
    assert len(rows) == n
    sols = [archive_solution_from_ps_row(r) for r in rows]
    assert all(isinstance(x, ArchiveSolution) for x in sols)
    assert all(isinstance(x.releasedate, date) for x in sols)


def test_primera_fila_real_da_la_clave_dorada():
    s = archive_solution_from_ps_row(BASE)
    assert s.solution_key == "7c8e61d2ce6b1a783db30f2778e516b6c0f638f5f0606527f013c05880e1db9a"
    assert s.releasedate == date(2019, 6, 27)
    assert s.pl_pubdate == "2019-08"
    assert s.ttv_flag is True and s.pl_controv_flag is False
    assert s.is_default is False
    assert s.mass.value is None and s.mass.lim is None
    assert s.radius.lim == 0 and s.period.value == 24.13861
    assert s.ref_key == "2019AJ....158...79D"
    assert s.ref_text == "David et al. 2019"
    assert s.arxiv_id is None


def test_filas_de_preprint_informan_arxiv_id():
    sols = [archive_solution_from_ps_row(r) for r in fixture_rows(RELEASED)]
    arx = [s for s in sols if s.arxiv_id]
    assert arx
    assert all("arXiv" in s.pl_refname for s in arx)
    assert all(len(s.arxiv_id.split(".")) == 2 for s in arx)


def test_releasedate_con_hora_se_trunca():
    assert archive_solution_from_ps_row(row(releasedate="2019-06-27 10:30:00")).releasedate == date(
        2019, 6, 27
    )
    assert archive_solution_from_ps_row(row(releasedate="2019-06-27T10:30")).releasedate == date(
        2019, 6, 27
    )


def test_nulos_a_none_y_flags_nulos_permitidos():
    s = archive_solution_from_ps_row(
        row(
            ttv_flag=None,
            pl_controv_flag=None,
            pl_pubdate=None,
            soltype=None,
            st_mass=None,
            discoverymethod=None,
        )
    )
    assert s.ttv_flag is None and s.pl_controv_flag is None
    assert s.pl_pubdate is None and s.soltype is None and s.discoverymethod is None
    assert s.st_mass.value is None


@pytest.mark.parametrize(
    "bad",
    [
        {"pl_orbper": "abc"},
        {"pl_rade": "inf"},
        {"pl_rade": "nan"},
        {"pl_bmasseerr1": "-inf"},
        {"st_rad": "nan"},
        {"pl_radelim": "2"},
        {"pl_orbperlim": "-2"},
        {"pl_bmasselim": "x"},
        {"default_flag": None},
        {"default_flag": "2"},
        {"default_flag": "x"},
        {"ttv_flag": "3"},
        {"pl_controv_flag": "yes"},
        {"releasedate": None},
        {"releasedate": "2019/06/27"},
        {"releasedate": "2019-13-45"},
        {"releasedate": "19-06-27"},
        {"releasedate": "2019-06-27x10"},
        {"pl_name": None},
    ],
)
def test_datos_invalidos_lanzan_unavailable(bad):
    with pytest.raises(ExoplanetArchiveUnavailable):
        archive_solution_from_ps_row(row(**bad))


@pytest.mark.parametrize("flag,expected", [("1", True), ("0", False)])
def test_default_flag_a_bool(flag, expected):
    assert archive_solution_from_ps_row(row(default_flag=flag)).is_default is expected


# --------------------------------------------------------------- reference_key


def test_reference_key_con_bibcode():
    r = (
        "<a refstr=X href=https://ui.adsabs.harvard.edu/abs/"
        "2019AJ....158...79D/abstract target=ref>x</a>"
    )
    assert reference_key(r) == "2019AJ....158...79D"


def test_reference_key_decodifica_porcentaje_26():
    r = (
        "<a refstr=X href=https://ui.adsabs.harvard.edu/abs/"
        "2019A%26A...621A..86B/abstract target=ref>x</a>"
    )
    assert reference_key(r) == "2019A&A...621A..86B"


def test_reference_key_sin_bibcode_es_el_texto_normalizado():
    assert reference_key("<a refstr=X>  Smith   et al.\n 2020 </a>") == "Smith et al. 2020"
    assert reference_key("") == ""


def test_reference_key_en_filas_reales_nunca_es_html():
    for name in (PLANETS, RELEASED):
        for r in fixture_rows(name):
            k = reference_key(r["pl_refname"])
            assert k and "<" not in k and " " not in k


# ---------------------------------------------------------------------- cliente


def _client(handler, *, max_response_bytes=None):
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    limiter = RateLimiter(2.0, sleep=FakeSleep(), monotonic=lambda: 0.0)
    return http, ArchiveHttpClient(
        http,
        tap_url=TAP_URL,
        alias_url=ALIAS_URL,
        limiter=limiter,
        request_timeout_s=30.0,
        max_requests=10,
        max_response_bytes=max_response_bytes,
    )


@pytest.mark.anyio
async def test_max_response_bytes_configurable_se_aplica():
    http, client = _client(
        lambda r: httpx.Response(200, content=b"a\n" + b"x" * 50), max_response_bytes=10
    )
    async with http:
        with pytest.raises(ExoplanetArchiveUnavailable, match="tope de 10"):
            await client.query_csv("q")


@pytest.mark.anyio
async def test_max_response_bytes_mayor_que_el_modulo_permite_respuestas_grandes(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 10)
    http, client = _client(
        lambda r: httpx.Response(200, content=b"a\n" + b"x" * 50), max_response_bytes=1000
    )
    async with http:
        assert len(await client.query_csv("q")) == 1


@pytest.mark.anyio
async def test_con_none_se_usa_el_tope_del_modulo(monkeypatch):
    monkeypatch.setattr(client_module, "MAX_RESPONSE_BYTES", 10)
    http, client = _client(lambda r: httpx.Response(200, content=b"a\n" + b"x" * 50))
    async with http:
        with pytest.raises(ExoplanetArchiveUnavailable, match="tope de 10"):
            await client.query_csv("q")


def test_adql_string_escapa_comillas():
    assert adql_string("abc") == "'abc'"
    assert adql_string("O'Neil") == "'O''Neil'"
    assert adql_string("'") == "''''"
