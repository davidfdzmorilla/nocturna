"""T72: `Finding.catalog_tension`, `CatalogTension` y su factoría (dominio puro).

Idioma: español. Sin BD ni red (el catálogo es el MockTransport de helpers).
"""

import math
from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from helpers.exoplanet import (
    V1298_ARCHIVE_URL,
    V1298_THRESHOLD,
    catalog_tension_v1298_b,
    make_measurement,
    make_solution,
    v1298_tension_results,
)

import nocturna.domain.catalog as catalog_module
from nocturna.domain import entities
from nocturna.domain.entities import (
    CatalogTension,
    CatalogTensionComparison,
    Finding,
    FindingType,
    MeasuredParameter,
    MeasurementLimit,
    MeasurementOrigin,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.tension import TensionResult, catalog_tension_from, compare

NOW = datetime(2026, 9, 30, 3, 0, tzinfo=UTC)
B = "V1298 Tau b"


def _finding(type_, catalog_tension=None, **extra):
    kwargs = dict(
        item_id=uuid4(),
        run_id=uuid4(),
        type=type_,
        title="t",
        level_curious="c",
        level_amateur="a",
        level_technical="x",
    )
    if catalog_tension is not None:
        kwargs["catalog_tension"] = catalog_tension
        # T76: un catalog_tension nace siempre de una TensionEvaluation.
        kwargs["tension_evaluation_id"] = uuid4()
    kwargs.update(extra)
    return Finding(**kwargs)


def _comparison(paper=None, prior=None, sigma=3.5):
    return CatalogTensionComparison(
        paper=paper or make_measurement(0.5, 0.1, 0.1),
        prior=prior or make_solution(0.04, 0.02, 0.02, is_default=True),
        sigma=sigma,
    )


def _tension(**overrides):
    kwargs = dict(
        planet_name=B,
        parameter=MeasuredParameter.MASS,
        archive_url="https://archive.test/b",
        threshold_sigma=3.0,
        reference_sigma=3.5,
        comparisons=(_comparison(),),
    )
    kwargs.update(overrides)
    return CatalogTension(**kwargs)


# --- Finding ----------------------------------------------------------------


def test_finding_catalog_tension_sin_dato_falla():
    with pytest.raises(InvariantViolation):
        _finding(FindingType.CATALOG_TENSION)


def test_finding_paper_explained_con_dato_falla():
    with pytest.raises(InvariantViolation):
        _finding(FindingType.PAPER_EXPLAINED, catalog_tension=catalog_tension_v1298_b())


@pytest.mark.parametrize("bogus", ["tension", 3.4, object()])
def test_finding_catalog_tension_con_objeto_que_no_es_catalog_tension_falla(bogus):
    with pytest.raises(InvariantViolation):
        _finding(FindingType.CATALOG_TENSION, catalog_tension=bogus)


def test_finding_catalog_tension_valido_se_construye():
    tension = catalog_tension_v1298_b()

    finding = _finding(FindingType.CATALOG_TENSION, catalog_tension=tension)

    assert finding.catalog_tension is tension


def test_finding_catalog_tension_sin_tension_evaluation_id_falla():
    with pytest.raises(InvariantViolation, match="tension_evaluation_id"):
        _finding(
            FindingType.CATALOG_TENSION,
            catalog_tension=catalog_tension_v1298_b(),
            tension_evaluation_id=None,
        )


def test_finding_paper_explained_con_tension_evaluation_id_falla():
    with pytest.raises(InvariantViolation, match="tension_evaluation_id"):
        _finding(FindingType.PAPER_EXPLAINED, tension_evaluation_id=uuid4())


def test_finding_paper_explained_sin_dato_sigue_funcionando():
    finding = _finding(FindingType.PAPER_EXPLAINED)

    assert finding.catalog_tension is None


def test_finding_catalog_tension_se_publica_igual_que_paper_explained():
    finding = _finding(FindingType.CATALOG_TENSION, catalog_tension=catalog_tension_v1298_b())

    finding.publish(confidence=0.8, at=NOW)

    assert finding.published_at == NOW
    assert finding.catalog_tension is not None


# --- Invariantes de CatalogTension -------------------------------------------


def test_catalog_tension_valida_se_construye():
    assert _tension().reference_sigma == 3.5


def test_comparaciones_vacias_fallan():
    with pytest.raises(InvariantViolation):
        _tension(comparisons=())


def test_comparaciones_con_parametro_heterogeneo_fallan():
    radius_paper = make_measurement(
        5.0, 0.5, 0.5, parameter=MeasuredParameter.RADIUS, unit=entities.MeasurementUnit.R_EARTH
    )
    radius_prior = make_solution(
        4.0,
        0.5,
        0.5,
        parameter=MeasuredParameter.RADIUS,
        unit=entities.MeasurementUnit.R_EARTH,
        is_default=True,
    )
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(), _comparison(radius_paper, radius_prior)))


def test_previa_de_otro_planeta_falla():
    other = make_solution(0.04, 0.02, 0.02, planet_name="V1298 Tau e", is_default=True)
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(prior=other),))


def test_medida_de_literatura_falla():
    paper = make_measurement(0.5, 0.1, 0.1, origin=MeasurementOrigin.LITERATURE)
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(paper=paper),))


def test_medida_con_cota_falla():
    paper = make_measurement(0.5, 0.1, 0.1, limit=MeasurementLimit.UPPER)
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(paper=paper),))


def test_medida_sin_errores_falla():
    paper = make_measurement(0.5, None, None)
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(paper=paper),))


def test_previa_con_cota_falla():
    prior = make_solution(0.04, 0.02, 0.02, limit=MeasurementLimit.UPPER)
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(prior=prior),))


def test_previa_con_error_cero_falla():
    prior = make_solution(0.04, 0.0, 0.02)
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(prior=prior),))


