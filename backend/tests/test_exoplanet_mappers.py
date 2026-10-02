"""Mappers puros del Exoplanet Archive (T74) sobre filas REALES grabadas."""

import pytest
from helpers.archive import fixture_rows

from nocturna.infrastructure.exoplanet_archive.mappers import (
    arxiv_id_from_refname,
    planet_overview_url,
    reference_text,
)

# --- referencias ------------------------------------------------------------


def test_reference_text_decodifica_entidades_html():
    rows = [
        r for r in fixture_rows("ps_v1298tau.csv") if "Su&aacute;rez" in (r["pl_refname"] or "")
    ]

    assert reference_text(rows[0]["pl_refname"]) == "Suárez Mascareño et al. 2022"


def test_reference_text_recorta_espacios_de_los_extremos():
    rows = fixture_rows("ps_refname_arxiv_top10.csv")

    assert reference_text(rows[0]["pl_refname"]) == "Faedi et al. 2011"


@pytest.mark.parametrize(
    ("refname", "expected"),
    [
        ("<a href=https://ui.adsabs.harvard.edu/abs/2015arXiv150907750N/abstract>", "1509.07750"),
        ("<a href=https://ui.adsabs.harvard.edu/abs/2011arXiv1102.1375F/abstract>", "1102.1375"),
        ("<a href=https://ui.adsabs.harvard.edu/abs/2026Natur.649..310L/abstract>", None),
        ("", None),
    ],
)
def test_arxiv_id_from_refname(refname, expected):
    assert arxiv_id_from_refname(refname) == expected


def test_arxiv_id_sobre_las_diez_filas_reales_del_fixture():
    rows = fixture_rows("ps_refname_arxiv_top10.csv")

    ids = [arxiv_id_from_refname(r["pl_refname"]) for r in rows]

    assert ids == [
        "1102.1375",
        "1103.3825",
        "1104.2823",
        "1104.5230",
        "1106.1212",
        "1103.1813",
        "1008.3096",
        "1509.07750",
        "1509.07750",
        "1508.02411",
    ]


# --- enlace ------------------------------------------------------------------


def test_planet_overview_url_codifica_espacios():
    assert (
        planet_overview_url("V1298 Tau b")
        == "https://exoplanetarchive.ipac.caltech.edu/overview/V1298%20Tau%20b"
    )


def test_planet_overview_url_codifica_el_signo_mas():
    assert planet_overview_url("Foo+1 b").endswith("/overview/Foo%2B1%20b")
