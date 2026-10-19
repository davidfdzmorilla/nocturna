"""T95 contra PostgreSQL: HD 65216 c, periodo. La vía arXiv ya publicó el
`catalog_tension` y la alta del archivo cita el mismo paper: se excluye. Sin Claude."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

import pytest
from factories import make_item, make_reading, make_run
from helpers.cross_path_dedup import (
    HD65216_ARXIV_ID,
    arxiv_evaluation,
    arxiv_finding,
)
from test_archive_repository import save, snapshot, solution

from nocturna import cli
from nocturna.domain.archive import ArchiveParameterValue, catalog_solution_from_archive
from nocturna.domain.entities import (
    ITEM_SOURCE_EXOPLANET_ARCHIVE,
    FindingType,
    MeasuredParameter,
    ReferenceRole,
)
from nocturna.domain.solution_tension import SolutionTension
from nocturna.domain.tension import PeriodCheck
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
    SqlAlchemySolutionTensionRepository,
    SqlAlchemyTensionEvaluationRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

REAL_PIPELINE_TOML = Path(__file__).resolve().parents[3] / "config" / "pipeline.toml"


@pytest.mark.parametrize("published", [True, False])
def test_hd65216c_periodo_con_filas_reales_se_excluye_como_same_paper_arxiv(
    db_session_factory, published: bool
) -> None:
    """Publicado o no: el hallazgo arXiv existente basta para excluir."""
    config = load_pipeline_config(REAL_PIPELINE_TOML)
    arxiv_item = make_item(external_id=HD65216_ARXIV_ID)
    planet = "HD 65216 c"
    wallace = solution(
        planet,
        "Wallace et al. 2026",
        mass=None,
        is_default=True,
        arxiv_id=HD65216_ARXIV_ID,
        releasedate=date(2026, 10, 8),
        period=ArchiveParameterValue(value=1100.0, err1=2.0, err2=-2.0, lim=0),
    )
    wittenmyer = solution(
        planet,
        "Wittenmyer et al. 2019",
        mass=None,
        arxiv_id="1901.00001",
        releasedate=date(2026, 7, 1),
        period=ArchiveParameterValue(value=1000.0, err1=8.0, err2=-8.0, lim=0),
    )
    archive_item = make_item(
        source=ITEM_SOURCE_EXOPLANET_ARCHIVE,
        external_id=wallace.solution_key,
        categories=["exoplanet_archive"],
    )
    run = make_run()
    reading = make_reading(arxiv_item.id)
    evaluation = arxiv_evaluation(
        arxiv_item.id,
        planet="HD 65216 c",
        parameter=MeasuredParameter.PERIOD,
        reading_id=reading.id,
    )
    period = MeasuredParameter.PERIOD
    new = catalog_solution_from_archive(wallace, period, is_default=True)
    reference = catalog_solution_from_archive(wittenmyer, period, is_default=False)
    assert new is not None and reference is not None
    tension = SolutionTension(
        new_solution_key=wallace.solution_key,
        reference_solution_key=wittenmyer.solution_key,
        planet_name=planet,
        parameter=period,
        new=new,
        reference=reference,
        reference_role=ReferenceRole.MOST_RECENT,
        computed_at=datetime(2026, 10, 14, 4, 0, tzinfo=UTC),
        sigma=10.09,
        period_check=PeriodCheck(min_difference_met=True, alias_suspected=False),
        item_id=archive_item.id,
    )
    with unit_of_work(db_session_factory) as session:
        save(SqlAlchemyArchiveRepository(session), snapshot(), [wittenmyer, wallace])
        SqlAlchemyItemRepository(session).add_many([arxiv_item, archive_item])
        SqlAlchemyRunRepository(session).add(run)
        SqlAlchemyReadingRepository(session).add(reading)
        SqlAlchemyTensionEvaluationRepository(session).add(evaluation)
        SqlAlchemyFindingRepository(session).add(
            arxiv_finding(
                FindingType.CATALOG_TENSION,
                item_id=arxiv_item.id,
                run_id=run.id,
                evaluation_id=evaluation.id,
                published=published,
            )
        )
        SqlAlchemySolutionTensionRepository(session).add(tension)

    @contextmanager
    def _work():
        with unit_of_work(db_session_factory) as session:
            yield cli._solution_tension_writer_work(session)

    pending = cli.select_solution_tensions_from_config(config, candidates_work=_work).pending()

    assert pending.candidates == ()
    assert pending.skipped == ((tension.id, "same_paper_arxiv"),)
