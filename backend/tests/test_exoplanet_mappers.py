"""Mappers puros del Exoplanet Archive (T74) sobre filas REALES grabadas."""

import pytest
from helpers.archive import fixture_rows

from nocturna.domain.entities import MeasuredParameter, MeasurementLimit, MeasurementUnit
from nocturna.infrastructure.exoplanet_archive.client import ExoplanetArchiveUnavailable
from nocturna.infrastructure.exoplanet_archive.mappers import (
    arxiv_id_from_refname,
    planet_overview_url,
    reference_text,
    solutions_from_ps_rows,
)

MASS = MeasuredParameter.MASS
RADIUS = MeasuredParameter.RADIUS
PERIOD = MeasuredParameter.PERIOD


def _row(**overrides: str | None) -> dict[str, str | None]:
    row: dict[str, str | None] = {
        "pl_name": "Foo b",
        "default_flag": "0",
        "pl_refname": (
            "<a href=https://ui.adsabs.harvard.edu/abs/2020AJ....1..1X/abstract> X et al. 2020 </a>"
        ),
        "pl_bmassprov": "Mass",
        "pl_bmasse": "10.0",
        "pl_bmasseerr1": "2.0",
        "pl_bmasseerr2": "-3.0",
        "pl_bmasselim": "0",
        "pl_rade": "2.0",
        "pl_radeerr1": "0.1",
        "pl_radeerr2": "-0.1",
        "pl_radelim": "0",
        "pl_orbper": "5.0",
        "pl_orbpererr1": "0.01",
        "pl_orbpererr2": "-0.01",
        "pl_orbperlim": "0",
    }
    row.update(overrides)
    return row


# --- errores y valores ------------------------------------------------------


def test_errores_asimetricos_se_guardan_en_valor_absoluto():
    (sol,) = solutions_from_ps_rows([_row()], MASS)

    assert sol.value == 10.0
    assert sol.err_plus == 2.0
    assert sol.err_minus == 3.0
    assert sol.unit == MeasurementUnit.M_EARTH


def test_errores_simetricos_en_valor_absoluto():
    rows = fixture_rows("ps_v1298tau.csv")
    livingston = [r for r in rows if r["pl_name"] == "V1298 Tau b" and r["default_flag"] == "1"]

    (sol,) = solutions_from_ps_rows(livingston, MASS)

    assert (sol.value, sol.err_plus, sol.err_minus) == (13.1, 5.3, 5.3)


def test_un_error_vacio_queda_none():
    (sol,) = solutions_from_ps_rows([_row(pl_radeerr1=None, pl_radeerr2=None)], RADIUS)

    assert sol.err_plus is None and sol.err_minus is None
    assert sol.usable_as_prior is False


def test_valor_vacio_se_omite():
    assert solutions_from_ps_rows([_row(pl_rade=None)], RADIUS) == ()


def test_valor_cero_se_omite():
    assert solutions_from_ps_rows([_row(pl_rade="0")], RADIUS) == ()
    assert solutions_from_ps_rows([_row(pl_rade="0.0000")], RADIUS) == ()


@pytest.mark.parametrize(
    ("flag", "expected"),
    [("1", MeasurementLimit.UPPER), ("-1", MeasurementLimit.LOWER), ("0", MeasurementLimit.NONE)],
)
def test_la_bandera_lim_se_traduce(flag, expected):
    (sol,) = solutions_from_ps_rows([_row(pl_bmasselim=flag)], MASS)

    assert sol.limit == expected


@pytest.mark.parametrize("prov", [None, "Msini", "Msin(i)/sin(i)"])
def test_masa_con_procedencia_distinta_de_mass_no_da_solucion(prov):
    assert solutions_from_ps_rows([_row(pl_bmassprov=prov)], MASS) == ()


def test_la_procedencia_de_la_masa_no_afecta_a_radio_ni_periodo():
    row = _row(pl_bmassprov="Msini")

    assert len(solutions_from_ps_rows([row], RADIUS)) == 1
    assert len(solutions_from_ps_rows([row], PERIOD)) == 1


