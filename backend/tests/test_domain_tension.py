"""Tests puros de `domain/catalog.py` y `domain/tension.py` (T73).

Sin base de datos, sin red, sin Claude. Valores esperados calculados a mano:
sigma = |x_p - x_i| / sqrt(e_p^2 + e_i^2), eligiendo el lado del error segun
la posicion relativa del valor del paper respecto a la previa.

- V1298 b, 0.52(+0.12/-0.14) vs Livingston 0.041+-0.017:
  0.479 / sqrt(0.14^2 + 0.017^2) = 3.39648
- V1298 b, 0.67+-0.16 vs Livingston: 0.629 / sqrt(0.16^2 + 0.017^2) = 3.90925
- V1298 b, 0.52(+0.12/-0.14) vs Suarez Mascareño 0.64+-0.19 (paper por debajo):
  0.12 / sqrt(0.12^2 + 0.19^2) = 0.53399
- V1298 e vs 0.048+-0.013: 0.40(-0.13) -> 2.69425; 0.66(-0.18) -> 3.39117;
  0.71(-0.20) -> 3.30303; minimo 2.69425.
"""

import dataclasses
import math

import pytest
from helpers.exoplanet import (
    M_EARTH,
    MASS,
    RADIUS,
    make_item,
    make_measurement,
    make_solution,
)