@pytest.mark.parametrize("sigma", [-0.1, math.nan, math.inf])
def test_sigma_negativo_o_no_finito_falla(sigma):
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(sigma=sigma),))


@pytest.mark.parametrize("threshold", [0.0, -1.0, math.nan, math.inf])
def test_threshold_sigma_no_positivo_o_no_finito_falla(threshold):
    with pytest.raises(InvariantViolation):
        _tension(threshold_sigma=threshold)


def test_reference_sigma_menor_que_umbral_falla():
    with pytest.raises(InvariantViolation):
        _tension(threshold_sigma=3.0, reference_sigma=2.99)


@pytest.mark.parametrize("reference", [math.nan, math.inf])
def test_reference_sigma_no_finito_falla(reference):
    with pytest.raises(InvariantViolation):
        _tension(reference_sigma=reference)


def test_reference_sigma_que_no_es_el_minimo_frente_a_la_default_falla():
    default = make_solution(0.04, 0.02, 0.02, is_default=True)
    comparisons = (_comparison(prior=default, sigma=3.5), _comparison(prior=default, sigma=4.2))
    with pytest.raises(InvariantViolation):
        _tension(reference_sigma=4.2, comparisons=comparisons)


def test_reference_sigma_igual_al_minimo_frente_a_la_default_ignora_otras_previas():
    default = make_solution(0.04, 0.02, 0.02, is_default=True)
    other = make_solution(0.05, 0.02, 0.02, is_default=False)
    comparisons = (
        _comparison(prior=default, sigma=4.2),
        _comparison(prior=default, sigma=3.5),
        _comparison(prior=other, sigma=3.1),
    )
    assert _tension(reference_sigma=3.5, comparisons=comparisons).reference_sigma == 3.5


def test_sin_previa_default_falla():
    prior = make_solution(0.04, 0.02, 0.02, is_default=False)
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(prior=prior),))


def test_dos_previas_default_distintas_fallan():
    first = make_solution(0.04, 0.02, 0.02, is_default=True)
    second = make_solution(0.05, 0.02, 0.02, is_default=True)
    with pytest.raises(InvariantViolation):
        _tension(comparisons=(_comparison(prior=first), _comparison(prior=second)))


def test_finding_con_type_como_cadena_y_sin_dato_falla():
    with pytest.raises(InvariantViolation):
        _finding("catalog_tension")


def test_planet_name_en_blanco_falla():
    with pytest.raises(InvariantViolation):
        _tension(planet_name="   ")


def test_archive_url_en_blanco_falla():
    with pytest.raises(InvariantViolation):
        _tension(archive_url="  ")


def test_elemento_de_comparaciones_que_no_es_comparison_falla():
    with pytest.raises(InvariantViolation):
        _tension(comparisons=("no",))


def test_comparaciones_se_normalizan_a_tupla():
    assert isinstance(_tension(comparisons=[_comparison()]).comparisons, tuple)


# --- Factoría catalog_tension_from --------------------------------------------


def test_factoria_v1298_b_conserva_referencia_orden_y_numero():
    result = v1298_tension_results()[B]

    tension = catalog_tension_from(
        result, threshold_sigma=V1298_THRESHOLD, archive_url=V1298_ARCHIVE_URL
    )

    assert tension.planet_name == B
    assert tension.parameter is MeasuredParameter.MASS
    assert tension.reference_sigma == result.reference_sigma()
    assert tension.reference_sigma == pytest.approx(3.368, abs=1e-3)
    assert tension.threshold_sigma == V1298_THRESHOLD
    assert tension.archive_url == V1298_ARCHIVE_URL
    assert len(tension.comparisons) == len(result.comparisons) == 8
    for got, src in zip(tension.comparisons, result.comparisons, strict=True):
        assert (got.paper, got.prior, got.sigma) == (src.paper, src.prior, src.sigma)


def test_factoria_v1298_e_por_debajo_del_umbral_falla():
    result = v1298_tension_results()["V1298 Tau e"]

    assert result.reference_sigma() == pytest.approx(2.69, abs=1e-2)
    with pytest.raises(InvariantViolation):
        catalog_tension_from(result, threshold_sigma=V1298_THRESHOLD, archive_url="https://a.test")


def test_factoria_sin_previa_por_defecto_falla():
    paper = make_measurement(0.5, 0.1, 0.1)
    prior = make_solution(0.04, 0.02, 0.02, is_default=False)
    result = TensionResult(
        item_id=uuid4(),
        planet_name=B,
        parameter=MeasuredParameter.MASS,
        comparisons=(compare(paper, prior),),
    )

    with pytest.raises(InvariantViolation):
        catalog_tension_from(result, threshold_sigma=3.0, archive_url="https://a.test")


def test_factoria_con_previa_por_defecto_sintetica_funciona():
    paper = make_measurement(0.5, 0.1, 0.1)
    prior = make_solution(0.04, 0.02, 0.02, is_default=True)
    result = TensionResult(
        item_id=uuid4(),
        planet_name=B,
        parameter=MeasuredParameter.MASS,
        comparisons=(compare(paper, prior),),
    )

    tension = catalog_tension_from(result, threshold_sigma=3.0, archive_url="https://a.test")

    assert tension.reference_sigma == result.comparisons[0].sigma


# --- CatalogSolution única -----------------------------------------------------


def test_catalog_solution_es_la_misma_clase_en_catalog_y_entities():
    assert catalog_module.CatalogSolution is entities.CatalogSolution


def test_catalog_tension_es_inmutable():
    tension = _tension()
    with pytest.raises(AttributeError):
        tension.reference_sigma = 9.0  # type: ignore[misc]
    assert replace(tension, threshold_sigma=2.0).threshold_sigma == 2.0
