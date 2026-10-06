"""T83: clasificación de una solución del archivo respecto a un paper (dominio puro).

Idioma: español. Sin BD, sin red, sin Claude. La regla decide si una solución
del archivo es la del propio paper (`OWN_ARXIV_ID`, `OWN_VALUE_MATCH`), una
ajena (`INDEPENDENT`) o no se puede saber (`AMBIGUOUS`). Valores de la regla
de los tests: tolerancia relativa 0,01 y margen de 6 meses (los de
`[tension.own_solution]`).
"""

from datetime import UTC, datetime

import pytest
from helpers.exoplanet import make_measurement, make_solution

from nocturna.domain.entities import (
    MeasuredParameter,
    MeasurementLimit,
    MeasurementUnit,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.own_solution import (
    OwnSolutionRule,
    SolutionProvenance,
    classify_solution,
)
from nocturna.domain.tension import M_JUP_IN_M_EARTH

MASS = MeasuredParameter.MASS
RADIUS = MeasuredParameter.RADIUS
M_EARTH = MeasurementUnit.M_EARTH
M_JUP = MeasurementUnit.M_JUP
R_EARTH = MeasurementUnit.R_EARTH

EXTERNAL_ID = "2609.35979"
PUBLISHED = datetime(2026, 9, 15, 8, 0, tzinfo=UTC)
RULE = OwnSolutionRule(value_rel_tolerance=0.01, pubdate_margin_months=6)


def _paper(value=14.0, *, unit=M_EARTH, parameter=MASS, planet="HIP 67522 b"):
    return make_measurement(value, 1.0, 1.0, planet_name=planet, unit=unit, parameter=parameter)


def _solution(value=14.0, *, unit=M_EARTH, parameter=MASS, **kwargs):
    return make_solution(
        value,
        1.0,
        1.0,
        planet_name="HIP 67522 b",
        unit=unit,
        parameter=parameter,
        reference="Chakraborty et al. 2026",
        **kwargs,
    )


def _classify(solution, *, measurements=None, rule=RULE, external_id=EXTERNAL_ID, at=PUBLISHED):
    return classify_solution(
        solution,
        external_id=external_id,
        published_at=at,
        measurements=[_paper()] if measurements is None else measurements,
        rule=rule,
    )


# --- arxiv_id ----------------------------------------------------------------


def test_mismo_arxiv_id_es_own_arxiv_id():
    assert _classify(_solution(arxiv_id=EXTERNAL_ID, pl_pubdate="2026-09")) == (
        SolutionProvenance.OWN_ARXIV_ID
    )


def test_mismo_arxiv_id_gana_aunque_los_valores_no_casen_o_la_fecha_sea_antigua():
    solution = _solution(99.0, arxiv_id=EXTERNAL_ID, pl_pubdate="2019-01")

    assert _classify(solution) == SolutionProvenance.OWN_ARXIV_ID


def test_mismo_arxiv_id_gana_aunque_la_solucion_sea_una_cota():
    solution = _solution(arxiv_id=EXTERNAL_ID, limit=MeasurementLimit.UPPER)

    assert _classify(solution) == SolutionProvenance.OWN_ARXIV_ID


def test_arxiv_id_distinto_con_valores_identicos_es_independiente():
    solution = _solution(14.0, arxiv_id="2606.18045", pl_pubdate="2026-09")

    assert _classify(solution) == SolutionProvenance.INDEPENDENT


def test_arxiv_id_distinto_es_independiente_sin_mirar_la_fecha_ni_el_valor():
    solution = _solution(14.0, arxiv_id="2606.18045", pl_pubdate=None)

    assert _classify(solution) == SolutionProvenance.INDEPENDENT


# --- bibcode de revista (sin arxiv_id) ---------------------------------------


def test_valor_al_medio_por_ciento_y_fecha_posterior_es_own_value_match():
    solution = _solution(14.0 * 1.005, pl_pubdate="2026-10")

    assert _classify(solution) == SolutionProvenance.OWN_VALUE_MATCH


def test_valor_a_la_izquierda_dentro_de_la_tolerancia_tambien_casa():
    solution = _solution(14.0 * 0.995, pl_pubdate="2026-10")

    assert _classify(solution) == SolutionProvenance.OWN_VALUE_MATCH


def test_valor_identico_con_pl_pubdate_2024_09_tipo_barber_es_independiente():
    solution = _solution(14.0, pl_pubdate="2024-09")

    assert _classify(solution) == SolutionProvenance.INDEPENDENT


def test_valor_distinto_y_fecha_plausible_es_ambiguo():
    solution = _solution(14.6, pl_pubdate="2026-10")

    assert _classify(solution) == SolutionProvenance.AMBIGUOUS


def test_pl_pubdate_nulo_cuenta_como_plausible_y_con_valor_igual_es_own_value_match():
    assert _classify(_solution(14.0, pl_pubdate=None)) == SolutionProvenance.OWN_VALUE_MATCH


def test_pl_pubdate_nulo_con_valor_distinto_es_ambiguo():
    assert _classify(_solution(30.0, pl_pubdate=None)) == SolutionProvenance.AMBIGUOUS


@pytest.mark.parametrize("pubdate", ["", "sin-fecha", "2026", "2026-13", "26-09"])
def test_pl_pubdate_ilegible_cuenta_como_plausible(pubdate):
    assert _classify(_solution(14.0, pl_pubdate=pubdate)) == SolutionProvenance.OWN_VALUE_MATCH
    assert _classify(_solution(30.0, pl_pubdate=pubdate)) == SolutionProvenance.AMBIGUOUS


# --- unidades ----------------------------------------------------------------


def test_medida_en_m_jup_frente_a_solucion_en_m_earth_casa_tras_to_canonical():
    paper = _paper(0.0440, unit=M_JUP)
    solution = _solution(0.0440 * M_JUP_IN_M_EARTH * 1.002, unit=M_EARTH, pl_pubdate="2026-10")

    assert _classify(solution, measurements=[paper]) == SolutionProvenance.OWN_VALUE_MATCH


def test_medida_en_m_jup_con_solucion_en_m_earth_fuera_de_tolerancia_es_ambigua():
    paper = _paper(0.0440, unit=M_JUP)
    solution = _solution(0.0440 * M_JUP_IN_M_EARTH * 1.05, unit=M_EARTH, pl_pubdate="2026-10")

    assert _classify(solution, measurements=[paper]) == SolutionProvenance.AMBIGUOUS


def test_otro_parametro_con_el_mismo_numero_no_casa():
    """Un radio de 14,0 R_earth no identifica una solución de masa de 14,0 M_earth."""
    radius = _paper(14.0, unit=R_EARTH, parameter=RADIUS)

    assert _classify(_solution(14.0, pl_pubdate="2026-10"), measurements=[radius]) == (
        SolutionProvenance.AMBIGUOUS
    )


# --- alguna medida, cotas ----------------------------------------------------


def test_basta_con_que_case_alguna_de_las_medidas_del_paper():
    measurements = [_paper(25.0), _paper(23.1), _paper(14.0)]

    assert _classify(_solution(14.05, pl_pubdate="2026-10"), measurements=measurements) == (
        SolutionProvenance.OWN_VALUE_MATCH
    )


def test_sin_medidas_una_solucion_sin_arxiv_id_y_fecha_plausible_es_ambigua():
    assert _classify(_solution(14.0, pl_pubdate="2026-10"), measurements=[]) == (
        SolutionProvenance.AMBIGUOUS
    )


@pytest.mark.parametrize("limit", [MeasurementLimit.UPPER, MeasurementLimit.LOWER])
def test_una_solucion_que_es_cota_nunca_es_own_value_match(limit):
    solution = _solution(14.0, limit=limit, pl_pubdate="2026-10")

    assert _classify(solution) == SolutionProvenance.AMBIGUOUS


def test_una_cota_con_fecha_anterior_sigue_siendo_independiente():
    solution = _solution(14.0, limit=MeasurementLimit.UPPER, pl_pubdate="2020-01")

    assert _classify(solution) == SolutionProvenance.INDEPENDENT


# --- bordes ------------------------------------------------------------------


def test_tolerancia_justo_dentro_casa_y_justo_fuera_no():
    inside = _solution(14.0 * 1.009, pl_pubdate="2026-10")
    outside = _solution(14.0 * 1.011, pl_pubdate="2026-10")

    assert _classify(inside) == SolutionProvenance.OWN_VALUE_MATCH
    assert _classify(outside) == SolutionProvenance.AMBIGUOUS


def test_valor_exactamente_igual_casa_con_la_tolerancia_minima_admitida():
    tiny = OwnSolutionRule(value_rel_tolerance=1e-9, pubdate_margin_months=6)

    assert _classify(_solution(14.0, pl_pubdate="2026-10"), rule=tiny) == (
        SolutionProvenance.OWN_VALUE_MATCH
    )
    assert _classify(_solution(14.0 * 1.001, pl_pubdate="2026-10"), rule=tiny) == (
        SolutionProvenance.AMBIGUOUS
    )


def test_margen_de_meses_el_mes_limite_es_plausible_y_el_anterior_no():
    """Paper de 2026-09 con margen de 6: 2026-03 es plausible; 2026-02, anterior."""
    assert _classify(_solution(14.0, pl_pubdate="2026-03")) == SolutionProvenance.OWN_VALUE_MATCH
    assert _classify(_solution(14.0, pl_pubdate="2026-02")) == SolutionProvenance.INDEPENDENT


def test_margen_de_meses_cruza_el_cambio_de_anio():
    paper_in_march = datetime(2026, 3, 10, tzinfo=UTC)

    assert _classify(_solution(14.0, pl_pubdate="2025-09"), at=paper_in_march) == (
        SolutionProvenance.OWN_VALUE_MATCH
    )
    assert _classify(_solution(14.0, pl_pubdate="2025-08"), at=paper_in_march) == (
        SolutionProvenance.INDEPENDENT
    )


def test_margen_cero_exige_el_mismo_mes_o_posterior():
    zero = OwnSolutionRule(value_rel_tolerance=0.01, pubdate_margin_months=0)

    assert _classify(_solution(14.0, pl_pubdate="2026-09"), rule=zero) == (
        SolutionProvenance.OWN_VALUE_MATCH
    )
    assert _classify(_solution(14.0, pl_pubdate="2026-08"), rule=zero) == (
        SolutionProvenance.INDEPENDENT
    )


def test_el_mes_del_paper_se_toma_de_published_at_aware_en_utc():
    late_utc = datetime(2026, 9, 30, 23, 59, tzinfo=UTC)

    assert _classify(_solution(14.0, pl_pubdate="2026-03"), at=late_utc) == (
        SolutionProvenance.OWN_VALUE_MATCH
    )
    assert _classify(_solution(14.0, pl_pubdate="2026-02"), at=late_utc) == (
        SolutionProvenance.INDEPENDENT
    )


# --- invariantes de OwnSolutionRule -----------------------------------------


def test_regla_valida_con_los_limites_incluidos():
    assert OwnSolutionRule(value_rel_tolerance=0.1, pubdate_margin_months=0)
    assert OwnSolutionRule(value_rel_tolerance=0.001, pubdate_margin_months=24)


@pytest.mark.parametrize("tolerance", [0.0, -0.01, 0.1000001, 0.5, 1.0, float("nan"), float("inf")])
def test_tolerancia_fuera_de_0_a_0_1_es_invariante_violado(tolerance):
    with pytest.raises(InvariantViolation):
        OwnSolutionRule(value_rel_tolerance=tolerance, pubdate_margin_months=6)


@pytest.mark.parametrize("margin", [-1, -12])
def test_margen_negativo_es_invariante_violado(margin):
    with pytest.raises(InvariantViolation):
        OwnSolutionRule(value_rel_tolerance=0.01, pubdate_margin_months=margin)


def test_regla_es_inmutable():
    with pytest.raises(AttributeError):
        RULE.value_rel_tolerance = 0.05  # type: ignore[misc]


def test_los_cuatro_valores_de_procedencia():
    assert {p.value for p in SolutionProvenance} == {
        "own_arxiv_id",
        "own_value_match",
        "ambiguous",
        "independent",
    }


# --- periodo en DAY (sin arxiv_id, pl_pubdate posterior) ---------------------


def test_periodo_con_valor_identico_sin_arxiv_id_y_pubdate_posterior_es_ambiguo():
    period = MeasuredParameter.PERIOD
    day = MeasurementUnit.DAY
    paper = make_measurement(
        24.1386, 0.0001, 0.0001, planet_name="HIP 67522 b", unit=day, parameter=period
    )

    def solution(**kwargs):
        return _solution(24.1386, unit=day, parameter=period, **kwargs)

    assert _classify(solution(pl_pubdate="2026-10"), measurements=[paper]) == (
        SolutionProvenance.AMBIGUOUS
    )
    assert _classify(
        solution(arxiv_id=EXTERNAL_ID, pl_pubdate="2026-10"), measurements=[paper]
    ) == (SolutionProvenance.OWN_ARXIV_ID)