from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.entities import (
    UNITS_BY_PARAMETER,
    MeasuredParameter,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.tension import (
    CANONICAL_UNIT,
    M_JUP_IN_M_EARTH,
    R_JUP_IN_R_EARTH,
    CatalogComparison,
    TensionResult,
    compare,
    to_canonical,
)

# ---------- conversion de unidades ----------


def test_constantes_de_conversion():
    assert M_JUP_IN_M_EARTH == 317.83
    assert R_JUP_IN_R_EARTH == 11.209


def test_unidades_canonicas():
    assert CANONICAL_UNIT[MeasuredParameter.MASS] == MeasurementUnit.M_EARTH
    assert CANONICAL_UNIT[MeasuredParameter.RADIUS] == MeasurementUnit.R_EARTH
    assert CANONICAL_UNIT[MeasuredParameter.PERIOD] == MeasurementUnit.DAY


def test_to_canonical_masa_jupiter_a_tierra():
    assert to_canonical(2.0, MeasurementUnit.M_JUP) == pytest.approx(635.66)


def test_to_canonical_radio_jupiter_a_tierra():
    assert to_canonical(2.0, MeasurementUnit.R_JUP) == pytest.approx(22.418)


@pytest.mark.parametrize(
    "unit", [MeasurementUnit.M_EARTH, MeasurementUnit.R_EARTH, MeasurementUnit.DAY]
)
def test_to_canonical_identidad_para_unidades_ya_canonicas(unit):
    assert to_canonical(4.2, unit) == 4.2


def test_units_by_parameter_es_publica():
    assert MeasurementUnit.M_JUP in UNITS_BY_PARAMETER[MeasuredParameter.MASS]


# ---------- compare ----------


def test_compare_sin_tension_valores_identicos_da_sigma_cero():
    paper = make_measurement(5.02, 0.75, 0.75, planet_name="TOI-2109 b")
    prior = make_solution(5.02, 0.75, 0.75, planet_name="TOI-2109 b", is_default=True)

    comparison = compare(paper, prior)

    assert isinstance(comparison, CatalogComparison)
    assert comparison.sigma == 0.0
    assert comparison.paper is paper
    assert comparison.prior is prior


def test_compare_v1298_b_contra_livingston_asimetrico():
    paper = make_measurement(0.52, 0.12, 0.14)
    prior = make_solution(0.041, 0.017, 0.017, is_default=True)

    assert compare(paper, prior).sigma == pytest.approx(3.396480, abs=1e-4)


def test_compare_v1298_b_segunda_medida_simetrica():
    paper = make_measurement(0.67, 0.16, 0.16)
    prior = make_solution(0.041, 0.017, 0.017, is_default=True)

    assert compare(paper, prior).sigma == pytest.approx(3.909246, abs=1e-4)


def test_compare_paper_por_encima_usa_err_minus_del_paper_y_err_plus_de_la_previa():
    paper = make_measurement(0.52, 0.12, 0.14)
    prior = make_solution(0.041, 0.017, 0.5, is_default=True)  # err_minus de la previa no cuenta

    comparison = compare(paper, prior)

    assert comparison.sigma == pytest.approx(3.396480, abs=1e-4)
    assert comparison.paper_err == pytest.approx(0.14 * M_JUP_IN_M_EARTH)
    assert comparison.prior_err == pytest.approx(0.017 * M_JUP_IN_M_EARTH)


def test_compare_paper_por_debajo_usa_err_plus_del_paper_y_err_minus_de_la_previa():
    paper = make_measurement(0.52, 0.12, 0.14)
    prior = make_solution(0.64, 0.01, 0.19, reference="Suarez Mascareño et al.")

    comparison = compare(paper, prior)

    assert comparison.sigma == pytest.approx(0.533993, abs=1e-4)
    assert comparison.paper_err == pytest.approx(0.12 * M_JUP_IN_M_EARTH)
    assert comparison.prior_err == pytest.approx(0.19 * M_JUP_IN_M_EARTH)
    assert comparison.paper_value == pytest.approx(0.52 * M_JUP_IN_M_EARTH)
    assert comparison.prior_value == pytest.approx(0.64 * M_JUP_IN_M_EARTH)
    assert comparison.unit == MeasurementUnit.M_EARTH


def test_compare_unidades_mezcladas_da_el_mismo_sigma_que_en_la_misma_unidad():
    paper = make_measurement(0.52, 0.12, 0.14)  # M_jup
    prior_jup = make_solution(0.041, 0.017, 0.017, is_default=True)
    prior_earth = make_solution(13.031, 5.403, 5.403, unit=M_EARTH, is_default=True)

    sigma_jup = compare(paper, prior_jup).sigma
    sigma_mixed = compare(paper, prior_earth).sigma

    assert sigma_mixed == pytest.approx(sigma_jup, rel=1e-3)
    assert sigma_mixed == pytest.approx(3.39648, abs=5e-3)


def test_compare_parametro_distinto_es_violacion():
    paper = make_measurement(0.52, 0.12, 0.14)
    prior = make_solution(1.2, 0.1, 0.1, parameter=RADIUS, unit=MeasurementUnit.R_JUP)

    with pytest.raises(InvariantViolation):
        compare(paper, prior)


@pytest.mark.parametrize(
    "paper",
    [
        make_measurement(0.52, 0.12, 0.14, origin=MeasurementOrigin.LITERATURE),
        make_measurement(0.52, None, None, limit=MeasurementLimit.UPPER),
        make_measurement(0.52, 0.12, 0.14, limit=MeasurementLimit.LOWER),
        make_measurement(0.52, None, None),
        make_measurement(0.52, 0.12, None),
    ],
    ids=["literature", "cota_superior", "cota_inferior", "sin_error", "un_solo_error"],
)
def test_compare_paper_no_usable_para_tension_es_violacion(paper):
    prior = make_solution(0.041, 0.017, 0.017)

    with pytest.raises(InvariantViolation):
        compare(paper, prior)


@pytest.mark.parametrize(
    "prior",
    [
        make_solution(0.041, 0.017, 0.017, limit=MeasurementLimit.UPPER),
        make_solution(0.041, None, 0.017),
        make_solution(0.041, 0.017, 0.0),
    ],
    ids=["cota", "sin_error", "error_cero"],
)
def test_compare_previa_no_usable_como_prior_es_violacion(prior):
    paper = make_measurement(0.52, 0.12, 0.14)

    with pytest.raises(InvariantViolation):
        compare(paper, prior)


# ---------- CatalogSolution ----------


def test_catalog_solution_valida_se_construye():
    solution = make_solution(0.041, 0.017, 0.017, is_default=True, arxiv_id="2601.99999")
    assert solution.usable_as_prior is True


@pytest.mark.parametrize("value", [0.0, -1.0, math.inf, math.nan])
def test_catalog_solution_valor_debe_ser_finito_y_positivo(value):
    with pytest.raises(InvariantViolation):
        make_solution(value, 0.1, 0.1)


@pytest.mark.parametrize("err", [-0.1, math.inf, math.nan])
def test_catalog_solution_errores_deben_ser_none_o_finitos_no_negativos(err):
    with pytest.raises(InvariantViolation):
        make_solution(1.0, err, 0.1)
    with pytest.raises(InvariantViolation):
        make_solution(1.0, 0.1, err)


def test_catalog_solution_acepta_errores_none_y_cero():
    assert make_solution(1.0, None, None).err_plus is None
    assert make_solution(1.0, 0.0, 0.0).err_minus == 0.0


def test_catalog_solution_unidad_incoherente_con_parametro():
    with pytest.raises(InvariantViolation):
        make_solution(1.0, 0.1, 0.1, parameter=MASS, unit=MeasurementUnit.R_JUP)


@pytest.mark.parametrize("field", ["planet_name", "reference"])
@pytest.mark.parametrize("blank", ["", "   "])
def test_catalog_solution_textos_obligatorios_no_vacios(field, blank):
    with pytest.raises(InvariantViolation):
        make_solution(1.0, 0.1, 0.1, **{field: blank})


@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        (dict(err_plus=0.1, err_minus=0.1), True),
        (dict(err_plus=0.1, err_minus=0.1, limit=MeasurementLimit.UPPER), False),
        (dict(err_plus=0.1, err_minus=0.1, limit=MeasurementLimit.LOWER), False),
        (dict(err_plus=None, err_minus=0.1), False),
        (dict(err_plus=0.1, err_minus=None), False),
        (dict(err_plus=0.0, err_minus=0.1), False),
        (dict(err_plus=0.1, err_minus=0.0), False),
    ],
    ids=["ok", "cota_upper", "cota_lower", "sin_plus", "sin_minus", "plus_cero", "minus_cero"],
)
def test_usable_as_prior(kwargs, expected):
    solution = make_solution(1.0, **kwargs)
    assert solution.usable_as_prior is expected


