"""T83: `ComputeTensions` reconoce la solución propia por `arxiv_id` y por valores.

Sin red, sin Claude, sin BD. Filas REALES de HIP 67522 (fixture
`ps_hip67522_chakraborty2026.csv`: Barber 2024 por defecto sin masa, Chakraborty
2026 con `arxiv_id` 2606.18045) y filas SINTÉTICAS con bibcode de revista
(`2026AJ....`) que representan la versión de revista del mismo paper.
"""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from fakes.catalog import FakeExoplanetCatalog
from fakes.clock import FakeClock
from helpers.archive import fixture_rows, make_archive, make_catalog
from helpers.exoplanet import (
    make_measurement,
    make_own_solution_rule,
    make_period_rule,
    make_reading,
    make_solution,
)
from helpers.measurement_findings import make_arxiv_item

from nocturna.application.use_cases.compute_tensions import ComputeTensions, SkipReason
from nocturna.domain.archive import (
    ArchiveParameterValue,
    ArchiveSolution,
    catalog_solution_from_archive,
)
from nocturna.domain.entities import MeasuredParameter, MeasurementLimit, MeasurementUnit
from nocturna.domain.own_solution import OwnSolutionRule, SolutionProvenance, classify_solution
from nocturna.domain.tension import EvaluationStatus, LimitOutcome
from nocturna.infrastructure.exoplanet_archive.mappers import archive_solution_from_ps_row

pytestmark = pytest.mark.anyio

MASS = MeasuredParameter.MASS
M_E = MeasurementUnit.M_EARTH
FIXTURE = "ps_hip67522_chakraborty2026.csv"
NOW = datetime(2026, 10, 2, tzinfo=UTC)
JOURNAL_BIBCODE = "2026AJ....168..297C"
JOURNAL_REFNAME = (
    "<a refstr=CHAKRABORTY_ET_AL_2026 "
    f"href=https://ui.adsabs.harvard.edu/abs/{JOURNAL_BIBCODE}/abstract "
    "target=ref>Chakraborty et al. 2026</a>"
)
HIP_PAPER = "2609.35979"  # paper de HIP 67522 b y c, posterior a Chakraborty
CHAKRABORTY_PAPER = "2606.18045"  # el propio preprint de Chakraborty et al.


def _rows() -> list[ArchiveSolution]:
    return [archive_solution_from_ps_row(row) for row in fixture_rows(FIXTURE)]


def _barber_and_chakraborty_b():
    barber, chak_b, chak_c = _rows()
    assert barber.is_default and chak_b.arxiv_id == CHAKRABORTY_PAPER and chak_c.mass.lim == 1
    return barber, chak_b, chak_c


def _journal_version(chak_b: ArchiveSolution, *, mass=13.8, err1=1.0, err2=-1.0, **over):
    """Versión de revista del paper de Chakraborty: mismo planeta y referencia,
    sin `arxiv_id`, con bibcode `2026AJ....` y `releasedate` posterior."""
    return replace(
        chak_b,
        pl_refname=JOURNAL_REFNAME,
        ref_key=JOURNAL_BIBCODE,
        arxiv_id=None,
        releasedate=date(2026, 10, 20),
        pl_pubdate="2026-10",
        mass=replace(chak_b.mass, value=mass, err1=err1, err2=err2),
        **over,
    )


def _item(external_id: str, published_at: datetime):
    return make_arxiv_item(external_id, published_at=published_at)


def _compute(archive_rows, *, rule: OwnSolutionRule | None = None):
    catalog, _, seen = make_catalog(archive=make_archive(archive_rows))
    compute = ComputeTensions(
        catalog,
        threshold_sigma=3.0,
        period_rule=make_period_rule(),
        own_solution_rule=rule or make_own_solution_rule(),
        clock=FakeClock(NOW),
    )
    return compute, seen


def _measure_b(value, err_plus, err_minus):
    return make_measurement(value, err_plus, err_minus, planet_name="HIP 67522 b", unit=M_E)


# --- el comportamiento de hoy no cambia -------------------------------------


