"""Reglas de T85 con los números reales de HD 202206 c (Cao 2026 frente a Benedict 2017)."""

from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from nocturna.domain.archive import ArchiveParameterValue, ArchiveSolution
from nocturna.domain.entities import (
    MeasuredParameter,
    ReferenceRole,
    SolutionTensionLabel,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.solution_tension import (
    SkipReason,
    SolutionTensionRule,
    build_solution_tension,
    compare_solutions,
    is_old_paper,
    select_pair_reference,
    solution_tension_skip_reason,
    usable_solution,
)
from nocturna.domain.tension import PeriodRule

MASS = MeasuredParameter.MASS
PERIOD = MeasuredParameter.PERIOD
RADIUS = MeasuredParameter.RADIUS
NOW = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)


def sol(**kw) -> ArchiveSolution:
    base = {
        "pl_name": "HD 202206 c",
        "hostname": "HD 202206",
        "pl_refname": "x",
        "ref_key": "2026A&A...711A.229C",
        "ref_text": "Cao et al. 2026",
        "arxiv_id": None,
        "soltype": "Published Confirmed",
        "releasedate": date(2026, 8, 20),
        "pl_pubdate": "2026-07",
        "is_default": True,
        "mass": ArchiveParameterValue(982.09, 44.50, -44.50, 0),
        "radius": ArchiveParameterValue(),
        "period": ArchiveParameterValue(1299.22, 3.21, -2.76, 0),
        "pl_bmassprov": "Mass",
        "st_rad": ArchiveParameterValue(),
        "st_mass": ArchiveParameterValue(),
        "discoverymethod": "Radial Velocity",
        "ttv_flag": False,
        "pl_controv_flag": False,
    }
    base.update(kw)
    return ArchiveSolution(**base)


NEW = sol()
OLD = sol(
    ref_key="2017AJ....153..258B",
    ref_text="Benedict & Harrison 2017",
    releasedate=date(2017, 2, 1),
    pl_pubdate="2017-03",
    is_default=False,
    mass=ArchiveParameterValue(5689.0, 922.0, -572.0, 0),
    period=ArchiveParameterValue(1260.0, 11.0, -11.0, 0),
)
MSINI = sol(
    ref_key="2005A&A...440..751C",
    ref_text="Correia 2005",
    releasedate=date(2005, 1, 1),
    pl_pubdate="2005-06",
    is_default=False,
    pl_bmassprov="Msini",
    mass=ArchiveParameterValue(700.0, 50.0, -50.0, 0),
    period=ArchiveParameterValue(1383.0, 18.0, -18.0, 0),
)
ACTIVE = [(NEW, True), (OLD, False), (MSINI, False)]
RULE = SolutionTensionRule(
    threshold_sigma=3.0,
    since=date(2026, 8, 1),
    max_pubdate_age_months=24,
    period_rule=PeriodRule(1e-4, 1.0 / 24, 0.01, 5),
)


def build(parameter, new=NEW, new_default=True, active=ACTIVE):
    picked = select_pair_reference(new, new_default, parameter, active)
    assert picked is not None
    reference, ref_default, role = picked
    return build_solution_tension(
        new, new_default, reference, ref_default, role, parameter, active, RULE, NOW
    )


def test_masa_hd_202206_c_8_2_sigma_frente_a_benedict():
    tension = build(MASS)
    assert tension.reference_solution_key == OLD.solution_key
    assert tension.reference_role is ReferenceRole.MOST_RECENT
    assert tension.sigma == pytest.approx(8.2, abs=0.1)
    assert tension.skip_reason is None
    assert tension.others == ()  # la Msini no sirve para masa


def test_periodo_3_46_sigma_pasa_la_diferencia_minima():
    tension = build(PERIOD)
    assert tension.sigma == pytest.approx(3.46, abs=0.05)
    assert tension.skip_reason is None
    assert tension.period_check.min_difference_met
    assert not tension.period_check.alias_suspected
    # la Msini (Published Confirmed) sí aporta contexto en periodo
    assert len(tension.others) == 1