def test_default_flag_solo_1_es_default():
    sols = solutions_from_ps_rows([_row(default_flag="1"), _row(default_flag="0")], RADIUS)

    assert [s.is_default for s in sols] == [True, False]


def test_filas_reales_de_v1298_tau_b_masa_radio_periodo():
    rows = [r for r in fixture_rows("ps_v1298tau.csv") if r["pl_name"] == "V1298 Tau b"]

    assert len(solutions_from_ps_rows(rows, MASS)) == 2  # Suárez Mascareño y Livingston
    assert len(solutions_from_ps_rows(rows, RADIUS)) == 5  # Johnson no mide radio
    assert len(solutions_from_ps_rows(rows, PERIOD)) == 6
    defaults = [s for s in solutions_from_ps_rows(rows, PERIOD) if s.is_default]
    assert len(defaults) == 1 and defaults[0].reference == "Livingston et al. 2026"
    assert defaults[0].arxiv_id is None  # revista, no preprint


def test_errores_asimetricos_reales_de_finociety():
    rows = [r for r in fixture_rows("ps_v1298tau.csv") if "Finociety" in (r["pl_refname"] or "")]

    (sol,) = solutions_from_ps_rows(rows, MASS)

    assert (sol.err_plus, sol.err_minus) == (111.23994245, 82.63538582)


# --- filas malformadas ------------------------------------------------------


def test_valor_no_numerico_lanza_unavailable():
    with pytest.raises(ExoplanetArchiveUnavailable, match="pl_rade"):
        solutions_from_ps_rows([_row(pl_rade="abc")], RADIUS)


def test_error_no_numerico_lanza_unavailable():
    with pytest.raises(ExoplanetArchiveUnavailable, match="pl_radeerr1"):
        solutions_from_ps_rows([_row(pl_radeerr1="x")], RADIUS)


@pytest.mark.parametrize("flag", ["2", "-2", "abc"])
def test_lim_fuera_de_rango_lanza_unavailable(flag):
    with pytest.raises(ExoplanetArchiveUnavailable, match="pl_bmasselim"):
        solutions_from_ps_rows([_row(pl_bmasselim=flag)], MASS)


# --- referencias ------------------------------------------------------------


def test_reference_text_decodifica_entidades_html():
    rows = [
        r for r in fixture_rows("ps_v1298tau.csv") if "Su&aacute;rez" in (r["pl_refname"] or "")
    ]

    assert reference_text(rows[0]["pl_refname"]) == "Suárez Mascareño et al. 2022"


def test_reference_text_recorta_espacios_de_los_extremos():
    rows = fixture_rows("ps_refname_arxiv_top10.csv")

    assert reference_text(rows[0]["pl_refname"]) == "Faedi et al. 2011"


def test_solucion_sin_referencia_usa_marcador_en_lugar_de_fallar():
    (sol,) = solutions_from_ps_rows([_row(pl_refname=None)], RADIUS)

    assert sol.reference == "(sin referencia)"


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


def test_el_arxiv_id_llega_a_la_solucion():
    rows = [
        {**_row(), "pl_refname": r["pl_refname"]}
        for r in fixture_rows("ps_refname_arxiv_top10.csv")[7:8]
    ]

    (sol,) = solutions_from_ps_rows(rows, RADIUS)

    assert sol.arxiv_id == "1509.07750"
    assert sol.reference == "Neveu-VanMalle et al. 2015"


# --- enlace ------------------------------------------------------------------


def test_planet_overview_url_codifica_espacios():
    assert (
        planet_overview_url("V1298 Tau b")
        == "https://exoplanetarchive.ipac.caltech.edu/overview/V1298%20Tau%20b"
    )


def test_planet_overview_url_codifica_el_signo_mas():
    assert planet_overview_url("Foo+1 b").endswith("/overview/Foo%2B1%20b")
