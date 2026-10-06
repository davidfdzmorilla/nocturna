"""T83: `ComputeTensions` reconoce la solución propia por `arxiv_id` y por valores.

Sin red, sin Claude, sin BD. Filas REALES de HIP 67522 (fixture
`ps_hip67522_chakraborty2026.csv`: Barber 2024 por defecto sin masa, Chakraborty
2026 con `arxiv_id` 2606.18045) y filas SINTÉTICAS con bibcode de revista
(`2026AJ....`) que representan la versión de revista del mismo paper.
"""

from dataclasses import replace
from datetime import UTC, date, datetime

import pytest
from fakes.clock import FakeClock
from helpers.archive import fixture_rows, make_archive, make_catalog
from helpers.exoplanet import (
    make_measurement,
    make_own_solution_rule,
    make_period_rule,
    make_reading,
)
from helpers.measurement_findings import make_arxiv_item

from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.domain.archive import (
    ArchiveParameterValue,
    ArchiveSolution,
    catalog_solution_from_archive,
)
from nocturna.domain.entities import MeasuredParameter, MeasurementUnit
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
