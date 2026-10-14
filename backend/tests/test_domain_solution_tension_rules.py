"""Reglas de T85 una a una: σ simétrico, reglas 1-7, D5 e invariantes (dominio puro).

Complementa `test_domain_solution_tension.py` (números reales de HD 202206 c).
Nombres en español, consistentes con ese fichero.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from test_domain_solution_tension import (
    ACTIVE,
    MSINI,
    NEW,
    NOW,
    OLD,
    RULE,
    build,
    sol,
)

from nocturna.domain.archive import ArchiveParameterValue
from nocturna.domain.entities import (
    Finding,
    FindingType,
    MeasuredParameter,
    ReferenceRole,
    SolutionTensionData,
    SolutionTensionLabel,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.solution_tension import (
    SkipReason,
    SolutionTensionRule,
    build_solution_tension,
    compare_solutions,
    is_old_paper,
    labels_for,
    same_reference,
    select_pair_reference,
    solution_tension_skip_reason,
    usable_solution,
)

MASS = MeasuredParameter.MASS
PERIOD = MeasuredParameter.PERIOD
PV = ArchiveParameterValue


def skip(new, ref, parameter=MASS, *, sigma=9.0, period_check=None, rule=RULE):
    return solution_tension_skip_reason(
        new, ref, parameter, sigma=sigma, period_check=period_check, rule=rule
    )


# --- σ: simétrico y con el lado del error correcto ---------------------------------


def test_sigma_es_simetrico_y_usa_el_lado_del_error_que_mira_al_otro_valor():
    a = usable_solution(sol(mass=PV(10.0, 1.0, -5.0, 0)), MASS, is_default=True)
    b = usable_solution(sol(ref_key="B", mass=PV(20.0, 8.0, -2.0, 0)), MASS, is_default=False)
    # a < b: del lado de a, err_plus = 1; del lado de b, err_minus = 2.
    esperado = 10.0 / (1.0**2 + 2.0**2) ** 0.5
    assert compare_solutions(a, b) == pytest.approx(esperado)
    assert compare_solutions(b, a) == pytest.approx(esperado)
    # Los lados equivocados (minus de a, plus de b) darian 10/sqrt(89) = 1,06.
    assert compare_solutions(a, b) > 4.0


def test_sigma_de_hd_202206_c_masa_8_20_y_periodo_3_46():
    mass = build(MASS)
    period = build(PERIOD)
    assert mass.sigma == pytest.approx(8.20, abs=0.01)
    assert period.sigma == pytest.approx(3.46, abs=0.01)
    # simetria sobre los datos reales
    a = usable_solution(NEW, MASS, is_default=True)
    b = usable_solution(OLD, MASS, is_default=False)
    assert compare_solutions(a, b) == pytest.approx(compare_solutions(b, a))


def test_sigma_exige_el_mismo_parametro_y_soluciones_utilizables():
    a = usable_solution(NEW, MASS, is_default=True)
    p = usable_solution(NEW, PERIOD, is_default=True)
    with pytest.raises(InvariantViolation):
        compare_solutions(a, p)


def test_la_masa_solo_con_pl_bmassprov_mass():
    for prov in ("Msini", "Msin(i)/sin(i)", None):
        assert usable_solution(sol(pl_bmassprov=prov), MASS, is_default=True) is None
    assert usable_solution(sol(pl_bmassprov="Mass"), MASS, is_default=True) is not None
    # el periodo no depende de pl_bmassprov
    assert usable_solution(sol(pl_bmassprov="Msini"), PERIOD, is_default=True) is not None


def test_un_limite_o_un_error_no_positivo_no_es_utilizable():
    assert usable_solution(sol(mass=PV(982.0, 44.0, -44.0, 1)), MASS, is_default=True) is None
    assert usable_solution(sol(mass=PV(982.0, None, None, 0)), MASS, is_default=True) is None
    assert usable_solution(sol(mass=PV(982.0, 0.0, -44.0, 0)), MASS, is_default=True) is None


# --- regla 1: Published Confirmed en cualquiera de los dos lados -------------------


@pytest.mark.parametrize("lado", ["alta", "referencia"])
def test_regla_1_published_confirmed_en_los_dos_lados(lado):
    candidate = sol(ref_key="C", soltype="Published Candidate", is_default=False)
    new, ref = (candidate, OLD) if lado == "alta" else (NEW, candidate)
    assert skip(new, ref) is SkipReason.NOT_PUBLISHED_CONFIRMED


def test_regla_1_con_soltype_nulo():
    assert skip(sol(soltype=None), OLD) is SkipReason.NOT_PUBLISHED_CONFIRMED
    assert skip(NEW, sol(ref_key="Z", soltype=None)) is SkipReason.NOT_PUBLISHED_CONFIRMED


def test_regla_1_gana_a_todas_las_demas():
    new = sol(soltype="Other", ttv_flag=True, pl_pubdate="2000-01")
    ref = sol(ref_key=new.ref_key)
    assert skip(new, ref, PERIOD, sigma=0.1) is SkipReason.NOT_PUBLISHED_CONFIRMED


# --- regla 2: misma referencia -----------------------------------------------------


def test_regla_2_misma_ref_key():
    assert same_reference(NEW, sol(is_default=False, mass=PV(5.0, 1.0, -1.0, 0)))
    assert skip(NEW, sol(is_default=False, mass=PV(5.0, 1.0, -1.0, 0))) is SkipReason.SAME_REFERENCE


def test_regla_2_mismo_arxiv_id_con_ref_key_distinto():
    a = sol(ref_key="A", arxiv_id="2607.00001")
    b = sol(ref_key="B", arxiv_id="2607.00001", is_default=False)
    assert same_reference(a, b) and same_reference(b, a)
    assert skip(a, b) is SkipReason.SAME_REFERENCE


@pytest.mark.parametrize(
    ("arxiv_a", "arxiv_b"),
    [(None, None), ("2607.00001", None), (None, "2607.00001"), ("2607.00001", "2607.00002")],
)
def test_regla_2_no_salta_con_ref_key_distinto_y_arxiv_id_distinto_o_ausente(arxiv_a, arxiv_b):
    a = sol(ref_key="A", arxiv_id=arxiv_a)
    b = sol(ref_key="B", arxiv_id=arxiv_b, is_default=False)
    assert not same_reference(a, b)
    assert skip(a, b) is None


# --- regla 3: paper antiguo, frontera de 24 meses, solo la alta --------------------


@pytest.mark.parametrize(
    ("pubdate", "release", "old"),
    [
        ("2024-08", date(2026, 8, 20), False),  # exactamente 24 meses
        ("2024-07", date(2026, 8, 20), True),  # 25 meses
        ("2024-12", date(2026, 12, 1), False),  # 24 meses, cruzando año
        ("2024-11", date(2026, 12, 1), True),
        ("2024-08-15", date(2026, 8, 20), False),  # con día
        (None, date(2026, 8, 20), False),
        ("", date(2026, 8, 20), False),
        ("basura", date(2026, 8, 20), False),
        ("2024", date(2026, 8, 20), False),
        ("2024-13", date(2026, 8, 20), False),
        ("2024-00", date(2026, 8, 20), False),
        ("1990-01", date(2026, 8, 20), True),
        ("2027-01", date(2026, 8, 20), False),  # pubdate posterior: reciente
    ],
)
def test_regla_3_frontera_de_24_meses(pubdate, release, old):
    assert is_old_paper(sol(pl_pubdate=pubdate, releasedate=release), RULE) is old


def test_regla_3_usa_el_maximo_de_la_regla():
    strict = SolutionTensionRule(3.0, date(2026, 8, 1), 12, RULE.period_rule)
    alta = sol(pl_pubdate="2025-07", releasedate=date(2026, 8, 20))  # 13 meses
    assert is_old_paper(alta, strict) and not is_old_paper(alta, RULE)


def test_regla_3_solo_se_aplica_a_la_alta_no_a_la_referencia():
    # Una referencia con desfase de mas de 24 meses no se descarta por ello.
    vieja_ref = sol(
        ref_key="V", is_default=False, pl_pubdate="2015-01", releasedate=date(2026, 8, 20)
    )
    assert is_old_paper(vieja_ref, RULE)
    assert skip(NEW, vieja_ref) is None
    # En cambio como alta si se descarta.
    antigua = sol(ref_key="X", pl_pubdate="2020-01", releasedate=date(2026, 8, 20))
    assert skip(antigua, OLD) is SkipReason.OLD_PAPER


def test_regla_3_pubdate_nulo_cuenta_como_reciente_en_la_decision():
    assert skip(sol(pl_pubdate=None), OLD) is None
    assert skip(sol(pl_pubdate="basura"), OLD) is None


# --- regla 5: periodo --------------------------------------------------------------


def _period_pair(p_new, e_new, p_ref, e_ref, **kw_new):
    new = sol(ref_key="N", period=PV(p_new, e_new, -e_new, 0), **kw_new)
    ref = sol(ref_key="R", is_default=False, period=PV(p_ref, e_ref, -e_ref, 0))
    active = [(new, True), (ref, False)]
    return build_solution_tension(
        new, True, ref, False, ReferenceRole.MOST_RECENT, PERIOD, active, RULE, NOW
    )


def test_regla_5_diferencia_minima_no_alcanzada():
    # dP/P = 1e-6 < 1e-4 y dP = 1e-5 d < 1/24 d, pero con σ enorme.
    t = _period_pair(10.00001, 1e-9, 10.0, 1e-9)
    assert t.sigma > 3.0
    assert not t.period_check.min_difference_met
    assert t.skip_reason is SkipReason.PERIOD_MIN_DIFFERENCE


def test_regla_5_diferencia_minima_alcanzada_solo_por_la_relativa_o_solo_por_la_absoluta():
    # relativa: dP/P = 2e-4 >= 1e-4 y dP = 0,02 d < 1/24
    assert _period_pair(100.02, 1e-4, 100.0, 1e-4).period_check.min_difference_met
    # absoluta: dP = 0,05 d >= 1/24 pero dP/P = 5e-5 < 1e-4
    t = _period_pair(1000.05, 1e-4, 1000.0, 1e-4)
    assert t.period_check.min_difference_met and t.skip_reason is None


def test_regla_5_alias_se_guarda_con_su_motivo():
    t = _period_pair(10.0, 0.01, 20.0, 0.01)
    assert t.period_check.alias_suspected
    assert t.skip_reason is SkipReason.PERIOD_ALIAS
    assert t.sigma is not None and t.sigma > 3.0  # la fila existe, con σ


def test_regla_5_alias_en_el_otro_sentido_y_fuera_de_tolerancia():
    assert _period_pair(30.0, 0.01, 10.0, 0.01).skip_reason is SkipReason.PERIOD_ALIAS
    # ratio 2,05 queda fuera de la tolerancia (0,01): no es alias
    assert _period_pair(10.5, 0.01, 5.122, 0.01).skip_reason is None


@pytest.mark.parametrize("lado", ["alta", "referencia"])
def test_regla_5_ttv_en_cualquiera_de_los_dos_lados_no_compara_periodo(lado):
    new = sol(ttv_flag=lado == "alta")
    ref = sol(ref_key="R", ttv_flag=lado == "referencia", is_default=False)
    assert skip(new, ref, PERIOD) is SkipReason.PERIOD_TTV
    assert skip(new, ref, MASS) is None  # la masa no depende de ttv_flag


def test_regla_5_ttv_none_no_es_ttv():
    assert skip(sol(ttv_flag=None), sol(ref_key="R", ttv_flag=None), MASS) is None


def test_el_periodo_sin_period_check_es_un_error_de_programacion():
    with pytest.raises(InvariantViolation):
        skip(NEW, OLD, PERIOD, sigma=9.0, period_check=None)


def test_orden_de_los_motivos():
    from nocturna.domain.tension import PeriodCheck

    bad = PeriodCheck(min_difference_met=False, alias_suspected=True)
    old_alta = dict(pl_pubdate="2000-01")
    # same_reference antes que old_paper
    assert skip(sol(**old_alta), sol(is_default=False), MASS) is SkipReason.SAME_REFERENCE
    # old_paper antes que period_ttv
    assert (
        skip(sol(ref_key="A", ttv_flag=True, **old_alta), OLD, PERIOD, period_check=bad)
        is SkipReason.OLD_PAPER
    )
    # period_ttv antes que below_threshold
    assert (
        skip(sol(ref_key="A", ttv_flag=True), OLD, PERIOD, sigma=0.1, period_check=bad)
        is SkipReason.PERIOD_TTV
    )
    # below_threshold antes que min_difference / alias
    assert (
        skip(sol(ref_key="A"), OLD, PERIOD, sigma=0.1, period_check=bad)
        is SkipReason.BELOW_THRESHOLD
    )
    # min_difference antes que alias
    assert (
        skip(sol(ref_key="A"), OLD, PERIOD, sigma=9.0, period_check=bad)
        is SkipReason.PERIOD_MIN_DIFFERENCE
    )


def test_umbral_frontera_exacta():
    assert skip(NEW, OLD, sigma=3.0) is None
    assert skip(NEW, OLD, sigma=2.999999) is SkipReason.BELOW_THRESHOLD


def test_el_umbral_de_la_regla_se_usa():
    alto = SolutionTensionRule(10.0, date(2026, 8, 1), 24, RULE.period_rule)
    assert skip(NEW, OLD, sigma=8.2, rule=alto) is SkipReason.BELOW_THRESHOLD


# --- reglas 6 y 7: etiquetas sin descartar -----------------------------------------


def test_etiqueta_modelo_depende_por_la_ref_key_de_la_alta():
    twin = sol(mass=PV(900.0, 40.0, -40.0, 0), is_default=False)  # misma ref_key que NEW
    assert labels_for(NEW, OLD, [NEW, OLD, twin]) == (SolutionTensionLabel.MODEL_DEPENDENT,)


def test_etiqueta_modelo_depende_por_la_ref_key_de_la_referencia():
    twin = sol(ref_key=OLD.ref_key, soltype="Published Candidate", is_default=False)
    assert labels_for(NEW, OLD, [NEW, OLD, twin]) == (SolutionTensionLabel.MODEL_DEPENDENT,)


def test_sin_etiqueta_modelo_con_una_fila_por_referencia():
    assert labels_for(NEW, OLD, [NEW, OLD, MSINI]) == ()


def test_etiqueta_controversial_en_alta_o_referencia():
    assert labels_for(sol(pl_controv_flag=True), OLD, [NEW, OLD]) == (
        SolutionTensionLabel.CONTROVERSIAL,
    )
    ref = sol(ref_key="R", pl_controv_flag=True, is_default=False)
    assert labels_for(NEW, ref, [NEW, ref]) == (SolutionTensionLabel.CONTROVERSIAL,)


def test_las_etiquetas_no_descartan():
    twin = sol(
        ref_key=OLD.ref_key,
        mass=PV(900.0, 40.0, -40.0, 0),
        is_default=False,
        pl_pubdate="2016-01",
    )
    controversial = sol(pl_controv_flag=True)
    t = build(
        MASS,
        new=controversial,
        active=[(controversial, True), (OLD, False), (twin, False)],
    )
    assert t.reference_solution_key == OLD.solution_key
    assert set(t.labels) == {
        SolutionTensionLabel.MODEL_DEPENDENT,
        SolutionTensionLabel.CONTROVERSIAL,
    }
    assert t.skip_reason is None


# --- D5: select_pair_reference -----------------------------------------------------


def test_d5_alta_no_por_defecto_referencia_es_la_por_defecto_vigente():
    picked = select_pair_reference(OLD, False, MASS, ACTIVE)
    assert picked == (NEW, True, ReferenceRole.DEFAULT)


def test_d5_la_por_defecto_puede_no_ser_published_confirmed_y_el_motivo_lo_recoge():
    default = sol(ref_key="D", soltype="Published Candidate")
    alta = sol(ref_key="A", is_default=False)
    picked = select_pair_reference(alta, False, MASS, [(alta, False), (default, True)])
    assert picked == (default, True, ReferenceRole.DEFAULT)
    assert skip(alta, default) is SkipReason.NOT_PUBLISHED_CONFIRMED


def test_d5_sin_exactamente_una_por_defecto_no_hay_referencia():
    alta = sol(ref_key="A", is_default=False)
    other = sol(ref_key="B", is_default=False)
    assert select_pair_reference(alta, False, MASS, [(alta, False), (other, False)]) is None
    d1, d2 = sol(ref_key="D1"), sol(ref_key="D2")
    assert select_pair_reference(alta, False, MASS, [(alta, False), (d1, True), (d2, True)]) is None


def test_d5_la_por_defecto_sin_valor_utilizable_no_es_referencia():
    default = sol(ref_key="D", mass=PV())
    alta = sol(ref_key="A", is_default=False)
    assert select_pair_reference(alta, False, MASS, [(alta, False), (default, True)]) is None
    assert select_pair_reference(alta, False, PERIOD, [(alta, False), (default, True)]) is not None


def test_d5_alta_por_defecto_elige_la_published_confirmed_mas_reciente_utilizable():
    alta = sol(ref_key="A")
    older = sol(ref_key="B", pl_pubdate="2020-01", releasedate=date(2020, 2, 1), is_default=False)
    newer = sol(ref_key="C", pl_pubdate="2022-05", releasedate=date(2022, 6, 1), is_default=False)
    candidate = sol(
        ref_key="D", soltype="Published Candidate", pl_pubdate="2025-01", is_default=False
    )
    msini = sol(ref_key="E", pl_bmassprov="Msini", pl_pubdate="2025-06", is_default=False)
    active = [(alta, True), (older, False), (newer, False), (candidate, False), (msini, False)]
    picked = select_pair_reference(alta, True, MASS, active)
    assert picked == (newer, False, ReferenceRole.MOST_RECENT)
    # el periodo si admite la Msini (mas reciente)
    assert select_pair_reference(alta, True, PERIOD, active)[0] == msini


def test_d5_desempate_por_releasedate_y_luego_por_solution_key():
    alta = sol(ref_key="A")
    a = sol(ref_key="B", pl_pubdate="2022-05", releasedate=date(2022, 6, 1), is_default=False)
    b = sol(ref_key="C", pl_pubdate="2022-05", releasedate=date(2023, 1, 1), is_default=False)
    assert select_pair_reference(alta, True, MASS, [(alta, True), (a, False), (b, False)])[0] == b
    c = sol(
        ref_key="D",
        pl_pubdate="2022-05",
        releasedate=date(2023, 1, 1),
        is_default=False,
        mass=PV(3.0, 1.0, -1.0, 0),
    )
    first = min((b, c), key=lambda s: s.solution_key)
    assert (
        select_pair_reference(alta, True, MASS, [(alta, True), (c, False), (b, False)])[0] == first
    )


def test_d5_pubdate_nulo_es_el_menos_reciente():
    alta = sol(ref_key="A")
    sin = sol(ref_key="B", pl_pubdate=None, releasedate=date(2026, 1, 1), is_default=False)
    con = sol(ref_key="C", pl_pubdate="2001-01", releasedate=date(2001, 2, 1), is_default=False)
    assert (
        select_pair_reference(alta, True, MASS, [(alta, True), (sin, False), (con, False)])[0]
        == con
    )


def test_d5_reference_role_se_propaga_a_la_tension():
    assert build(MASS).reference_role is ReferenceRole.MOST_RECENT
    new_not_default = build(MASS, new=OLD, new_default=False)
    assert new_not_default.reference_role is ReferenceRole.DEFAULT
    assert new_not_default.reference_solution_key == NEW.solution_key


def test_d5_la_alta_nunca_es_su_propia_referencia():
    assert select_pair_reference(NEW, True, MASS, [(NEW, True)]) is None


def test_build_devuelve_none_si_la_alta_no_tiene_valor_utilizable():
    ref = OLD
    assert (
        build_solution_tension(
            sol(mass=PV()), True, ref, False, ReferenceRole.MOST_RECENT, MASS, ACTIVE, RULE, NOW
        )
        is None
    )


def test_others_excluye_alta_y_referencia_y_las_no_confirmadas():
    cand = sol(ref_key="K", soltype="Published Candidate", is_default=False)
    third = sol(
        ref_key="T", is_default=False, mass=PV(3000.0, 100.0, -100.0, 0), pl_pubdate="2010-01"
    )
    t = build(MASS, active=[(NEW, True), (OLD, False), (cand, False), (third, False)])
    assert [c.solution.reference for c in t.others] == ["T"] or len(t.others) == 1
    assert len(t.others) == 1 and t.others[0].sigma >= 0


# --- invariantes de SolutionTensionData y Finding (iff) ----------------------------


def _data(**kw):
    new = usable_solution(NEW, MASS, is_default=True)
    ref = usable_solution(OLD, MASS, is_default=False)
    base = dict(
        planet_name="HD 202206 c",
        parameter=MASS,
        archive_url="https://exoplanetarchive.ipac.caltech.edu/overview/HD%20202206%20c",
        threshold_sigma=3.0,
        sigma=8.2,
        new=new,
        reference=ref,
        others=[],
        labels=[],
        reference_role=ReferenceRole.MOST_RECENT,
    )
    base.update(kw)
    return SolutionTensionData(**base)


def test_solution_tension_data_valida_y_normaliza_a_tuplas():
    data = _data()
    assert data.others == () and data.labels == ()


@pytest.mark.parametrize(
    "kw",
    [
        {"planet_name": " "},
        {"archive_url": ""},
        {"threshold_sigma": 0.0},
        {"threshold_sigma": float("nan")},
        {"sigma": 2.99},
        {"sigma": float("inf")},
        {"sigma": float("nan")},
        {"parameter": PERIOD},  # new/reference son de masa
    ],
)
def test_solution_tension_data_invariantes(kw):
    with pytest.raises(InvariantViolation):
        _data(**kw)


def test_solution_tension_data_sigma_igual_al_umbral_vale():
    assert _data(sigma=3.0).sigma == 3.0


def _finding(**kw):
    base = dict(
        item_id=uuid4(),
        run_id=uuid4(),
        type=FindingType.SOLUTION_TENSION,
        title="t",
        level_curious="c",
        level_amateur="a",
        level_technical="x",
        solution_tension=_data(),
        solution_tension_id=uuid4(),
    )
    base.update(kw)
    return Finding(**base)


def test_finding_solution_tension_valido():
    f = _finding()
    assert f.solution_tension is not None and f.solution_tension_id is not None
    f.publish(0.7, datetime(2026, 10, 14, tzinfo=UTC))
    assert f.is_published


@pytest.mark.parametrize(
    "kw",
    [
        {"solution_tension": None},
        {"solution_tension_id": None},
        {"solution_tension": None, "solution_tension_id": None},
        {"type": FindingType.PAPER_EXPLAINED},  # payload e id con otro tipo
        {"solution_tension": "no soy un SolutionTensionData"},
    ],
)
def test_finding_solution_tension_iff(kw):
    with pytest.raises(InvariantViolation):
        _finding(**kw)


def test_finding_de_otro_tipo_no_admite_payload_ni_id_de_solution_tension():
    base = dict(
        item_id=uuid4(),
        run_id=uuid4(),
        type=FindingType.PAPER_EXPLAINED,
        title="t",
        level_curious="c",
        level_amateur="a",
        level_technical="x",
    )
    Finding(**base)
    with pytest.raises(InvariantViolation):
        Finding(**base, solution_tension=_data())
    with pytest.raises(InvariantViolation):
        Finding(**base, solution_tension_id=uuid4())


def test_finding_solution_tension_no_lleva_tension_evaluation_id():
    with pytest.raises(InvariantViolation):
        _finding(tension_evaluation_id=uuid4())


# --- invariantes de SolutionTension (__post_init__) ----------------------------------


def _valid_tension(**overrides):
    from dataclasses import replace

    base = build(MASS)
    assert base is not None
    return replace(base, **overrides) if overrides else base


def test_solution_tension_valida_se_construye():
    assert _valid_tension().parameter == MASS


@pytest.mark.parametrize("sigma", [float("nan"), float("inf"), -0.1])
def test_sigma_no_finito_o_negativo_se_rechaza(sigma):
    with pytest.raises(InvariantViolation):
        _valid_tension(sigma=sigma)


def test_parametro_distinto_del_de_las_soluciones_se_rechaza():
    with pytest.raises(InvariantViolation):
        _valid_tension(parameter=PERIOD)


def test_period_check_solo_en_periodo():
    from nocturna.domain.tension import PeriodCheck

    with pytest.raises(InvariantViolation):
        _valid_tension(period_check=PeriodCheck(min_difference_met=True, alias_suspected=False))
    period = build(PERIOD)
    assert period is not None and period.period_check is not None
    from dataclasses import replace

    with pytest.raises(InvariantViolation):
        replace(period, period_check=None)


def test_item_id_solo_sin_skip_reason():
    with pytest.raises(InvariantViolation):
        _valid_tension(item_id=uuid4(), skip_reason=SkipReason.OLD_PAPER)
    assert _valid_tension(item_id=uuid4(), skip_reason=None).item_id is not None


def test_alta_y_referencia_distintas():
    with pytest.raises(InvariantViolation):
        _valid_tension(reference_solution_key=NEW.solution_key)


# --- D5: el mismo paper nunca es referencia --------------------------------------------


def test_alta_por_defecto_ignora_filas_de_su_mismo_paper():
    otra_fila = sol(
        is_default=False,
        pl_bmassprov="Mass",
        mass=PV(900.0, 40.0, -40.0, 0),
        releasedate=date(2026, 8, 21),
    )
    active = [(NEW, True), (otra_fila, False), (OLD, False)]
    picked = select_pair_reference(NEW, True, MASS, active)
    assert picked is not None
    assert picked[0] == OLD and picked[2] is ReferenceRole.MOST_RECENT


def test_alta_por_defecto_sin_otro_paper_no_tiene_referencia():
    otra_fila = sol(is_default=False, mass=PV(900.0, 40.0, -40.0, 0))
    assert select_pair_reference(NEW, True, MASS, [(NEW, True), (otra_fila, False)]) is None


def test_alta_no_por_defecto_con_la_por_defecto_del_mismo_paper_usa_la_mas_reciente_de_otro():
    # NEW (por defecto) y ALTA son filas del mismo paper; ALTA no es la vigente.
    alta = sol(is_default=False, mass=PV(900.0, 40.0, -40.0, 0), releasedate=date(2026, 8, 21))
    active = [(NEW, True), (alta, False), (OLD, False), (MSINI, False)]
    picked = select_pair_reference(alta, False, MASS, active)
    assert picked is not None
    assert picked[0] == OLD and picked[2] is ReferenceRole.MOST_RECENT


def test_alta_no_por_defecto_y_por_defecto_del_mismo_paper_sin_otro_paper_sin_referencia():
    alta = sol(is_default=False, mass=PV(900.0, 40.0, -40.0, 0))
    assert select_pair_reference(alta, False, MASS, [(NEW, True), (alta, False)]) is None


def test_mismo_arxiv_id_cuenta_como_mismo_paper():
    alta = sol(arxiv_id="2607.00001")
    hermana = sol(ref_key="OTRA", arxiv_id="2607.00001", is_default=False)
    assert select_pair_reference(alta, True, MASS, [(alta, True), (hermana, False)]) is None


def test_alta_no_por_defecto_y_por_defecto_del_mismo_paper_no_utilizable_cae_a_otro_paper():
    default_sin_masa = sol(mass=PV())
    alta = sol(is_default=False, mass=PV(900.0, 40.0, -40.0, 0))
    active = [(default_sin_masa, True), (alta, False), (OLD, False)]
    picked = select_pair_reference(alta, False, MASS, active)
    assert picked is not None
    assert picked[0] == OLD and picked[2] is ReferenceRole.MOST_RECENT
