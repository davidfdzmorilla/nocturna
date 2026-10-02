"""Regla de referencia, cotas, periodo y TensionEvaluation (T88, dominio)."""

from datetime import UTC, date, datetime

import pytest
from helpers.exoplanet import make_item, make_measurement, make_solution

from nocturna.domain.entities import (
    MeasuredParameter,
    MeasurementLimit,
    MeasurementUnit,
)
from nocturna.domain.errors import GuardedFieldAssignment, InvalidTransition, InvariantViolation
from nocturna.domain.tension import (
    EvaluationStatus,
    LimitOutcome,
    PeriodRule,
    TensionEvaluation,
    TensionResult,
    catalog_tension_from,
    check_period,
    compare,
    compare_with_limit,
    select_reference,
    select_upper_limit,
)

MASS = MeasuredParameter.MASS
PERIOD = MeasuredParameter.PERIOD
DAY = MeasurementUnit.DAY
NOW = datetime(2026, 10, 2, tzinfo=UTC)
ITEM = make_item()


def test_select_reference_prefiere_default_y_descarta_otros_soltype():
    d = make_solution(1.0, 0.1, 0.1, is_default=True, solution_key="a")
    newer = make_solution(2.0, 0.1, 0.1, pl_pubdate="2026-09", solution_key="b")
    other = make_solution(3.0, 0.1, 0.1, is_default=True, soltype="Controversial")
    assert select_reference([newer, d, other]) == d


def test_select_reference_dos_defaults_distintas_es_none():
    a = make_solution(1.0, 0.1, 0.1, is_default=True, solution_key="a")
    b = make_solution(2.0, 0.1, 0.1, is_default=True, solution_key="b")
    assert select_reference([a, b]) is None


def test_select_reference_mas_reciente_con_desempate_determinista():
    old = make_solution(1.0, 0.1, 0.1, pl_pubdate="2020-01", solution_key="a")
    t1 = make_solution(
        2.0, 0.1, 0.1, pl_pubdate="2026-01", releasedate=date(2026, 2, 1), solution_key="z"
    )
    t2 = make_solution(
        3.0, 0.1, 0.1, pl_pubdate="2026-01", releasedate=date(2026, 2, 1), solution_key="b"
    )
    assert select_reference([old, t1, t2]) == t2
    assert select_reference([t2, t1, old]) == t2
    assert select_reference([make_solution(1.0, 0.1, 0.1, soltype=None)]) is None


def test_reference_sigma_con_referencia_no_default():
    prior = make_solution(0.041, 0.017, 0.017, pl_pubdate="2026-01")
    result = TensionResult(
        item_id=ITEM.id,
        planet_name="V1298 Tau b",
        parameter=MASS,
        comparisons=(compare(make_measurement(0.52, 0.12, 0.14), prior),),
    )
    assert result.reference() == prior
    assert result.is_candidate(3.0)
    with pytest.raises(InvariantViolation):
        catalog_tension_from(result, threshold_sigma=3.0, archive_url="https://x")


def test_select_upper_limit():
    lim = make_solution(0.5, None, None, limit=MeasurementLimit.UPPER, pl_pubdate="2026-01")
    point = make_solution(0.4, 0.1, 0.1)
    assert select_upper_limit([point, lim]) == lim
    assert select_upper_limit([point]) is None


def test_compare_with_limit_incompatible_solo_si_todas():
    lim = make_solution(0.3, None, None, limit=MeasurementLimit.UPPER)
    high = make_measurement(0.52, 0.12, 0.10)  # (0.52-0.3)/0.10 = 2.2
    higher = make_measurement(0.80, 0.12, 0.10)
    c = compare_with_limit([high, higher], lim, threshold_sigma=2.0)
    assert c.outcome == LimitOutcome.INCOMPATIBLE
    assert c.margins[0] == pytest.approx(2.2)
    low = make_measurement(0.35, 0.12, 0.10)
    assert (
        compare_with_limit([high, low], lim, threshold_sigma=2.0).outcome == LimitOutcome.CONSISTENT
    )


def test_check_period_diferencia_y_alias():
    rule = PeriodRule(0.01, 0.1, 0.05, 5)
    ref = make_solution(10.0, 0.01, 0.01, parameter=PERIOD, unit=DAY)
    same = make_measurement(10.01, 0.01, 0.01, parameter=PERIOD, unit=DAY)
    assert check_period([same], ref, rule).min_difference_met is False
    half = make_measurement(5.01, 0.01, 0.01, parameter=PERIOD, unit=DAY)
    check = check_period([half], ref, rule)
    assert check.min_difference_met and check.alias_suspected
    far = make_measurement(13.0, 0.01, 0.01, parameter=PERIOD, unit=DAY)
    assert check_period([far], ref, rule).alias_suspected is False


def test_period_rule_valida():
    for args in ((0, 1, 0.1, 3), (0.1, 0, 0.1, 3), (0.1, 1, 0.5, 3), (0.1, 1, 0.1, 1)):
        with pytest.raises(InvariantViolation):
            PeriodRule(*args)


def _evaluation(status=EvaluationStatus.AWAITING_REFERENCE, **kw) -> TensionEvaluation:
    m = make_measurement(0.52, 0.12, 0.14)
    base = dict(
        reading_id=ITEM.id,
        item_id=ITEM.id,
        planet_name="V1298 Tau b",
        parameter=MASS,
        measurements=(m,),
        status=status,
        evaluated_at=NOW,
    )
    base.update(kw)
    return TensionEvaluation(**base)


def test_evaluation_invariantes_y_status_protegido():
    ev = _evaluation()
    with pytest.raises(GuardedFieldAssignment):
        ev.status = EvaluationStatus.EVALUATED
    with pytest.raises(InvariantViolation):
        _evaluation(EvaluationStatus.EVALUATED)
    with pytest.raises(InvariantViolation):
        _evaluation(EvaluationStatus.CLOSED_LOOP)
    with pytest.raises(InvariantViolation):
        _evaluation(EvaluationStatus.INCOMPATIBLE_WITH_LIMIT)


def test_evaluation_reevaluate_conserva_id_y_solo_desde_awaiting():
    prior = make_solution(0.041, 0.017, 0.017, pl_pubdate="2026-01")
    m = make_measurement(0.52, 0.12, 0.14)
    result = TensionResult(
        item_id=ITEM.id,
        planet_name="V1298 Tau b",
        parameter=MASS,
        comparisons=(compare(m, prior),),
        reading_id=ITEM.id,
    )
    waiting = _evaluation()
    done = waiting.reevaluate_with(
        _evaluation(EvaluationStatus.EVALUATED, result=result, archive_planet_name="V1298 Tau b")
    )
    assert done.id == waiting.id and done.is_candidate(3.0)
    assert not waiting.same_outcome(done)
    with pytest.raises(InvalidTransition):
        done.reevaluate_with(_evaluation())
