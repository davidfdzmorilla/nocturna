"""HIP 67522 desde filas del archivo (T88): fila de `ps` -> `ArchiveSolution` ->
`CatalogSolution` -> `ComputeTensions`. Fixture construida a mano, ver su README."""

from datetime import UTC, date, datetime

import pytest
from fakes.clock import FakeClock
from helpers.archive import fixture_rows, make_archive, make_catalog
from helpers.exoplanet import make_item, make_measurement, make_period_rule, make_reading

from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.domain.archive import catalog_solution_from_archive
from nocturna.domain.entities import MeasuredParameter, MeasurementUnit
from nocturna.domain.tension import EvaluationStatus, LimitOutcome
from nocturna.infrastructure.exoplanet_archive.mappers import (
    archive_solution_from_ps_row,
    arxiv_id_from_refname,
)

pytestmark = pytest.mark.anyio

MASS = MeasuredParameter.MASS
M_E = MeasurementUnit.M_EARTH
FIXTURE = "ps_hip67522_chakraborty2026.csv"
CHAKRABORTY_REFNAME = (
    "<a refstr=CHAKRABORTY_ET_AL_2026 "
    "href=https://ui.adsabs.harvard.edu/abs/2026arXiv260618045C/abstract "
    "target=ref>Chakraborty et al. 2026</a>"
)


def _archive_solutions():
    return [archive_solution_from_ps_row(row) for row in fixture_rows(FIXTURE)]


def test_arxiv_id_del_refname_de_chakraborty():
    assert arxiv_id_from_refname(CHAKRABORTY_REFNAME) == "2606.18045"
    assert {r["pl_refname"] for r in fixture_rows(FIXTURE)} >= {CHAKRABORTY_REFNAME}


def test_filas_del_archivo_a_catalog_solution():
    barber, chak_b, chak_c = _archive_solutions()

    assert (
        barber.is_default and catalog_solution_from_archive(barber, MASS, is_default=True) is None
    )
    solution = catalog_solution_from_archive(chak_b, MASS, is_default=False)
    assert solution is not None
    assert (solution.value, solution.err_plus, solution.err_minus) == (13.8, 1.0, 1.0)
    assert solution.arxiv_id == "2606.18045" and solution.releasedate == date(2026, 10, 1)
    assert solution.pl_pubdate == "2026-09" and solution.reference == "Chakraborty et al. 2026"
    limit = catalog_solution_from_archive(chak_c, MASS, is_default=False)
    assert limit is not None and limit.value == 22.0 and limit.err_plus is None


async def test_hip67522_b_evaluada_con_chakraborty_y_c_consistente_con_la_cota():
    catalog, _, seen = make_catalog(archive=make_archive(_archive_solutions()))
    item = make_item("2610.00001")
    reading = make_reading(
        item.id,
        (
            make_measurement(25.0, 7.6, 7.8, planet_name="HIP 67522 b", unit=M_E),
            make_measurement(23.1, 15.4, 12.4, planet_name="HIP 67522 b", unit=M_E),
            make_measurement(11.2, 1.4, 1.4, planet_name="HIP 67522 c", unit=M_E),
        ),
    )
    compute = ComputeTensions(
        catalog,
        threshold_sigma=3.0,
        period_rule=make_period_rule(),
        clock=FakeClock(datetime(2026, 10, 2, tzinfo=UTC)),
    )

    report = await compute([(item, reading)])

    by_planet = {e.planet_name: e for e in report.evaluations}
    b, c = by_planet["HIP 67522 b"], by_planet["HIP 67522 c"]
    assert b.status == EvaluationStatus.EVALUATED
    assert b.result is not None and b.result.reference().reference == "Chakraborty et al. 2026"
    assert [x.sigma for x in b.result.comparisons] == [
        pytest.approx(1.42, abs=1e-2),
        pytest.approx(0.75, abs=1e-2),
    ]
    assert not b.is_candidate(3.0)
    assert c.status == EvaluationStatus.CONSISTENT_WITH_LIMIT
    assert c.limit is not None and c.limit.outcome == LimitOutcome.CONSISTENT
    assert seen == []
