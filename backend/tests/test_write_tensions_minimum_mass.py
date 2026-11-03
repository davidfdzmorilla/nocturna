"""T100: una tensión de catálogo cuya masa es un `m sin i` no llega al redactor."""

from __future__ import annotations

import pytest
from fakes.llm import FakeLLMProvider
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    make_item,
    make_policy,
    make_selector,
    make_writer,
)
from helpers.tension_writer import v1298_evaluation

from nocturna.domain.entities import ItemStatus

pytestmark = pytest.mark.anyio

EVIDENCE = "Msin(i) = 0.64 M_jup"


def _env(abstract: str, *, evidence: str):
    item = make_item(status=ItemStatus.READ, abstract=abstract)
    policy = make_policy(writer_reserve_tokens=20_000, max_writer_calls_per_night=5)
    env = Environment(items=[item], policy=policy, now=WITHIN_WINDOW)
    evaluation = v1298_evaluation(item.id, evidence=evidence)
    env.evaluations.add(evaluation)
    return env, evaluation


async def test_masa_m_sin_i_a_mas_de_3_sigma_no_llega_al_redactor() -> None:
    env, evaluation = _env(f"We find V1298 Tau b with {EVIDENCE} from RVs.", evidence=EVIDENCE)
    fake = FakeLLMProvider()

    pending = make_selector(env).pending()
    for candidate in pending.candidates:
        await make_writer(env, fake)(candidate)

    assert pending.candidates == ()
    assert pending.skipped == ((evaluation.id, "minimum_mass"),)
    assert fake.calls == [], "cero llamadas al redactor"


async def test_la_misma_tension_sin_masa_minima_sigue_siendo_candidata() -> None:
    env, _ = _env("We find V1298 Tau b with a mass of 0.64 M_jup.", evidence="0.64 M_jup")

    pending = make_selector(env).pending()

    assert len(pending.candidates) == 1 and pending.skipped == ()
