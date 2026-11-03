"""T100 (D2): `states_minimum_mass` sobre los abstracts reales de la base (fixtures)."""

import json
from pathlib import Path

import pytest

from nocturna.domain.entities import (
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
)
from nocturna.domain.minimum_mass import states_minimum_mass

FIXTURE = Path(__file__).parent / "fixtures" / "t100" / "abstracts.json"
_PAPERS = {p["external_id"]: p for p in json.loads(FIXTURE.read_text(encoding="utf-8"))}


def _measurement(raw: dict) -> Measurement:
    return Measurement(
        planet_name=raw["planet_name"],
        parameter=MeasuredParameter(raw["parameter"]),
        value=raw["value"],
        err_plus=raw["err_plus"],
        err_minus=raw["err_minus"],
        unit=MeasurementUnit(raw["unit"]),
        limit=MeasurementLimit(raw["limit"]),
        origin=MeasurementOrigin(raw["origin"]),
        evidence=raw["evidence"],
    )


def _real(paper: str, planet: str, parameter: str = "mass") -> tuple[Measurement, str]:
    data = _PAPERS[paper]
    (raw,) = [
        m
        for m in data["measurements"]
        if m["planet_name"] == planet and m["parameter"] == parameter
    ]
    return _measurement(raw), data["abstract"]


def test_toi_2158_c_m_p_sin_i_del_abstract_real() -> None:
    """El Reader no la extrajo, pero la cita literal del abstract real la delata."""
    abstract = _PAPERS["2609.30915"]["abstract"]
    evidence = r"TOI-2158 c ($M_p \sin i = 1.62\pm0.13$ M$_{\rm Jup}$"
    assert evidence in abstract
    measurement = _mass(evidence)
    assert states_minimum_mass(measurement, abstract) is True


@pytest.mark.parametrize(
    ("paper", "planet", "expected"),
    [
        ("2610.08922", "TOI-2211 c", True),  # Msin(i)
        ("2610.08922", "TOI-2211 b", False),
        ("2609.30915", "TOI-5120 b", False),
        ("2609.30915", "TOI-5699 b", False),
        ("2609.30915", "TOI-2158 b", False),
        # `sin i` en la frase anterior, pero son masas verdaderas por astrometría.
        ("2610.02783", "HD 65216 b", False),
        ("2610.02783", "HD 65216 c", False),
    ],
)
def test_abstracts_reales(paper: str, planet: str, expected: bool) -> None:
    measurement, abstract = _real(paper, planet)
    assert states_minimum_mass(measurement, abstract) is expected


def test_radio_nunca() -> None:
    radius, abstract = _real("2609.30915", "TOI-5120 b", "radius")
    assert states_minimum_mass(radius, abstract) is False


def _mass(evidence: str, parameter: MeasuredParameter = MeasuredParameter.MASS) -> Measurement:
    unit = MeasurementUnit.M_EARTH if parameter == MeasuredParameter.MASS else MeasurementUnit.DAY
    return Measurement(
        planet_name="X b",
        parameter=parameter,
        value=8.5,
        err_plus=1.0,
        err_minus=1.0,
        unit=unit,
        limit=MeasurementLimit.NONE,
        origin=MeasurementOrigin.THIS_WORK,
        evidence=evidence,
    )


def test_periodo_nunca() -> None:
    measurement = _mass("Msin(i) period 8.5 d", MeasuredParameter.PERIOD)
    assert states_minimum_mass(measurement, "Msin(i) period 8.5 d") is False


@pytest.mark.parametrize(
    "evidence",
    [
        "Msin(i) = 8.5 +- 1.0 M_earth",
        r"$M_p \sin i = 8.5 \pm 1.0$ M$_\oplus$",
        r"$M_{\rm p}\sin i = 8.5 \pm 1.0$ M$_\oplus$",
        "msini = 8.5 +- 1.0 M_earth",
        "M sin i of 8.5 +- 1.0 M_earth",
        "a minimum mass of 8.5 +- 1.0 M_earth",
        "minimum masses of 8.5 +- 1.0 M_earth",
        "a projected mass of 8.5 +- 1.0 M_earth",
    ],
)
def test_variantes_sinteticas_en_la_evidencia(evidence: str) -> None:
    assert states_minimum_mass(_mass(evidence), f"We find X b with {evidence}.") is True