def test_catalog_solution_es_inmutable():
    solution = make_solution(1.0, 0.1, 0.1)
    with pytest.raises(AttributeError):
        solution.value = 2.0  # type: ignore[misc]
    assert isinstance(solution, CatalogSolution)


# ---------- TensionResult ----------

_ITEM = make_item()


def _v1298_b_priors() -> list[CatalogSolution]:
    return [
        make_solution(0.041, 0.017, 0.017, reference="Livingston et al. 2026", is_default=True),
        make_solution(0.64, 0.19, 0.19, reference="Suarez Mascareño et al."),
        make_solution(0.30, 0.10, 0.10, reference="Otro A"),
        make_solution(0.90, 0.30, 0.20, reference="Otro B"),
    ]


def _result(papers, priors, *, planet="V1298 Tau b", parameter=MASS) -> TensionResult:
    comparisons = tuple(compare(p, q) for p in papers for q in priors)
    return TensionResult(
        item_id=_ITEM.id, planet_name=planet, parameter=parameter, comparisons=comparisons
    )


def test_tension_result_vacio_es_violacion():
    with pytest.raises(InvariantViolation):
        TensionResult(item_id=_ITEM.id, planet_name="V1298 Tau b", parameter=MASS, comparisons=())


def test_tension_result_no_homogeneo_en_parametro_es_violacion():
    comparison = compare(make_measurement(0.52, 0.12, 0.14), make_solution(0.041, 0.017, 0.017))
    with pytest.raises(InvariantViolation):
        TensionResult(
            item_id=_ITEM.id,
            planet_name="V1298 Tau b",
            parameter=RADIUS,
            comparisons=(comparison,),
        )


