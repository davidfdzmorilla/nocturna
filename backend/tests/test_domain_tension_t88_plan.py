"""T88 dominio: cobertura del plan (referencia, HIP 67522, cotas, periodo,
conversión desde el archivo y estados de TensionEvaluation). Nombres en español."""

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from helpers.exoplanet import (
    V1298_THRESHOLD,
    make_item,
    make_measurement,
    make_solution,
    v1298_tension_results,
)

from nocturna.domain.archive import (
    ArchiveParameterValue,
    ArchiveSolution,
    catalog_solution_from_archive,
)
from nocturna.domain.entities import MeasuredParameter, MeasurementLimit, MeasurementUnit
from nocturna.domain.errors import GuardedFieldAssignment, InvalidTransition, InvariantViolation
from nocturna.domain.tension import (
    EvaluationStatus,
    LimitOutcome,
    PeriodCheck,
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
M_E = MeasurementUnit.M_EARTH
M_J = MeasurementUnit.M_JUP
DAY = MeasurementUnit.DAY
NOW = datetime(2026, 10, 2, tzinfo=UTC)
HIP_B = "HIP 67522 b"
HIP_C = "HIP 67522 c"


def sol_m(value, ep=1.0, em=1.0, **kw):
    """Solución de masa en M_earth (por defecto Published Confirmed)."""
    kw.setdefault("unit", M_E)
    return make_solution(value, ep, em, **kw)


# ------------------------------------------------------------ select_reference


def test_referencia_gana_la_default_si_es_published_confirmed_con_error_bilateral():
    default = sol_m(10.0, is_default=True, solution_key="d", pl_pubdate="2020-01")
    newer = sol_m(11.0, pl_pubdate="2026-09", solution_key="n")
    assert select_reference([newer, default]) == default


def test_referencia_default_sin_masa_cae_a_chakraborty_2026_hip67522b():
    # Barber 2024 es la default del archivo pero no tiene masa verdadera
    barber = _archive_solution(
        pl_name=HIP_B,
        is_default=True,
        pl_bmassprov="Msini",
        mass=ArchiveParameterValue(5, 1, -1, 0),
    )
    assert catalog_solution_from_archive(barber, MASS, is_default=True) is None
    chakraborty = sol_m(
        13.8, 1.0, 1.0, planet_name=HIP_B, pl_pubdate="2026-09", releasedate=date(2026, 10, 1)
    )
    assert select_reference([chakraborty]) == chakraborty


def test_referencia_default_no_published_confirmed_deja_la_mas_reciente_que_si_lo_es():
    default = sol_m(10.0, is_default=True, soltype="Controversial", solution_key="d")
    old = sol_m(11.0, pl_pubdate="2019-01", solution_key="o")
    new = sol_m(12.0, pl_pubdate="2025-01", solution_key="n")
    assert select_reference([default, old, new]) == new


def test_referencia_excluye_no_published_confirmed_y_sin_soltype():
    assert select_reference([sol_m(1.0, soltype="Controversial")]) is None
    assert select_reference([sol_m(1.0, soltype="Retracted"), sol_m(2.0, soltype=None)]) is None


def test_referencia_solo_cotas_es_none():
    lim = sol_m(22.0, None, None, limit=MeasurementLimit.UPPER, is_default=True)
    assert select_reference([lim]) is None


def test_referencia_ignora_cotas_y_previas_sin_error_pese_a_ser_default():
    lim = sol_m(22.0, None, None, limit=MeasurementLimit.UPPER, is_default=True)
    point = sol_m(10.0, pl_pubdate="2020-01")
    assert select_reference([lim, point]) == point


def test_referencia_dos_defaults_distintas_es_none():
    a = sol_m(1.0, is_default=True, solution_key="a")
    b = sol_m(2.0, is_default=True, solution_key="b")
    assert select_reference([a, b]) is None


def test_referencia_misma_default_duplicada_cuenta_como_una():
    a = sol_m(1.0, is_default=True, solution_key="a")
    assert select_reference([a, replace(a)]) == a


def test_referencia_orden_pubdate_luego_releasedate_luego_solution_key():
    older_pub = sol_m(1.0, pl_pubdate="2020-01", releasedate=date(2026, 1, 1), solution_key="a")
    newer_pub = sol_m(2.0, pl_pubdate="2021-01", releasedate=date(2020, 1, 1), solution_key="b")
    assert select_reference([older_pub, newer_pub]) == newer_pub
    early_rel = sol_m(3.0, pl_pubdate="2021-01", releasedate=date(2020, 1, 1), solution_key="a")
    late_rel = sol_m(4.0, pl_pubdate="2021-01", releasedate=date(2021, 1, 1), solution_key="z")
    assert select_reference([early_rel, late_rel]) == late_rel


def test_referencia_none_en_fechas_va_al_final():
    undated = sol_m(1.0, pl_pubdate=None, releasedate=None, solution_key="a")
    dated = sol_m(2.0, pl_pubdate="1999-01", releasedate=date(1999, 1, 1), solution_key="z")
    assert select_reference([undated, dated]) == dated
    pub_only = sol_m(3.0, pl_pubdate="2020-01", releasedate=None, solution_key="a")
    pub_rel = sol_m(4.0, pl_pubdate="2020-01", releasedate=date(2000, 1, 1), solution_key="z")
    assert select_reference([pub_only, pub_rel]) == pub_rel


def test_referencia_empate_total_es_determinista_por_solution_key():
    a = sol_m(1.0, pl_pubdate="2020-01", releasedate=date(2020, 1, 1), solution_key="a")
    b = sol_m(2.0, pl_pubdate="2020-01", releasedate=date(2020, 1, 1), solution_key="b")
    for order in ([a, b], [b, a]):
        assert select_reference(order) == a


def test_referencia_vacia_es_none():
    assert select_reference([]) is None


# ------------------------------------------------------- TensionResult HIP/V1298


def _hip_result(measures):
    prior = sol_m(
        13.8,
        1.0,
        1.0,
        planet_name=HIP_B,
        pl_pubdate="2026-09",
        releasedate=date(2026, 10, 1),
        solution_key="chak",
    )
    comparisons = tuple(
        compare(make_measurement(v, p, m, planet_name=HIP_B, unit=M_E), prior)
        for v, p, m in measures
    )
    return TensionResult(
        item_id=uuid4(), planet_name=HIP_B, parameter=MASS, comparisons=comparisons
    )


def test_hip67522b_sigmas_y_no_candidato_a_3_sigma():
    result = _hip_result([(25.0, 7.6, 7.8), (23.1, 15.4, 12.4)])
    assert [c.sigma for c in result.comparisons] == [
        pytest.approx(1.42, abs=1e-2),
        pytest.approx(0.75, abs=1e-2),
    ]
    assert result.reference_sigma() == pytest.approx(0.75, abs=1e-2)
    assert result.is_candidate(3.0) is False


def test_v1298_tau_regresion_b_candidato_y_e_no():
    results = v1298_tension_results()
    b, e = results["V1298 Tau b"], results["V1298 Tau e"]
    assert b.reference_sigma() == pytest.approx(3.37, abs=1e-2)
    assert b.is_candidate(V1298_THRESHOLD)
    assert e.reference_sigma() == pytest.approx(2.69, abs=1e-2)
    assert not e.is_candidate(V1298_THRESHOLD)


def test_catalog_tension_from_rechaza_referencia_no_default():
    result = _hip_result([(60.0, 1.0, 1.0)])
    assert result.is_candidate(3.0)
    assert not result.reference().is_default
    with pytest.raises(InvariantViolation):
        catalog_tension_from(result, threshold_sigma=3.0, archive_url="https://x")


# ----------------------------------------------------------------------- cotas

LIMIT_C = sol_m(
    22.0, None, None, planet_name=HIP_C, limit=MeasurementLimit.UPPER, solution_key="lim"
)


def _m(value, err_plus, err_minus, unit=M_E):
    return make_measurement(value, err_plus, err_minus, planet_name=HIP_C, unit=unit)


def test_cota_hip67522c_medidas_reales_son_consistentes():
    ms = [_m(11.2, 1.4, 1.4), _m(11.8, 3.0, 2.3), _m(9.7, 1.9, 1.6)]
    assert compare_with_limit(ms, LIMIT_C, threshold_sigma=3.0).outcome == LimitOutcome.CONSISTENT


def test_cota_medida_muy_por_encima_es_incompatible():
    c = compare_with_limit([_m(40, 3, 3)], LIMIT_C, threshold_sigma=3.0)
    assert c.outcome == LimitOutcome.INCOMPATIBLE
    assert c.margins == (pytest.approx(6.0),)


def test_cota_cerca_del_limite_es_consistente():
    c = compare_with_limit([_m(24, 3, 3)], LIMIT_C, threshold_sigma=3.0)
    assert c.outcome == LimitOutcome.CONSISTENT


def test_cota_criterio_usa_err_minus_y_umbral_inclusivo():
    exact = compare_with_limit([_m(31, 9, 3)], LIMIT_C, threshold_sigma=3.0)
    assert exact.margins == (pytest.approx(3.0),)
    assert exact.outcome == LimitOutcome.INCOMPATIBLE
    below = compare_with_limit([_m(30.9, 9, 3)], LIMIT_C, threshold_sigma=3.0)
    assert below.outcome == LimitOutcome.CONSISTENT
    # err_plus enorme no afecta: manda err_minus
    wide_minus = compare_with_limit([_m(40, 1, 30)], LIMIT_C, threshold_sigma=3.0)
    assert wide_minus.outcome == LimitOutcome.CONSISTENT


def test_cota_medidas_mezcladas_son_consistentes():
    c = compare_with_limit([_m(40, 3, 3), _m(11.2, 1.4, 1.4)], LIMIT_C, threshold_sigma=3.0)
    assert c.outcome == LimitOutcome.CONSISTENT


def test_cota_convierte_mjup_a_mearth():
    # 0.126 M_J * 317.83 = 40.04 M_earth; err 0.01 M_J = 3.178 M_earth
    c = compare_with_limit([_m(0.126, 0.01, 0.01, unit=M_J)], LIMIT_C, threshold_sigma=3.0)
    assert c.margins[0] == pytest.approx((0.126 * 317.83 - 22.0) / (0.01 * 317.83))
    assert c.outcome == LimitOutcome.INCOMPATIBLE


def test_select_upper_limit_ignora_cota_inferior_y_no_published_confirmed():
    lower = sol_m(5.0, None, None, limit=MeasurementLimit.LOWER)
    controversial = sol_m(30.0, None, None, limit=MeasurementLimit.UPPER, soltype="Controversial")
    assert select_upper_limit([lower, controversial]) is None
    assert select_upper_limit([lower, controversial, LIMIT_C]) == LIMIT_C


# --------------------------------------------------------------------- periodo

RULE = PeriodRule(1e-4, 1 / 24, 0.05, 5)


def _p(v):
    return make_measurement(v, 0.001, 0.001, parameter=PERIOD, unit=DAY)


def _pref(v=10.0):
    return make_solution(v, 0.001, 0.001, parameter=PERIOD, unit=DAY)


def test_periodo_por_debajo_de_ambos_minimos_no_cumple_diferencia():
    # rel 5e-5 < 1e-4 y 0.0005 d < 1 h
    assert check_period([_p(10.0005)], _pref(), RULE).min_difference_met is False


def test_periodo_vale_solo_diferencia_relativa():
    # P=1 d, delta 0.0002 d: rel 2e-4 >= 1e-4, absoluta < 1 h
    assert check_period([_p(1.0002)], _pref(1.0), RULE).min_difference_met is True


def test_periodo_vale_solo_diferencia_absoluta():
    # P=1000 d, delta 0.05 d >= 1 h (0.0417), rel 5e-5 < 1e-4
    assert check_period([_p(1000.05)], _pref(1000.0), RULE).min_difference_met is True


def test_periodo_diferencia_exige_todas_las_medidas():
    check = check_period([_p(10.5), _p(10.0001)], _pref(), RULE)
    assert check.min_difference_met is False


@pytest.mark.parametrize("p", [20.05, 4.99, 5.0, 30.0])
def test_periodo_alias_en_razones_enteras(p):
    assert check_period([_p(p)], _pref(), RULE).alias_suspected is True


def test_periodo_alias_razones_2_005_y_0_499():
    assert check_period([_p(20.05)], _pref(), RULE).alias_suspected  # 2.005
    assert check_period([_p(4.99)], _pref(), RULE).alias_suspected  # 0.499 -> inversa 2.004


def test_periodo_razon_1_5_no_es_alias():
    assert check_period([_p(15.0)], _pref(), RULE).alias_suspected is False


def test_periodo_razon_cercana_a_1_no_es_alias():
    assert check_period([_p(10.0001)], _pref(), RULE).alias_suspected is False


def test_periodo_mas_alla_del_armonico_maximo_no_es_alias():
    assert check_period([_p(60.0)], _pref(), RULE).alias_suspected is False  # n=6 > 5
    assert check_period([_p(60.0)], _pref(), PeriodRule(1e-4, 1 / 24, 0.05, 6)).alias_suspected


# ------------------------------------------------------ catalog_solution_from_archive


def _archive_solution(**kw) -> ArchiveSolution:
    base = {
        "pl_name": "V1298 Tau b",
        "hostname": "V1298 Tau",
        "pl_refname": "x",
        "ref_key": "2019AJ....158...79D",
        "ref_text": "David et al. 2019",
        "arxiv_id": "1906.00001",
        "soltype": "Published Confirmed",
        "releasedate": date(2019, 6, 27),
        "pl_pubdate": "2019-08",
        "is_default": False,
        "mass": ArchiveParameterValue(),
        "radius": ArchiveParameterValue(10.22, 0.55, -0.59, 0),
        "period": ArchiveParameterValue(24.13861, 0.00102, -0.0009, 0),
        "pl_bmassprov": None,
        "st_rad": ArchiveParameterValue(),
        "st_mass": ArchiveParameterValue(),
        "discoverymethod": "Transit",
        "ttv_flag": True,
        "pl_controv_flag": False,
    }
    base.update(kw)
    return ArchiveSolution(**base)


def test_archivo_masa_solo_con_provenance_mass():
    mass = ArchiveParameterValue(13.8, 1.0, -1.0, 0)
    ok = _archive_solution(mass=mass, pl_bmassprov="Mass")
    assert catalog_solution_from_archive(ok, MASS, is_default=False).value == 13.8
    for prov in ("Msini", "M-R relationship", None):
        bad = _archive_solution(mass=mass, pl_bmassprov=prov)
        assert catalog_solution_from_archive(bad, MASS, is_default=False) is None
    # radio y periodo no dependen de la provenance de masa
    assert catalog_solution_from_archive(ok, MeasuredParameter.RADIUS, is_default=False)


def test_archivo_errores_en_valor_absoluto_y_unidad_canonica():
    s = catalog_solution_from_archive(_archive_solution(), PERIOD, is_default=False)
    assert (s.err_plus, s.err_minus) == (0.00102, 0.0009)
    assert s.unit == DAY


def test_archivo_lim_se_traslada():
    for flag, expected in (
        (0, MeasurementLimit.NONE),
        (1, MeasurementLimit.UPPER),
        (-1, MeasurementLimit.LOWER),
    ):
        s = _archive_solution(
            mass=ArchiveParameterValue(22.0, None, None, flag), pl_bmassprov="Mass"
        )
        assert catalog_solution_from_archive(s, MASS, is_default=False).limit == expected
    unknown = _archive_solution(
        mass=ArchiveParameterValue(22.0, None, None, 7), pl_bmassprov="Mass"
    )
    assert catalog_solution_from_archive(unknown, MASS, is_default=False) is None


def test_archivo_is_default_y_cinco_campos_nuevos():
    s = catalog_solution_from_archive(_archive_solution(), PERIOD, is_default=True)
    raw = _archive_solution()
    assert s.is_default is True
    assert s.solution_key == raw.solution_key
    assert s.soltype == "Published Confirmed"
    assert s.pl_pubdate == "2019-08"
    assert s.releasedate == date(2019, 6, 27)
    assert s.ttv_flag is True
    assert s.arxiv_id == "1906.00001"
    assert s.reference == "David et al. 2019"


@pytest.mark.parametrize("value", [0.0, -1.0, float("nan"), float("inf"), None])
def test_archivo_valor_no_positivo_o_invalido_es_none(value):
    s = _archive_solution(mass=ArchiveParameterValue(value, 1.0, -1.0, 0), pl_bmassprov="Mass")
    assert catalog_solution_from_archive(s, MASS, is_default=False) is None


# ------------------------------------------------------------ TensionEvaluation

ITEM = make_item()
READING_ID = uuid4()


def _result(prior=None, *, value=0.52):
    prior = prior or make_solution(0.041, 0.017, 0.017, pl_pubdate="2026-01")
    m = make_measurement(value, 0.12, 0.14)
    return TensionResult(
        item_id=ITEM.id,
        planet_name="V1298 Tau b",
        parameter=MASS,
        comparisons=(compare(m, prior),),
        reading_id=READING_ID,
    )


def _ev(status=EvaluationStatus.AWAITING_REFERENCE, **kw) -> TensionEvaluation:
    base = {
        "reading_id": READING_ID,
        "item_id": ITEM.id,
        "planet_name": "V1298 Tau b",
        "parameter": MASS,
        "measurements": (make_measurement(0.52, 0.12, 0.14),),
        "status": status,
        "evaluated_at": NOW,
    }
    base.update(kw)
    return TensionEvaluation(**base)


def _limit(outcome=LimitOutcome.CONSISTENT):
    lim = make_solution(0.3, None, None, limit=MeasurementLimit.UPPER)
    threshold = 3.0 if outcome == LimitOutcome.INCOMPATIBLE else 100.0
    m = make_measurement(0.52, 0.12, 0.01)
    return compare_with_limit([m], lim, threshold_sigma=threshold)


def test_estado_awaiting_sin_result_valido_y_con_referencia_no():
    assert _ev().status == EvaluationStatus.AWAITING_REFERENCE
    with pytest.raises(InvariantViolation):
        _ev(result=_result())


def test_estado_awaiting_acepta_result_sin_referencia():
    no_ref = _result(make_solution(0.041, 0.017, 0.017, soltype="Controversial"))
    assert no_ref.reference() is None
    assert _ev(result=no_ref).result is no_ref


def test_estado_evaluated_requiere_result_con_referencia():
    assert _ev(EvaluationStatus.EVALUATED, result=_result()).result is not None
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.EVALUATED)
    no_ref = _result(make_solution(0.041, 0.017, 0.017, soltype="Controversial"))
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.EVALUATED, result=no_ref)