def test_contexto_izquierdo_dentro_de_la_clausula() -> None:
    abstract = "We detect X b with a minimum mass of 8.5 +- 1.0 M_earth in the data."
    assert states_minimum_mass(_mass("8.5 +- 1.0 M_earth"), abstract) is True


def test_el_contexto_no_cruza_un_limite_de_clausula() -> None:
    abstract = "Planet Y has a minimum mass of 3 M_earth, whereas X b has 8.5 +- 1.0 M_earth."
    assert states_minimum_mass(_mass("8.5 +- 1.0 M_earth"), abstract) is False


def test_el_contexto_tiene_como_maximo_80_caracteres() -> None:
    filler = "a" * 100
    abstract = f"minimum mass {filler} 8.5 +- 1.0 M_earth"
    assert states_minimum_mass(_mass("8.5 +- 1.0 M_earth"), abstract) is False


def test_si_la_evidencia_no_esta_en_el_abstract_solo_cuenta_la_evidencia() -> None:
    assert states_minimum_mass(_mass("8.5 M_earth"), "minimum mass of X b is unknown") is False
    assert states_minimum_mass(_mass("Msin(i) = 8.5 M_earth"), "otro texto") is True


def test_palabras_con_sin_no_son_masa_minima() -> None:
    abstract = "Using in situ data we measure X b at 8.5 +- 1.0 M_earth"
    assert states_minimum_mass(_mass("8.5 +- 1.0 M_earth"), abstract) is False


@pytest.mark.parametrize("planet", ["HD 65216 b", "HD 65216 c"])
def test_hd_65216_con_sin_i_legible_en_la_clausula_anterior_sigue_siendo_masa_verdadera(
    planet: str,
) -> None:
    """El TeX real (`sin$i\\sim$0.5`) se normaliza a «sinisim» y no casa por sí solo; esta
    variante deja `sin i` legible en la cláusula anterior y exige el límite de cláusula."""
    measurement, abstract = _real("2610.02783", planet)
    legible = abstract.replace(r"sin$i\sim$0.5", "sin i = 0.5")
    assert legible != abstract and "sin i = 0.5, producing true masses" in legible
    assert states_minimum_mass(measurement, legible) is False


@pytest.mark.parametrize(
    ("abstract", "evidence"),
    [
        (r"We measure $M_p\sin i_p = 5.0$ M$_\oplus$ for X b.", r"5.0$ M$_\oplus$"),
        (r"We measure $m\,\sin\,i=5.0$ M$_\oplus$ for X b.", r"5.0$ M$_\oplus$"),
        (r"We measure $M\sin i_{\rm b} = 5.0$ M$_\oplus$ for X b.", r"5.0$ M$_\oplus$"),
        (r"We measure $M\sin i_b = 5.0$ M$_\oplus$ for X b.", r"5.0$ M$_\oplus$"),
        (r"We measure $M_p\,\sin i = 5.0$ M$_\oplus$ for X b.", r"5.0$ M$_\oplus$"),
        ("We find a minimum-mass of 5.0 M_E for X b.", "5.0 M_E"),
        ("We find a minimum mass of 5.0 M_E for X b.", "5.0 M_E"),
    ],
)
def test_tex_real_de_arxiv_es_masa_minima(abstract: str, evidence: str) -> None:
    assert evidence in abstract
    assert states_minimum_mass(_mass(evidence), abstract) is True


def test_coma_dentro_de_una_lista_de_numeros_no_corta_la_clausula() -> None:
    abstract = "We find minimum masses of 3.1, 5.0 and 7 M_E for the planets."
    assert states_minimum_mass(_mass("5.0 and 7 M_E"), abstract) is True


def test_coma_antes_de_una_aposicion_numerica_no_corta_la_clausula() -> None:
    abstract = "The minimum mass of planet b, 5.0 M_E, is well constrained."
    assert states_minimum_mass(_mass("5.0 M_E"), abstract) is True


def test_coma_seguida_de_signo_o_dolar_cifra_no_corta() -> None:
    assert states_minimum_mass(_mass("5.0 M_E"), "Minimum masses of -1, +5.0 M_E here.") is True
    assert states_minimum_mass(_mass("$5.0$ M_E"), "Minimum masses of 3, $5.0$ M_E here.") is True


def test_coma_seguida_de_palabra_sigue_cortando_la_clausula() -> None:
    abstract = "The signal has sin i = 0.5, producing true masses of 2.23 M_E for X b."
    assert states_minimum_mass(_mass("2.23 M_E"), abstract) is False