async def test_paper_posterior_frente_a_chakraborty_b_evaluada_y_c_consistente_con_la_cota():
    item = _item(HIP_PAPER, datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    reading = make_reading(
        item.id,
        (
            _measure_b(25.0, 7.6, 7.8),
            _measure_b(23.1, 15.4, 12.4),
            make_measurement(11.2, 1.4, 1.4, planet_name="HIP 67522 c", unit=M_E),
        ),
    )
    compute, seen = _compute(_rows())

    report = await compute([(item, reading)])

    by_planet = {e.planet_name: e for e in report.evaluations}
    b, c = by_planet["HIP 67522 b"], by_planet["HIP 67522 c"]
    assert b.status == EvaluationStatus.EVALUATED
    assert b.own_solution_key is None
    assert b.result.reference().reference == "Chakraborty et al. 2026"
    assert [x.sigma for x in b.result.comparisons] == [
        pytest.approx(1.42, abs=1e-2),
        pytest.approx(0.75, abs=1e-2),
    ]
    assert c.status == EvaluationStatus.CONSISTENT_WITH_LIMIT
    assert c.limit is not None and c.limit.outcome == LimitOutcome.CONSISTENT
    assert seen == []


async def test_el_propio_preprint_de_chakraborty_es_closed_loop_por_arxiv_id():
    _, chak_b, _ = _barber_and_chakraborty_b()
    item = _item(CHAKRABORTY_PAPER, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    reading = make_reading(item.id, (_measure_b(13.8, 1.0, 1.0),))
    compute, _ = _compute(_rows())

    report = await compute([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.CLOSED_LOOP
    assert evaluation.own_solution_key == chak_b.solution_key
    assert evaluation.result is None


# --- T83: versión de revista del propio paper --------------------------------


async def test_version_de_revista_con_los_mismos_valores_es_closed_loop_por_valor():
    """Hoy saldría `evaluated` con σ = 0 contra la propia solución."""
    barber, chak_b, _ = _barber_and_chakraborty_b()
    journal = _journal_version(chak_b)
    item = _item(CHAKRABORTY_PAPER, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    reading = make_reading(item.id, (_measure_b(13.8, 1.0, 1.0),))
    compute, _ = _compute([barber, journal])

    report = await compute([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.CLOSED_LOOP
    assert evaluation.own_solution_key == journal.solution_key
    assert evaluation.result is None
    assert evaluation.archive_planet_name == "HIP 67522 b"


async def test_preprint_y_version_de_revista_juntos_son_propios_y_la_clave_es_la_menor():
    barber, chak_b, _ = _barber_and_chakraborty_b()
    journal = _journal_version(chak_b)
    item = _item(CHAKRABORTY_PAPER, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    reading = make_reading(item.id, (_measure_b(13.8, 1.0, 1.0),))
    compute, _ = _compute([barber, chak_b, journal])

    report = await compute([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.CLOSED_LOOP
    assert evaluation.own_solution_key == min(chak_b.solution_key, journal.solution_key)


async def test_version_de_revista_se_excluye_de_las_previas_si_hay_otras():
    barber, chak_b, _ = _barber_and_chakraborty_b()
    journal = _journal_version(chak_b)
    other = replace(
        chak_b,
        arxiv_id="2605.00001",
        ref_key="2026arXiv260500001X",
        pl_refname="<a href=https://ui.adsabs.harvard.edu/abs/2026arXiv260500001X/abstract>"
        "Otros et al. 2026</a>",
        ref_text="Otros et al. 2026",
        pl_pubdate="2026-05",
        releasedate=date(2026, 9, 15),
        mass=replace(chak_b.mass, value=20.0, err1=2.0, err2=-2.0),
    )
    item = _item(CHAKRABORTY_PAPER, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    reading = make_reading(item.id, (_measure_b(13.8, 1.0, 1.0),))
    compute, _ = _compute([barber, journal, other])

    report = await compute([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.EVALUATED
    assert evaluation.own_solution_key == journal.solution_key
    assert {c.prior.reference for c in evaluation.result.comparisons} == {"Otros et al. 2026"}
    assert evaluation.result.reference().arxiv_id == "2605.00001"


async def test_version_de_revista_con_valores_revisados_es_ambigua_y_sigue_siendo_previa():
    barber, chak_b, _ = _barber_and_chakraborty_b()
    journal = _journal_version(chak_b, mass=14.6, err1=1.1, err2=-1.1)
    item = _item(CHAKRABORTY_PAPER, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    paper = _measure_b(13.8, 1.0, 1.0)
    reading = make_reading(item.id, (paper,))
    compute, _ = _compute([barber, journal])

    report = await compute([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.EVALUATED
    assert evaluation.own_solution_key is None
    (comparison,) = evaluation.result.comparisons
    assert comparison.sigma == pytest.approx(0.8 / (1.0**2 + 1.1**2) ** 0.5, abs=1e-2)
    reference = evaluation.result.reference()
    assert reference.arxiv_id is None
    assert (
        classify_solution(
            reference,
            external_id=item.external_id,
            published_at=item.published_at,
            measurements=[paper],
            rule=make_own_solution_rule(),
        )
        == SolutionProvenance.AMBIGUOUS
    )


async def test_la_misma_fila_con_pl_pubdate_antiguo_es_independiente_y_se_evalua():
    """Un valor idéntico con `pl_pubdate` de 2024 (tipo Barber) no es el propio paper."""
    barber, chak_b, _ = _barber_and_chakraborty_b()
    old = _journal_version(chak_b, mass=13.8, err1=1.0, err2=-1.0, ref_text="Otro et al. 2024")
    old = replace(old, pl_pubdate="2024-09", releasedate=date(2024, 9, 10))
    item = _item(CHAKRABORTY_PAPER, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    reading = make_reading(item.id, (_measure_b(13.8, 1.0, 1.0),))
    compute, _ = _compute([barber, old])

    report = await compute([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.EVALUATED
    assert evaluation.own_solution_key is None
    (comparison,) = evaluation.result.comparisons
    assert comparison.sigma == 0.0
    assert catalog_solution_from_archive(old, MASS, is_default=False) is not None


async def test_la_tolerancia_de_la_regla_se_aplica_desde_el_constructor():
    barber, chak_b, _ = _barber_and_chakraborty_b()
    journal = _journal_version(chak_b, mass=13.9, err1=1.0, err2=-1.0)  # 0,72 % de 13,8
    item = _item(CHAKRABORTY_PAPER, datetime(2026, 6, 20, 8, 0, tzinfo=UTC))
    reading = make_reading(item.id, (_measure_b(13.8, 1.0, 1.0),))

    loose, _ = _compute([barber, journal], rule=OwnSolutionRule(0.01, 6))
    strict, _ = _compute([barber, journal], rule=OwnSolutionRule(0.001, 6))

    (with_loose,) = (await loose([(item, reading)])).evaluations
    (with_strict,) = (await strict([(item, reading)])).evaluations
    assert with_loose.status == EvaluationStatus.CLOSED_LOOP
    assert with_strict.status == EvaluationStatus.EVALUATED


async def test_el_constructor_exige_own_solution_rule():
    catalog, _, _ = make_catalog(archive=make_archive(_rows()))

    with pytest.raises(TypeError):
        ComputeTensions(  # type: ignore[call-arg]
            catalog,
            threshold_sigma=3.0,
            period_rule=make_period_rule(),
            clock=FakeClock(NOW),
        )


async def test_fila_de_periodo_con_valor_identico_sigue_siendo_previa_y_evaluada():
    barber, chak_b, _ = _barber_and_chakraborty_b()
    period = ArchiveParameterValue(value=24.1386, err1=0.0001, err2=-0.0001, lim=0)
    row = replace(chak_b, arxiv_id=None, pl_pubdate="2026-10", period=period)
    item = _item(HIP_PAPER, datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    reading = make_reading(
        item.id,
        (
            make_measurement(
                24.1386,
                0.0001,
                0.0001,
                planet_name="HIP 67522 b",
                unit=MeasurementUnit.DAY,
                parameter=MeasuredParameter.PERIOD,
            ),
        ),
    )
    compute, _ = _compute([barber, row])

    report = await compute([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.EVALUATED
    assert evaluation.own_solution_key is None


# --- T92: el periodo hereda la procedencia de la solución propia -------------

PERIOD_PAPER = 6.0
R_E = MeasurementUnit.R_EARTH


def _hd715_row(chak_b: ArchiveSolution) -> ArchiveSolution:
    """Fila R sin `arxiv_id`, `pl_pubdate` plausible y masa, radio y periodo del paper."""
    return replace(
        chak_b,
        arxiv_id=None,
        ref_key="2026AJ....168..999X",
        pl_refname="<a href=https://ui.adsabs.harvard.edu/abs/2026AJ....168..999X/abstract>"
        "Autores et al. 2026</a>",
        ref_text="Autores et al. 2026",
        pl_pubdate="2026-10",
        releasedate=date(2026, 10, 20),
        mass=ArchiveParameterValue(value=13.8, err1=1.0, err2=-1.0, lim=0),
        radius=ArchiveParameterValue(value=0.9, err1=0.05, err2=-0.05, lim=0),
        period=ArchiveParameterValue(value=PERIOD_PAPER, err1=0.01, err2=-0.01, lim=0),
    )


def _without_radius_and_period(solution: ArchiveSolution) -> ArchiveSolution:
    """Solo la fila R aporta radio y periodo (la de Barber los traería como previa legítima)."""
    empty = ArchiveParameterValue()
    return replace(solution, radius=empty, period=empty)


def _hd715_reading(item):
    def measure(value, parameter, unit):
        return make_measurement(
            value, 0.05, 0.05, planet_name="HIP 67522 b", parameter=parameter, unit=unit
        )

    return make_reading(
        item.id,
        (
            _measure_b(13.8, 1.0, 1.0),
            measure(0.9, MeasuredParameter.RADIUS, R_E),
            measure(PERIOD_PAPER, MeasuredParameter.PERIOD, MeasurementUnit.DAY),
        ),
    )


async def test_hd715_masa_radio_y_periodo_de_la_misma_fila_son_closed_loop_con_la_misma_clave():
    barber, chak_b, _ = _barber_and_chakraborty_b()
    barber = _without_radius_and_period(barber)
    row = _hd715_row(chak_b)
    item = _item(HIP_PAPER, datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    compute, _ = _compute([barber, row])

    report = await compute([(item, _hd715_reading(item))])

    by_parameter = {e.parameter: e for e in report.evaluations}
    assert set(by_parameter) == {
        MeasuredParameter.MASS,
        MeasuredParameter.RADIUS,
        MeasuredParameter.PERIOD,
    }
    for parameter in (MeasuredParameter.MASS, MeasuredParameter.RADIUS):
        assert by_parameter[parameter].status == EvaluationStatus.CLOSED_LOOP
        assert by_parameter[parameter].own_solution_key == row.solution_key
    period = by_parameter[MeasuredParameter.PERIOD]
    assert period.status == EvaluationStatus.CLOSED_LOOP
    assert period.own_solution_key == row.solution_key


async def test_hd715_periodo_con_otra_fila_independiente_anterior_se_evalua_sin_la_fila_propia():
    barber, chak_b, _ = _barber_and_chakraborty_b()
    barber = _without_radius_and_period(barber)
    row = _hd715_row(chak_b)
    earlier = replace(
        chak_b,
        arxiv_id="2401.00001",
        ref_key="2024arXiv240100001X",
        pl_refname="<a href=https://ui.adsabs.harvard.edu/abs/2024arXiv240100001X/abstract>"
        "Previos et al. 2024</a>",
        ref_text="Previos et al. 2024",
        pl_pubdate="2024-01",
        releasedate=date(2024, 2, 1),
        period=ArchiveParameterValue(value=6.2, err1=0.02, err2=-0.02, lim=0),
    )
    item = _item(HIP_PAPER, datetime(2026, 9, 28, 8, 0, tzinfo=UTC))
    compute, _ = _compute([barber, row, earlier])

    report = await compute([(item, _hd715_reading(item))])

    period = {e.parameter: e for e in report.evaluations}[MeasuredParameter.PERIOD]
    assert period.status == EvaluationStatus.EVALUATED
    references = {c.prior.reference for c in period.result.comparisons}
    assert "Previos et al. 2024" in references
    assert row.ref_text not in references


# --- T92: casos de contorno de la exclusión transversal ----------------------

R_KEY = "fila-R"
PLANET_B = "V1298 Tau b"
PLANET_C = "V1298 Tau c"
RADIUS = MeasuredParameter.RADIUS
PERIOD = MeasuredParameter.PERIOD
DAY = MeasurementUnit.DAY
PAPER_ID = "2609.35979"
PAPER_DATE = datetime(2026, 9, 28, 8, 0, tzinfo=UTC)


def _fake_compute(catalog: FakeExoplanetCatalog) -> ComputeTensions:
    return ComputeTensions(
        catalog,
        threshold_sigma=3.0,
        period_rule=make_period_rule(),
        own_solution_rule=make_own_solution_rule(),
        clock=FakeClock(NOW),
    )


def _sol(value, err, parameter, unit, key, **over):
    """Solución sin `arxiv_id`, con `pl_pubdate` plausible (ni propia ni ajena por fecha)."""
    fields = {
        "planet_name": PLANET_B,
        "parameter": parameter,
        "unit": unit,
        "solution_key": key,
        "pl_pubdate": "2026-10",
        "reference": f"ref {key}",
    }
    return make_solution(value, err, err, **{**fields, **over})


def _independent(value, err, parameter, unit, key, **over):
    return _sol(
        value, err, parameter, unit, key, arxiv_id="2401.00001", pl_pubdate="2024-01", **over
    )


def _meas(value, err, parameter, unit, planet=PLANET_B, **over):
    return make_measurement(
        value, err, err, planet_name=planet, parameter=parameter, unit=unit, **over
    )


def _fake_catalog(solutions, planets=(PLANET_B,), **kwargs):
    return FakeExoplanetCatalog(aliases={p: p for p in planets}, solutions=solutions, **kwargs)


async def _run_one(catalog, measurements):
    item = _item(PAPER_ID, PAPER_DATE)
    reading = make_reading(item.id, tuple(measurements))
    report = await _fake_compute(catalog)([(item, reading)])
    return report


def _by_key(report):
    return {(e.planet_name, e.parameter): e for e in report.evaluations}


async def test_t92_masa_propia_por_valor_y_radio_a_mas_del_1_por_ciento_excluye_la_fila_del_radio():
    row_mass = _sol(13.8, 1.0, MASS, M_E, R_KEY)
    row_radius = _sol(0.9, 0.05, RADIUS, R_E, R_KEY)
    catalog = _fake_catalog({(PLANET_B, MASS): [row_mass], (PLANET_B, RADIUS): [row_radius]})

    report = await _run_one(
        catalog,
        [_meas(13.8, 1.0, MASS, M_E), _meas(0.95, 0.05, RADIUS, R_E)],  # 5,6 % de 0,9
    )

    by = _by_key(report)
    radius = by[(PLANET_B, RADIUS)]
    assert (
        classify_solution(
            row_radius,
            external_id=PAPER_ID,
            published_at=PAPER_DATE,
            measurements=[_meas(0.95, 0.05, RADIUS, R_E)],
            rule=make_own_solution_rule(),
        )
        == SolutionProvenance.AMBIGUOUS
    )
    assert radius.status == EvaluationStatus.CLOSED_LOOP
    assert radius.own_solution_key == R_KEY
    assert radius.result is None
    assert by[(PLANET_B, MASS)].status == EvaluationStatus.CLOSED_LOOP


async def test_t92_el_radio_se_compara_solo_con_las_demas_previas_y_nunca_con_la_fila_propia():
    row_mass = _sol(13.8, 1.0, MASS, M_E, R_KEY)
    row_radius = _sol(0.9, 0.05, RADIUS, R_E, R_KEY, is_default=True)
    other = _independent(1.1, 0.1, RADIUS, R_E, "otra")
    catalog = _fake_catalog({(PLANET_B, MASS): [row_mass], (PLANET_B, RADIUS): [row_radius, other]})

    report = await _run_one(catalog, [_meas(13.8, 1.0, MASS, M_E), _meas(0.95, 0.05, RADIUS, R_E)])

    radius = _by_key(report)[(PLANET_B, RADIUS)]
    assert radius.status == EvaluationStatus.EVALUATED
    assert radius.own_solution_key == R_KEY
    assert [c.prior.solution_key for c in radius.result.comparisons] == ["otra"]


async def test_t92_la_cota_superior_de_masa_de_la_fila_propia_no_se_usa_como_limite():
    row_mass_limit = _sol(30.0, None, MASS, M_E, R_KEY, limit=MeasurementLimit.UPPER)
    row_radius = _sol(0.9, 0.05, RADIUS, R_E, R_KEY)
    catalog = _fake_catalog({(PLANET_B, MASS): [row_mass_limit], (PLANET_B, RADIUS): [row_radius]})

    # 40 M_earth supera la cota de 30: sin la exclusión saldría incompatible con la cota.
    report = await _run_one(catalog, [_meas(40.0, 1.0, MASS, M_E), _meas(0.9, 0.05, RADIUS, R_E)])

    mass = _by_key(report)[(PLANET_B, MASS)]
    assert mass.status == EvaluationStatus.CLOSED_LOOP
    assert mass.status not in (
        EvaluationStatus.CONSISTENT_WITH_LIMIT,
        EvaluationStatus.INCOMPATIBLE_WITH_LIMIT,
    )
    assert mass.limit is None
    assert mass.own_solution_key == R_KEY


async def test_t92_la_cota_superior_ajena_si_se_usa_aunque_exista_una_fila_propia_con_cota():
    own_limit = _sol(30.0, None, MASS, M_E, R_KEY, limit=MeasurementLimit.UPPER)
    foreign_limit = _independent(35.0, None, MASS, M_E, "ajena", limit=MeasurementLimit.UPPER)
    row_radius = _sol(0.9, 0.05, RADIUS, R_E, R_KEY)
    catalog = _fake_catalog(
        {(PLANET_B, MASS): [own_limit, foreign_limit], (PLANET_B, RADIUS): [row_radius]}
    )

    report = await _run_one(catalog, [_meas(10.0, 1.0, MASS, M_E), _meas(0.9, 0.05, RADIUS, R_E)])

    mass = _by_key(report)[(PLANET_B, MASS)]
    assert mass.status == EvaluationStatus.CONSISTENT_WITH_LIMIT
    assert mass.limit.limit.solution_key == "ajena"
    assert mass.own_solution_key == R_KEY


async def test_t92_sin_la_fila_propia_en_la_lista_de_masa_su_clave_propia_queda_en_none():
    row_radius = _sol(0.9, 0.05, RADIUS, R_E, R_KEY)  # R no aporta masa (p. ej. pl_bmassprov)
    catalog = _fake_catalog({(PLANET_B, MASS): [], (PLANET_B, RADIUS): [row_radius]})

    report = await _run_one(catalog, [_meas(13.8, 1.0, MASS, M_E), _meas(0.9, 0.05, RADIUS, R_E)])

    by = _by_key(report)
    assert by[(PLANET_B, RADIUS)].status == EvaluationStatus.CLOSED_LOOP
    mass = by[(PLANET_B, MASS)]
    assert mass.status == EvaluationStatus.AWAITING_REFERENCE
    assert mass.own_solution_key is None


async def test_t92_sin_la_fila_propia_en_la_lista_de_masa_otra_previa_sigue_evaluandose():
    row_radius = _sol(0.9, 0.05, RADIUS, R_E, R_KEY)
    prior = _independent(20.0, 2.0, MASS, M_E, "previa", is_default=True)
    catalog = _fake_catalog({(PLANET_B, MASS): [prior], (PLANET_B, RADIUS): [row_radius]})

    report = await _run_one(catalog, [_meas(13.8, 1.0, MASS, M_E), _meas(0.9, 0.05, RADIUS, R_E)])

    mass = _by_key(report)[(PLANET_B, MASS)]
    assert mass.status == EvaluationStatus.EVALUATED
    assert mass.own_solution_key is None
    assert [c.prior.solution_key for c in mass.result.comparisons] == ["previa"]


async def test_t92_la_fila_propia_de_un_planeta_no_afecta_a_otro_de_la_misma_lectura():
    row_b_mass = _sol(13.8, 1.0, MASS, M_E, "fila-b")
    row_b_period = _sol(6.0, 0.01, PERIOD, DAY, "fila-b")
    prior_c = _independent(
        20.0, 2.0, PERIOD, DAY, "previa-c", planet_name=PLANET_C, is_default=True
    )
    row_c_period_like_b = _sol(6.0, 0.01, PERIOD, DAY, "fila-c", planet_name=PLANET_C)
    catalog = _fake_catalog(
        {
            (PLANET_B, MASS): [row_b_mass],
            (PLANET_B, PERIOD): [row_b_period],
            (PLANET_C, PERIOD): [row_c_period_like_b, prior_c],
        },
        planets=(PLANET_B, PLANET_C),
    )

    report = await _run_one(
        catalog,
        [
            _meas(13.8, 1.0, MASS, M_E),
            _meas(6.0, 0.01, PERIOD, DAY),
            _meas(6.0, 0.01, PERIOD, DAY, planet=PLANET_C),
        ],
    )

    by = _by_key(report)
    assert by[(PLANET_B, PERIOD)].status == EvaluationStatus.CLOSED_LOOP
    assert by[(PLANET_B, PERIOD)].own_solution_key == "fila-b"
    c = by[(PLANET_C, PERIOD)]
    assert c.own_solution_key is None
    assert c.status == EvaluationStatus.EVALUATED
    assert {x.prior.solution_key for x in c.result.comparisons} == {"fila-c", "previa-c"}


async def test_t92_propia_por_arxiv_id_en_la_masa_no_cambia_y_no_arrastra_a_otros_parametros():
    own = _sol(13.8, 1.0, MASS, M_E, R_KEY, arxiv_id=PAPER_ID)
    foreign_radius = _independent(1.0, 0.1, RADIUS, R_E, "radio-ajeno", is_default=True)
    catalog = _fake_catalog({(PLANET_B, MASS): [own], (PLANET_B, RADIUS): [foreign_radius]})

    report = await _run_one(catalog, [_meas(13.8, 1.0, MASS, M_E), _meas(0.9, 0.05, RADIUS, R_E)])

    by = _by_key(report)
    assert by[(PLANET_B, MASS)].status == EvaluationStatus.CLOSED_LOOP
    assert by[(PLANET_B, MASS)].own_solution_key == R_KEY
    assert by[(PLANET_B, RADIUS)].status == EvaluationStatus.EVALUATED
    assert by[(PLANET_B, RADIUS)].own_solution_key is None


async def test_t92_fallo_de_resolucion_de_un_planeta_falla_todos_sus_grupos_y_evalua_los_demas():
    row_c = _independent(20.0, 2.0, MASS, M_E, "previa-c", planet_name=PLANET_C, is_default=True)
    catalog = _fake_catalog(
        {
            (PLANET_B, MASS): [_sol(13.8, 1.0, MASS, M_E, R_KEY)],
            (PLANET_B, RADIUS): [_sol(0.9, 0.05, RADIUS, R_E, R_KEY)],
            (PLANET_C, MASS): [row_c],
        },
        planets=(PLANET_B, PLANET_C),
        failing={PLANET_B},
    )

    report = await _run_one(
        catalog,
        [
            _meas(13.8, 1.0, MASS, M_E),
            _meas(0.9, 0.05, RADIUS, R_E),
            _meas(15.0, 1.0, MASS, M_E, planet=PLANET_C),
        ],
    )

    assert {(f.planet_name, f.parameter) for f in report.failures} == {
        (PLANET_B, MASS),
        (PLANET_B, RADIUS),
    }
    (only,) = report.evaluations
    assert (only.planet_name, only.parameter) == (PLANET_C, MASS)
    assert only.status == EvaluationStatus.EVALUATED
    assert catalog.solutions_calls == [(PLANET_C, MASS)]


async def test_t92_periodo_con_ttv_flag_sigue_saliendo_period_ttv_sin_evaluacion():
    row_mass = _sol(13.8, 1.0, MASS, M_E, R_KEY)
    ttv_period = _sol(6.0, 0.01, PERIOD, DAY, R_KEY, ttv_flag=True)
    catalog = _fake_catalog({(PLANET_B, MASS): [row_mass], (PLANET_B, PERIOD): [ttv_period]})

    report = await _run_one(catalog, [_meas(13.8, 1.0, MASS, M_E), _meas(6.0, 0.01, PERIOD, DAY)])

    assert [(s.measurement.parameter, s.reason) for s in report.skipped] == [
        (PERIOD, SkipReason.PERIOD_TTV)
    ]
    assert [e.parameter for e in report.evaluations] == [MASS]
    assert report.evaluations[0].status == EvaluationStatus.CLOSED_LOOP
