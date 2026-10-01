"""Tests de `ExoplanetFilter` (T79) con los patrones REALES de `config/pipeline.toml`.

Dominio puro: sin base de datos, sin red, sin Claude. Los patrones no se
copian aquí: se cargan con `load_pipeline_config`, así que si el autor cambia
la lista y rompe un caso, este fichero lo ve.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nocturna.domain.errors import InvariantViolation
from nocturna.domain.exoplanet_filter import ExoplanetFilter, normalize_text
from nocturna.infrastructure.config import load_pipeline_config

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PIPELINE_TOML = REPO_ROOT / "config" / "pipeline.toml"
FIXTURE = Path(__file__).parent / "fixtures" / "t79" / "ep_items.json"

# Lo que casa con la lista v2 (2026-10-01) entre los `exoplanet_general`. 2609.34523
# (disco de TW Hya) dejó de casar a propósito. La etiqueta esperada la decide el
# autor (`expected_match: null`). Si la lista cambia y esto cambia, el test
# avisa para revisarlo a mano.
FROZEN_GENERAL_MATCHES = frozenset({"2609.35676", "2609.35721"})

# Cero falsos negativos obligatorios: V1298, TOI-6981 b y los otros dos.
MANDATORY_NO_FN = ("2609.30038", "2609.37597", "2609.20748", "2609.26894")


@pytest.fixture(scope="module")
def real_filter() -> ExoplanetFilter:
    config = load_pipeline_config(REAL_PIPELINE_TOML)
    return ExoplanetFilter(
        keywords=tuple(config.exoplanet_filter.keywords),
        designation_patterns=tuple(config.exoplanet_filter.designation_patterns),
    )


def _in_abstract(designation: str) -> tuple[str, str]:
    return ("A neutral title", f"We study {designation} in this work.")


# --- keywords ---------------------------------------------------------------


@pytest.mark.parametrize("word", ["exoplanet", "Exoplanet", "EXOPLANETS", "exoplanets"])
def test_keyword_casa_sin_distinguir_mayusculas(real_filter, word):
    assert real_filter.matches("A neutral title", f"A study of {word} atmospheres.")


@pytest.mark.parametrize("word", ["exoplanetology", "exoplanetary", "preexoplanet"])
def test_keyword_respeta_limite_de_palabra(real_filter, word):
    assert not real_filter.matches("A neutral title", f"A study of {word} atmospheres.")


def test_casa_solo_por_titulo(real_filter):
    assert real_filter.matches("A new exoplanet survey", "Nothing relevant here.")


def test_casa_solo_por_abstract(real_filter):
    assert real_filter.matches("Nothing relevant here", "We discuss an exoplanet.")


def test_ni_titulo_ni_abstract_con_exoplaneta_no_casa(real_filter):
    assert not real_filter.matches("Galaxy clusters", "We study the halo mass function.")


# --- designaciones que DEBEN casar -------------------------------------------


@pytest.mark.parametrize(
    "designation",
    [
        "TOI-6981 b",
        "TOI 6981b",
        "WASP-12b",
        "Kepler-452b",
        "HAT-P-7b",
        "K2-18 b",
        "TOI-700",
        "TOI-1230.01",
        "V1298 Tau b",
        "the V1298 Tau system",
        "AU Mic b",
        "DS Tuc A b",
        "51 Peg b",
        "51 Pegasi b",
        "tau Bootis b",
        "61 Cygni",
        "TWA 7 b",
        "RX J0534.0-0221 b",
        "TRAPPIST-1 e",
        "Proxima Cen b",
        "Proxima Centauri b",
        "PDS 70 b",
        "PDS 70 c",
        # Varias letras ascendentes juntas (autor, 2026-10-01; 2609.35979).
        "HIP 67522 bc",
        "TOI-700 bcd",
        "AU Mic bc",
        "tau Bootis bc",
        "RX J0534.0-0221 bc",
        "Kepler-90 d",
        "Kepler-11 d",
        "Gliese 581 g",
        "L 98-59 b",
        "LTT 1445A b",
        "Ross 128 b",
        "HR 8799 b",
        "HD189733b",
        "GJ 1214 b",
        "HD 189733 b",
        "HD~189733~b",
        "HD\\,189733 b",
        "V1298,Tau b",
    ],
)
def test_designacion_casa(real_filter, designation):
    title, abstract = _in_abstract(designation)
    assert real_filter.matches(title, abstract), designation


# --- designaciones que NO deben casar ----------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "RR Lyrae stars",
        "RR Lyr",
        "BL Lac objects",
        "BL Lacertae",
        "AM CVn",
        "RS CVn",
        "SS Cyg",
        "SW Sex stars",
        "RV Tauri stars",
        "ZZ Ceti",
        "UX Ori",
        "FU Ori",
        "FU Orionis",
        "TW Hya disk",
        "HL Tau rings",
        "eta Carinae",
        "delta Scuti",
        "gamma Doradus",
        # Correcciones (a) y (b) de la revisión 2 (autor, 2026-10-01).
        "Kepler 4-year baseline",
        "the Kepler 4 yr data",
        "Kepler 10 years of data",
        "K2 2 campaigns",
        "NGTS 2 years",
        "WASP 2 days",
        "TWA 7 disk",
        "PDS 110 dipper",
        "PDS 70 disk",
        # Letras no ascendentes o palabras hechas de letras de planeta.
        "HD 1234 bed",
        "HD 1234 cb",
        "HD 1234 fed",
        "Figure 3 bc",
        "beta Cephei",
        "rho Ophiuchi cloud",
        "Sigma Orionis cluster",
        "the CO Ser region",
        "In SN Per",
        "GC Sgr A*",
        "Kepler 2nd law",
        "K2 mission",
        "Kepler mission",
        "HR 4796A",
        "HD 189733 A",
        "Figure 3 b",
        "Table 2 c",
        "in And and Mon",
        "HD 189733",
        "Section 4 b",
    ],
)
def test_designacion_no_casa(real_filter, text):
    title, abstract = _in_abstract(text)
    assert not real_filter.matches(title, abstract), text


# --- normalización ----------------------------------------------------------


def test_normalize_text_unifica_separadores_y_guiones_unicode():
    assert normalize_text("HD~189733~b") == "HD 189733 b"
    assert normalize_text("HD\\,189733 b") == "HD 189733 b"
    assert normalize_text("V1298,Tau b") == "V1298 Tau b"
    assert normalize_text("TOI–6981   b") == "TOI-6981 b"
    # una coma entre palabras con espacio no se toca
    assert normalize_text("a, b") == "a, b"


# --- construcción ------------------------------------------------------------


def test_patron_invalido_lanza_invariant_violation():
    with pytest.raises(InvariantViolation):
        ExoplanetFilter(keywords=(), designation_patterns=("(sin cerrar",))


def test_listas_vacias_nunca_casan():
    empty = ExoplanetFilter(keywords=(), designation_patterns=())
    assert not empty.matches("exoplanet TOI-6981 b", "HD 189733 b and exoplanets")


def test_keyword_vacia_no_casa_con_todo():
    only_empty = ExoplanetFilter(keywords=("",), designation_patterns=())
    assert not only_empty.matches("Galaxy clusters", "Halo mass function.")


def test_solo_designaciones_sin_keywords_casa_por_patron():
    f = ExoplanetFilter(keywords=(), designation_patterns=(r"\bTOI-\d+",))
    assert f.matches("TOI-1 is a star", "")
    assert not f.matches("An exoplanet", "")


# --- fixture real (ep_items.json) --------------------------------------------


def _load_items() -> list[dict]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


_ITEMS = _load_items()


@pytest.mark.parametrize("item", _ITEMS, ids=[i["external_id"] for i in _ITEMS])
def test_fixture_real_cumple_expected_match(real_filter, item):
    got = real_filter.matches(item["title"], item["abstract"])
    expected = item["expected_match"]
    if expected is True:
        assert got, f"falso negativo: {item['external_id']} ({item['label']})"
    elif expected is False:
        assert not got, f"falso positivo: {item['external_id']} ({item['label']})"
    else:
        # exoplanet_general: se congela el resultado actual
        assert got == (item["external_id"] in FROZEN_GENERAL_MATCHES), (
            f"cambio de resultado en exoplanet_general {item['external_id']}: revisar"
        )


def test_fixture_cero_falsos_negativos_obligatorios(real_filter):
    by_id = {i["external_id"]: i for i in _ITEMS}
    for external_id in MANDATORY_NO_FN:
        item = by_id[external_id]
        assert item["expected_match"] is True, external_id
        assert real_filter.matches(item["title"], item["abstract"]), (
            f"falso negativo obligatorio: {external_id}"
        )


def test_fixture_los_general_que_casan_son_exactamente_los_congelados(real_filter):
    matching = {
        i["external_id"]
        for i in _ITEMS
        if i["expected_match"] is None and real_filter.matches(i["title"], i["abstract"])
    }
    assert matching == FROZEN_GENERAL_MATCHES
