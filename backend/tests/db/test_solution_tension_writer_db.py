"""T85.e contra PostgreSQL: `solution_tension_ids_with_finding`, siembra del humo y
`SelectSolutionTensions` sobre filas reales. Sin Claude."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

from factories import make_finding
from helpers.solution_tension_smoke import seed_hd202206c_solution_tension

from nocturna import cli
from nocturna.domain.entities import FindingType
from nocturna.domain.solution_tension import solution_tension_data_from
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.repositories import SqlAlchemyFindingRepository
from nocturna.infrastructure.db.session import unit_of_work

REAL_PIPELINE_TOML = Path(__file__).resolve().parents[3] / "config" / "pipeline.toml"


def test_ids_con_finding_incluye_publicados_o_no_y_solo_de_ese_tipo(db_session_factory) -> None:
    config = load_pipeline_config(REAL_PIPELINE_TOML)
    seeded = seed_hd202206c_solution_tension(db_session_factory, config, run_budget_tokens=26_000)
    with unit_of_work(db_session_factory) as session:
        assert (
            SqlAlchemyFindingRepository(session).solution_tension_ids_with_finding() == frozenset()
        )
        data = solution_tension_data_from(
            seeded.mass, threshold_sigma=3.0, archive_url="https://archive.example/x"
        )
        SqlAlchemyFindingRepository(session).add(
            make_finding(
                seeded.item.id,
                seeded.run_id,
                type=FindingType.SOLUTION_TENSION,
                solution_tension=data,
                solution_tension_id=seeded.mass.id,
            )
        )
    with unit_of_work(db_session_factory) as session:
        ids = SqlAlchemyFindingRepository(session).solution_tension_ids_with_finding()
    assert ids == frozenset({seeded.mass.id})


def test_la_siembra_del_humo_deja_una_tension_de_masa_elegible(db_session_factory) -> None:
    config = load_pipeline_config(REAL_PIPELINE_TOML)
    seeded = seed_hd202206c_solution_tension(db_session_factory, config, run_budget_tokens=26_000)

    @contextmanager
    def _work():
        with unit_of_work(db_session_factory) as session:
            yield cli._solution_tension_writer_work(session)

    pending = cli.select_solution_tensions_from_config(config, candidates_work=_work).pending()

    assert pending.skipped == ()
    by_param = {c.tension.parameter.value: c for c in pending.candidates}
    assert set(by_param) == {"mass", "period"}
    mass = by_param["mass"]
    assert mass.tension.id == seeded.mass.id and mass.item.id == seeded.item.id
    assert 8.0 < mass.tension.sigma < 8.4 and not mass.extreme
    assert mass.data.archive_url.endswith("HD%20202206%20c")
    assert pending.candidates[0].tension.parameter.value == "mass"  # σ desc


def test_las_dos_siembras_del_humo_v4_comparten_un_unico_run(db_session_factory) -> None:
    """Misma composición que `tests/manual/test_edit_night_v4_smoke.py`, sin Claude."""
    from helpers.tension_smoke import seed_v1298_tension
    from sqlalchemy import select

    from nocturna.infrastructure.db.models import RunRow

    config = load_pipeline_config(REAL_PIPELINE_TOML)
    v1298 = seed_v1298_tension(db_session_factory, run_budget_tokens=20_000)
    archive = seed_hd202206c_solution_tension(db_session_factory, config, run_budget_tokens=None)

    assert archive.run_id is None
    with db_session_factory() as session:
        runs = list(session.execute(select(RunRow)).scalars())
    assert [r.id for r in runs] == [v1298.run_id] and runs[0].budget_tokens == 20_000