def test_estado_evaluated_no_admite_limit():
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.EVALUATED, result=_result(), limit=_limit())


def test_estado_consistent_with_limit_exige_limit_consistente():
    ok = _ev(EvaluationStatus.CONSISTENT_WITH_LIMIT, limit=_limit(LimitOutcome.CONSISTENT))
    assert ok.limit is not None
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.CONSISTENT_WITH_LIMIT)
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.CONSISTENT_WITH_LIMIT, limit=_limit(LimitOutcome.INCOMPATIBLE))


def test_estado_incompatible_with_limit_exige_limit_incompatible():
    ok = _ev(EvaluationStatus.INCOMPATIBLE_WITH_LIMIT, limit=_limit(LimitOutcome.INCOMPATIBLE))
    assert ok.limit.outcome == LimitOutcome.INCOMPATIBLE
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.INCOMPATIBLE_WITH_LIMIT)
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.INCOMPATIBLE_WITH_LIMIT, limit=_limit(LimitOutcome.CONSISTENT))


def test_estado_closed_loop_exige_own_solution_key_y_no_admite_limit():
    assert _ev(EvaluationStatus.CLOSED_LOOP, own_solution_key="k").own_solution_key == "k"
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.CLOSED_LOOP)
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.CLOSED_LOOP, own_solution_key="")
    with pytest.raises(InvariantViolation):
        _ev(EvaluationStatus.CLOSED_LOOP, own_solution_key="k", limit=_limit())


