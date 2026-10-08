"""T76: `catalog_tension_skip_reason`, la regla pura de qué tensiones se redactan."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from helpers.exoplanet import make_measurement, make_own_solution_rule, make_solution
from helpers.measurement_findings import hip67522_c, toi_6981_b
from helpers.tension_writer import INDEPENDENT_ARXIV_ID, v1298_evaluation

from nocturna.domain.entities import MeasuredParameter, MeasurementLimit, MeasurementUnit
from nocturna.domain.measurement_findings import catalog_tension_skip_reason
from nocturna.domain.tension import (
    EvaluationStatus,
    LimitOutcome,
    PeriodCheck,
    TensionEvaluation,
    TensionResult,
    compare,
    compare_with_limit,
)

PUBLISHED = datetime(2026, 9, 30, tzinfo=UTC)
PAPER_ID = "2609.30038"
RULE = make_own_solution_rule()


def _reason(ev: TensionEvaluation, *, threshold: float = 3.0, external_id: str = PAPER_ID):
    return catalog_tension_skip_reason(
        ev,
        threshold_sigma=threshold,
        item_external_id=external_id,
        item_published_at=PUBLISHED,
        own_rule=RULE,
    )


def _with_priors(ev: TensionEvaluation, patch) -> TensionEvaluation:
    assert ev.result is not None
    comparisons = tuple(replace(c, prior=patch(c.prior)) for c in ev.result.comparisons)
    result = replace(ev.result, comparisons=comparisons)
    return replace(ev, result=result)


def test_evaluacion_elegible() -> None:
    assert _reason(v1298_evaluation(uuid4())) is None


def test_por_debajo_del_umbral() -> None:
    assert _reason(v1298_evaluation(uuid4()), threshold=3.5) == "below_threshold"


def test_en_el_umbral_exacto_es_elegible() -> None:
    ev = v1298_evaluation(uuid4())
    assert ev.result is not None
    sigma = ev.result.reference_sigma()
    assert sigma is not None
    assert _reason(ev, threshold=sigma) is None


def test_referencia_que_no_es_la_solucion_por_defecto() -> None:
    ev = v1298_evaluation(uuid4())

    def patch(prior):
        # Una sola previa publicada y confirmada, sin marca de defecto.
        if prior.is_default:
            return replace(prior, is_default=False)
        return replace(prior, soltype="Controversial")

    assert _reason(_with_priors(ev, patch)) == "reference_not_default"


def test_referencia_propia_por_arxiv_id() -> None:
    ev = v1298_evaluation(uuid4(), reference_overrides={"arxiv_id": PAPER_ID})
    assert _reason(ev) == "reference_not_independent"


def test_referencia_sin_arxiv_id_ni_fecha_es_ambigua_y_se_excluye() -> None:
    ev = v1298_evaluation(uuid4(), reference_overrides={"arxiv_id": None, "pl_pubdate": None})
    assert _reason(ev) == "reference_not_independent"


def test_referencia_antigua_sin_arxiv_id_es_independiente() -> None:
    ev = v1298_evaluation(uuid4(), reference_overrides={"arxiv_id": None, "pl_pubdate": "2022-01"})
    assert _reason(ev) is None


@pytest.mark.parametrize("ev_factory", [toi_6981_b, hip67522_c])
def test_cualquier_estado_distinto_de_evaluated_es_not_evaluated(ev_factory) -> None:
    ev = ev_factory(uuid4())
    assert ev.status is not EvaluationStatus.EVALUATED
    assert _reason(ev) == "not_evaluated"


def test_incompatible_with_limit_no_se_redacta_aunque_el_limite_sea_incompatible() -> None:
    """OD 244 abierta: una cota incompatible no cabe en `CatalogTension` v1."""
    limit_solution = make_solution(0.3, None, None, limit=MeasurementLimit.UPPER)
    paper = (make_measurement(0.52, 0.12, 0.01),)
    ev = TensionEvaluation(
        reading_id=uuid4(),
        item_id=uuid4(),
        planet_name="V1298 Tau b",
        parameter=MeasuredParameter.MASS,
        measurements=paper,
        status=EvaluationStatus.INCOMPATIBLE_WITH_LIMIT,
        evaluated_at=PUBLISHED,
        archive_planet_name="V1298 Tau b",
        limit=compare_with_limit(paper, limit_solution, threshold_sigma=3.0),
    )
    assert ev.limit is not None and ev.limit.outcome is LimitOutcome.INCOMPATIBLE

    assert _reason(ev) == "not_evaluated"


def _period_evaluation(check: PeriodCheck) -> TensionEvaluation:
    day = MeasurementUnit.DAY
    period = MeasuredParameter.PERIOD
    paper = make_measurement(10.0, 0.001, 0.001, parameter=period, unit=day)
    prior = make_solution(
        10.01,
        0.001,
        0.001,
        parameter=period,
        unit=day,
        is_default=True,
        arxiv_id=INDEPENDENT_ARXIV_ID,
    )
    item_id = uuid4()
    result = TensionResult(
        item_id=item_id,
        planet_name="V1298 Tau b",
        parameter=period,
        comparisons=(compare(paper, prior),),
    )
    return TensionEvaluation(
        reading_id=uuid4(),
        item_id=item_id,
        planet_name="V1298 Tau b",
        parameter=period,
        measurements=(paper,),
        status=EvaluationStatus.EVALUATED,
        evaluated_at=PUBLISHED,
        archive_planet_name="V1298 Tau b",
        result=result,
        period_check=check,
    )


def test_periodo_sin_diferencia_minima() -> None:
    check = PeriodCheck(min_difference_met=False, alias_suspected=False)
    assert _reason(_period_evaluation(check)) == "period_min_difference"


def test_periodo_con_alias_sospechado() -> None:
    check = PeriodCheck(min_difference_met=True, alias_suspected=True)
    assert _reason(_period_evaluation(check)) == "period_alias"


def test_periodo_limpio_es_elegible() -> None:
    check = PeriodCheck(min_difference_met=True, alias_suspected=False)
    assert _reason(_period_evaluation(check)) is None