def test_radio_sin_valor_utilizable_no_genera_par():
    assert usable_solution(NEW, RADIUS, is_default=True) is None


def test_la_masa_msini_no_es_utilizable():
    assert usable_solution(MSINI, MASS, is_default=False) is None


def test_sigma_usa_el_error_que_mira_al_otro_valor():
    a = usable_solution(NEW, MASS, is_default=True)
    b = usable_solution(OLD, MASS, is_default=False)
    # a por encima de b: minus_a (44.5) y plus_b (922)
    assert compare_solutions(a, b) == pytest.approx((5689 - 982.09) / (44.5**2 + 572**2) ** 0.5)
    # invertida: b por encima de a -> minus_b y plus_a
    assert compare_solutions(b, a) == pytest.approx((5689 - 982.09) / (572**2 + 44.5**2) ** 0.5)


def test_alta_no_por_defecto_usa_la_por_defecto_como_referencia():
    picked = select_pair_reference(OLD, False, MASS, ACTIVE)
    assert picked == (NEW, True, ReferenceRole.DEFAULT)


def test_sin_referencia_si_no_hay_otra_utilizable():
    assert select_pair_reference(NEW, True, MASS, [(NEW, True)]) is None
    assert select_pair_reference(OLD, False, MASS, [(OLD, False)]) is None


def test_misma_referencia_por_ref_key_o_arxiv_id():
    same = sol(ref_key="OTRO", arxiv_id="2607.00001", is_default=False)
    new = sol(arxiv_id="2607.00001")
    assert (
        solution_tension_skip_reason(new, same, MASS, sigma=9.0, period_check=None, rule=RULE)
        is SkipReason.SAME_REFERENCE
    )


def test_solo_published_confirmed():
    cand = sol(soltype="Published Candidate", is_default=False, ref_key="X")
    assert (
        solution_tension_skip_reason(NEW, cand, MASS, sigma=9.0, period_check=None, rule=RULE)
        is SkipReason.NOT_PUBLISHED_CONFIRMED
    )


@pytest.mark.parametrize(
    ("pubdate", "old"),
    [("2024-08", False), ("2024-07", True), (None, False), ("basura", False), ("2026-07", False)],
)
def test_old_paper_con_24_meses(pubdate, old):
    assert is_old_paper(sol(pl_pubdate=pubdate, releasedate=date(2026, 8, 20)), RULE) is old


def test_ttv_en_cualquiera_excluye_el_periodo_pero_no_la_masa():
    ttv = sol(ttv_flag=True)
    assert (
        solution_tension_skip_reason(ttv, OLD, PERIOD, sigma=9.0, period_check=None, rule=RULE)
        is SkipReason.PERIOD_TTV
    )
    assert (
        solution_tension_skip_reason(ttv, OLD, MASS, sigma=9.0, period_check=None, rule=RULE)
        is None
    )


def test_por_debajo_del_umbral():
    assert (
        solution_tension_skip_reason(NEW, OLD, MASS, sigma=2.99, period_check=None, rule=RULE)
        is SkipReason.BELOW_THRESHOLD
    )


def test_etiquetas_modelo_y_controversia():
    twin = sol(ref_key=NEW.ref_key, mass=ArchiveParameterValue(900.0, 40.0, -40.0, 0))
    controversial = sol(pl_controv_flag=True)
    tension = build(
        MASS, new=controversial, active=[(controversial, True), (OLD, False), (twin, False)]
    )
    assert tension.labels == (
        SolutionTensionLabel.MODEL_DEPENDENT,
        SolutionTensionLabel.CONTROVERSIAL,
    )


def test_regla_no_admite_umbral_cero():
    with pytest.raises(InvariantViolation):
        SolutionTensionRule(0.0, date(2026, 8, 1), 24, RULE.period_rule)