def test_awaiting_no_admite_limit():
    with pytest.raises(InvariantViolation):
        _ev(limit=_limit())


def test_status_esta_protegido():
    with pytest.raises(GuardedFieldAssignment):
        _ev().status = EvaluationStatus.EVALUATED


def test_reevaluate_desde_awaiting_conserva_id():
    waiting = _ev()
    new = _ev(EvaluationStatus.EVALUATED, result=_result())
    done = waiting.reevaluate_with(new)
    assert done.id == waiting.id != new.id
    assert done.status == EvaluationStatus.EVALUATED


@pytest.mark.parametrize(
    "terminal",
    [
        lambda: _ev(EvaluationStatus.EVALUATED, result=_result()),
        lambda: _ev(EvaluationStatus.CONSISTENT_WITH_LIMIT, limit=_limit()),
        lambda: _ev(
            EvaluationStatus.INCOMPATIBLE_WITH_LIMIT, limit=_limit(LimitOutcome.INCOMPATIBLE)
        ),
        lambda: _ev(EvaluationStatus.CLOSED_LOOP, own_solution_key="k"),
    ],
)
def test_reevaluate_desde_estado_terminal_es_transicion_invalida(terminal):
    with pytest.raises(InvalidTransition):
        terminal().reevaluate_with(_ev())