def test_tension_result_mezcla_de_parametros_es_violacion():
    mass = compare(make_measurement(0.52, 0.12, 0.14), make_solution(0.041, 0.017, 0.017))
    radius = compare(
        make_measurement(1.0, 0.1, 0.1, parameter=RADIUS, unit=MeasurementUnit.R_JUP),
        make_solution(0.9, 0.1, 0.1, parameter=RADIUS, unit=MeasurementUnit.R_JUP),
    )
    with pytest.raises(InvariantViolation):
        TensionResult(
            item_id=_ITEM.id,
            planet_name="V1298 Tau b",
            parameter=MASS,
            comparisons=(mass, radius),
        )


def test_is_candidate_v1298_b_supera_3_sigma_frente_a_la_default():
    result = _result([make_measurement(0.52, 0.12, 0.14)], _v1298_b_priors())

    assert result.is_candidate(3.0) is True


def test_is_candidate_solo_mira_la_default_no_otras_previas():
    # Suarez Mascareño (0.534 sigma) no impide el candidato: la referencia es la default.
    result = _result([make_measurement(0.52, 0.12, 0.14)], _v1298_b_priors())

    assert result.is_candidate(3.0) is True
    assert result.is_candidate(3.5) is False


def test_is_candidate_v1298_b_con_dos_medidas_todas_superan_el_umbral():
    papers = [make_measurement(0.52, 0.12, 0.14), make_measurement(0.67, 0.16, 0.16)]
    result = _result(papers, _v1298_b_priors())

    assert result.is_candidate(3.0) is True  # minimo 3.3965
    assert result.is_candidate(3.5) is False  # la de 3.396 no llega


def test_is_candidate_con_copias_iguales_de_la_previa_default_identifica_por_valor():
    """Un TensionResult rehidratado o construido con copias iguales (pero no
    el mismo objeto) de la previa por defecto debe dar el mismo veredicto."""
    papers = [make_measurement(0.52, 0.12, 0.14), make_measurement(0.67, 0.16, 0.16)]
    default = make_solution(0.041, 0.017, 0.017, is_default=True)
    comparisons = (
        compare(papers[0], default),
        compare(papers[1], dataclasses.replace(default)),
    )
    assert comparisons[0].prior is not comparisons[1].prior
    assert comparisons[0].prior == comparisons[1].prior
    result = TensionResult(
        item_id=_ITEM.id, planet_name="V1298 Tau b", parameter=MASS, comparisons=comparisons
    )

    assert result.is_candidate(3.0) is True  # minimo 3.3965


def test_is_candidate_v1298_e_falso_a_3_sigma_y_verdadero_a_2_5_sigma():
    papers = [
        make_measurement(0.40, 0.14, 0.13, planet_name="V1298 Tau e"),
        make_measurement(0.66, 0.17, 0.18, planet_name="V1298 Tau e"),
        make_measurement(0.71, 0.20, 0.20, planet_name="V1298 Tau e"),
    ]
    priors = [make_solution(0.048, 0.013, 0.013, planet_name="V1298 Tau e", is_default=True)]
    result = _result(papers, priors, planet="V1298 Tau e")

    assert min(c.sigma for c in result.comparisons) == pytest.approx(2.694255, abs=1e-4)
    assert result.is_candidate(3.0) is False
    assert result.is_candidate(2.5) is True


def test_is_candidate_sin_previa_default_es_falso():
    priors = [
        make_solution(0.041, 0.017, 0.017),
        make_solution(0.64, 0.19, 0.19),
    ]
    result = _result([make_measurement(0.52, 0.12, 0.14)], priors)

    assert result.is_candidate(0.1) is False


def test_is_candidate_con_dos_defaults_es_falso():
    priors = [
        make_solution(0.041, 0.017, 0.017, is_default=True),
        make_solution(0.045, 0.017, 0.017, is_default=True, reference="Otra"),
    ]
    result = _result([make_measurement(0.52, 0.12, 0.14)], priors)

    assert result.is_candidate(0.1) is False
