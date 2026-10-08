"""T76: sección del `--dry-run` con las tensiones pendientes del redactor."""

from __future__ import annotations

from pathlib import Path

import pytest
from fakes.llm import FakeLLMProvider
from helpers.run_night import WITHIN_WINDOW, Environment, make_item, make_policy, make_selector
from helpers.tension_writer import v1298_evaluation

from nocturna import cli
from nocturna.domain.entities import ItemStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config

REAL_PIPELINE_TOML = Path(__file__).resolve().parents[2] / "config" / "pipeline.toml"


def test_la_seccion_lista_elegibles_y_excluidas_con_motivo() -> None:
    ok_item = make_item(status=ItemStatus.READ)
    own_item = make_item(status=ItemStatus.READ)
    env = Environment(items=[ok_item, own_item], policy=make_policy(), now=WITHIN_WINDOW)
    env.evaluations.add(v1298_evaluation(ok_item.id))
    own = v1298_evaluation(own_item.id, reference_overrides={"arxiv_id": own_item.external_id})
    env.evaluations.add(own)
    pending = make_selector(env).pending()

    text = cli._format_writer_section(
        pending,
        labels={own.id: f"{own_item.external_id} · V1298 Tau b (mass)"},
        estimated_tokens=12000,
    )

    assert "Tensiones pendientes del redactor" in text
    assert "elegibles=1 excluidas=1" in text
    assert f"elegible · {ok_item.external_id} · V1298 Tau b (mass)" in text
    assert (
        f"excluida · {own_item.external_id} · V1298 Tau b (mass) · reference_not_independent"
        in text
    )


def test_la_seccion_sin_nada_pendiente() -> None:
    env = Environment(items=[], policy=make_policy(), now=WITHIN_WINDOW)
    pending = make_selector(env).pending()

    text = cli._format_writer_section(pending, labels={}, estimated_tokens=1)

    assert "elegibles=0 excluidas=0" in text


@pytest.mark.anyio
async def test_write_tensions_from_config_autoriza_con_la_estimacion_y_el_modelo_del_redactor() -> (
    None
):
    config = load_pipeline_config(REAL_PIPELINE_TOML)
    budget = config.budget.model_copy(
        update={"writer_estimated_tokens": 4321, "popularizer_estimated_tokens": 1234}
    )
    config = config.model_copy(update={"budget": budget})
    item = make_item(status=ItemStatus.READ)
    env = Environment(
        items=[item],
        policy=make_policy(writer_reserve_tokens=20_000, max_writer_calls_per_night=5),
        now=WITHIN_WINDOW,
    )
    env.evaluations.add(v1298_evaluation(item.id))
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.WRITER,
        json={"title": "t", "level_curious": "c", "level_amateur": "a", "level_technical": "x"},
        tokens_in=10,
        tokens_out=10,
    )
    authorized: list[tuple[AgentRole, int]] = []
    real_authorize = env.guard.authorize

    def _spy(role: AgentRole, estimated_tokens: int) -> None:
        authorized.append((role, estimated_tokens))
        real_authorize(role, estimated_tokens)

    env.guard.authorize = _spy  # type: ignore[method-assign]
    writer = cli.write_tensions_from_config(
        config,
        work=env.work,
        provider=fake,
        system_prompt="p",
    )
    (candidate,) = make_selector(env).pending().candidates

    await writer(candidate)

    assert authorized == [(AgentRole.WRITER, 4321)]
    assert fake.calls[0].model == config.models.writer