@pytest.mark.parametrize(
    "change",
    [{"reading_id": uuid4()}, {"planet_name": "V1298 Tau e"}],
)
def test_reevaluate_con_otra_clave_es_invariant_violation(change):
    measurements = (
        make_measurement(0.52, 0.12, 0.14, planet_name=change.get("planet_name", "V1298 Tau b")),
    )
    new = _ev(EvaluationStatus.EVALUATED, result=_result(), measurements=measurements, **change)
    with pytest.raises(InvariantViolation):
        _ev().reevaluate_with(new)


def test_reevaluate_con_otro_parametro_es_invariant_violation():
    new = _ev(
        EvaluationStatus.CLOSED_LOOP,
        own_solution_key="k",
        parameter=MeasuredParameter.RADIUS,
        measurements=(
            make_measurement(
                1.0, 0.1, 0.1, parameter=MeasuredParameter.RADIUS, unit=MeasurementUnit.R_EARTH
            ),
        ),
    )
    with pytest.raises(InvariantViolation):
        _ev().reevaluate_with(new)


def test_same_outcome_ignora_evaluated_at_e_id_pero_no_el_resto():
    a = _ev()
    b = _ev(evaluated_at=datetime(2030, 1, 1, tzinfo=UTC))
    assert a.id != b.id and a.same_outcome(b)
    assert not a.same_outcome(_ev(EvaluationStatus.CLOSED_LOOP, own_solution_key="k"))
    assert not a.same_outcome(_ev(archive_planet_name="otro"))


