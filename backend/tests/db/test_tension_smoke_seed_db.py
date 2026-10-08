"""La siembra de los humos del redactor (T76) deja una tensión elegible. Sin Claude."""

from __future__ import annotations

from contextlib import contextmanager

from helpers.exoplanet import make_own_solution_rule
from helpers.tension_smoke import PAPER_ARXIV_ID, seed_v1298_tension

from nocturna import cli
from nocturna.application.use_cases.write_tensions import SelectTensions
from nocturna.infrastructure.db.session import unit_of_work


def test_la_siembra_del_humo_deja_una_tension_elegible(db_session_factory) -> None:
    seeded = seed_v1298_tension(db_session_factory, run_budget_tokens=24_000)

    @contextmanager
    def _candidates_work():
        with unit_of_work(db_session_factory) as session:
            yield cli._tension_writer_work(session)

    selector = SelectTensions(
        candidates_work=_candidates_work,
        own_solution_rule=make_own_solution_rule(),
        threshold_sigma=3.0,
        planet_overview_url=lambda name: f"https://archive.example/overview/{name}",
        max_attempts=2,
    )

    pending = selector.pending()

    assert pending.skipped == ()
    (candidate,) = pending.candidates
    assert candidate.item.external_id == PAPER_ARXIV_ID
    assert candidate.evaluation.id == seeded.evaluation.id
    assert candidate.tension.planet_name == "V1298 Tau b"
    assert candidate.tension.reference_sigma >= 3.0
