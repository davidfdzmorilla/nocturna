"""Cadena real del Editor (T43), equivalente de `test_popularize_chain.py` (T42).

`EditNight` real + `AgentRunner` real (`application/agents/runner.py`,
compartido por los tres agentes) + `BudgetGuard` real + repositorios
SQLAlchemy reales + `AgentSDKProvider` real. Lo único doblado es
`agent_sdk_provider.query` (`tests/helpers/sdk_doubles.py`), con un
`ResultMessage` que aprueba uno de los dos candidatos de la noche.

`tests/test_edit_night.py` ya prueba `EditNight` de punta a punta con
`FakeLLMProvider` y repositorios en memoria (T43). Lo que ese fichero NO
puede demostrar es que la publicación sobrevive al paso por PostgreSQL de
verdad -- en concreto:

- que `FindingRepository.save` persiste `confidence`/`published_at` de
  verdad (no solo que la entidad de dominio los lleva en memoria);
- que `FindingRepository.unpublished_for_run(run_id)` deja de devolver el
  `Finding` que el Editor acaba de aprobar, en una sesión NUEVA, abierta
  después del commit;
- que el `Item` aprobado queda `PUBLISHED` y el descartado `DISCARDED` en
  base de datos, no solo en el objeto de dominio en memoria.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time

import pytest
from factories import make_finding, make_item, make_run
from fakes.clock import FakeClock
from helpers.sdk_doubles import build_fake_query, make_result_message

from nocturna import cli
from nocturna.application.agents.prompt_loader import EDITOR_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetPolicy
from nocturna.application.use_cases.edit_night import EditNight, EditOutcome
from nocturna.domain.entities import ItemStatus
from nocturna.infrastructure.db.models import FindingRow, ItemRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_MODEL = "claude-opus-test"


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 300_000,
        "editor_reserve_tokens": 60_000,
        "max_items_per_night": 10,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 2,
        "item_timeout_s": 180,
        "editor_timeout_s": 300,
        "run_timeout_s": 16_200,
        "window_start": time(0, 0),
        "window_hard_stop": time(4, 45),
        "weekly_reset_weekday": 0,
        "weekly_reset_hour": 0,
        "reset_day_multiplier": 1.0,
    }
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


def _persist_run_and_two_candidates(db_session_factory, *, budget_tokens: int):
    """Un `Run`, dos `Item` ya `READ` y sus `Finding` sin publicar, persistidos de verdad."""
    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        items = SqlAlchemyItemRepository(session)
        findings = SqlAlchemyFindingRepository(session)

        run = make_run(budget_tokens=budget_tokens)
        runs.add(run)

        item_a = make_item(status=ItemStatus.READ, external_id="2601.00010")
        item_b = make_item(status=ItemStatus.READ, external_id="2601.00011")
        items.add_many([item_a, item_b])

        finding_a = make_finding(item_a.id, run.id, title="Candidato aprobado")
        finding_b = make_finding(item_b.id, run.id, title="Candidato descartado")
        findings.add(finding_a)
        findings.add(finding_b)

        session.flush()
        return run.id, item_a.id, item_b.id, finding_a.id, finding_b.id


async def test_orquestador_real_publica_el_aprobado_y_descarta_el_resto(
    db_session_factory, monkeypatch
) -> None:
    policy = _policy()
    run_id, item_a_id, item_b_id, finding_a_id, finding_b_id = _persist_run_and_two_candidates(
        db_session_factory, budget_tokens=policy.nightly_tokens
    )

    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        _build_editor_fake_query(publish_item_id=item_a_id),
        raising=True,
    )

    work = cli._agent_work_factory(db_session_factory, run_id, policy, FakeClock(_WITHIN_WINDOW))
    edit_night = EditNight(
        work=work,
        provider=AgentSDKProvider(),
        clock=FakeClock(_WITHIN_WINDOW),
        system_prompt=load_prompt("editor"),
        prompt_version=EDITOR_PROMPT_VERSION,
        model=_MODEL,
        max_turns=3,
        max_attempts=policy.max_editor_calls_per_night,
        base_tokens=1_000,
        tokens_per_candidate=200,
    )

    result = await edit_night(run_id=run_id)

    assert result.outcome is EditOutcome.EDITED
    assert [f.item_id for f in result.published] == [item_a_id]
    assert [f.item_id for f in result.discarded] == [item_b_id]

    # --- FindingRepository.save persiste confidence/published_at de verdad -
    with db_session_factory() as check_session:
        published_row = check_session.get(FindingRow, finding_a_id)
        assert published_row is not None
        assert published_row.confidence == 0.85
        assert published_row.published_at is not None

        discarded_row = check_session.get(FindingRow, finding_b_id)
        assert discarded_row is not None
        assert discarded_row.confidence is None
        assert discarded_row.published_at is None

        # --- unpublished_for_run deja de devolver el publicado -------------
        findings = SqlAlchemyFindingRepository(check_session)
        unpublished_ids = {f.item_id for f in findings.unpublished_for_run(run_id)}
        assert unpublished_ids == {item_b_id}, (
            "el Finding recién publicado ya no debe aparecer entre los candidatos "
            "pendientes de la noche -- si apareciera, una segunda llamada al Editor "
            "podría reconsiderarlo"
        )

        # --- el Item aprobado y el descartado quedan con el estado correcto
        item_a_row = check_session.get(ItemRow, item_a_id)
        assert item_a_row is not None
        assert item_a_row.status is ItemStatus.PUBLISHED

        item_b_row = check_session.get(ItemRow, item_b_id)
        assert item_b_row is not None
        assert item_b_row.status is ItemStatus.DISCARDED


def _build_editor_fake_query(*, publish_item_id):
    """Doble de `query()` que emite un `ResultMessage` con la decisión del
    Editor real: aprueba `publish_item_id`, descarta el resto -- mismo
    patrón que `tests/db/test_popularize_chain.py`."""

    payload = {
        "publish": [
            {
                "item_id": str(publish_item_id),
                "confidence": 0.85,
                "reason": "Hallazgo con potencial de interés general.",
            }
        ]
    }
    frame = make_result_message(
        result=json.dumps(payload),
        usage={
            "input_tokens": 1200,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 200,
        },
        model_usage=None,
    )
    return build_fake_query([frame], sleep_before_s=0.005)