def test_is_candidate_evaluated_segun_resultado():
    cand = _ev(EvaluationStatus.EVALUATED, result=_result())  # sigma ~3.4
    assert cand.is_candidate(3.0)
    assert not cand.is_candidate(10.0)
    weak = _ev(EvaluationStatus.EVALUATED, result=_result(value=0.06))
    assert not weak.is_candidate(3.0)


def _period_ev(period_check):
    prior = make_solution(10.0, 0.001, 0.001, parameter=PERIOD, unit=DAY, pl_pubdate="2026-01")
    m = make_measurement(10.5, 0.01, 0.01, parameter=PERIOD, unit=DAY)
    result = TensionResult(
        item_id=ITEM.id,
        planet_name="V1298 Tau b",
        parameter=PERIOD,
        comparisons=(compare(m, prior),),
    )
    return _ev(
        EvaluationStatus.EVALUATED,
        parameter=PERIOD,
        measurements=(m,),
        result=result,
        period_check=period_check,
    )


def test_is_candidate_periodo_exige_diferencia_minima():
    assert _period_ev(PeriodCheck(True, False)).is_candidate(3.0)
    assert not _period_ev(PeriodCheck(False, False)).is_candidate(3.0)


def test_periodo_evaluated_sin_period_check_viola_el_invariante():
    with pytest.raises(InvariantViolation, match="period_check"):
        _period_ev(None)


def test_masa_evaluated_no_requiere_period_check():
    assert _ev(EvaluationStatus.EVALUATED, result=_result()).period_check is None


def test_is_candidate_incompatible_con_cota_es_siempre_true():
    ev = _ev(EvaluationStatus.INCOMPATIBLE_WITH_LIMIT, limit=_limit(LimitOutcome.INCOMPATIBLE))
    assert ev.is_candidate(3.0) and ev.is_candidate(1000.0)


def test_is_candidate_resto_de_estados_es_false():
    assert not _ev().is_candidate(3.0)
    assert not _ev(EvaluationStatus.CONSISTENT_WITH_LIMIT, limit=_limit()).is_candidate(3.0)
    assert not _ev(EvaluationStatus.CLOSED_LOOP, own_solution_key="k").is_candidate(3.0)
