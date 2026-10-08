"""Humo de `editor-v3` (T76): un `paper_explained` y un `catalog_tension` en UNA llamada a Opus.

**NO SE LANZA en la suite normal.** Excluido por `addopts = "-m 'not manual'"`
y por la fixture autouse de `tests/manual/conftest.py`. Lo lanza el autor a
mano, y solo con su autorización explícita (T76, D9):

    env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s \
        tests/manual/test_edit_night_v3_smoke.py

Solo lleva los marcadores `manual` y `anyio` (ver `test_manual_markers_guard.py`).

## Qué demuestra

Que el prompt real de `prompts/editor-v3.md`, con el bloque `<candidates>`
mixto (un `paper_explained` y un `catalog_tension` de V1298 Tau b con su línea
`data` acotada), produce contra Opus un JSON `publish` válido, en UNA sola
llamada, con el tope de este humo (10.000 tokens). Imprime la decisión y los
tokens para que el autor valore si `editor-v3` trata el `catalog_tension` como
"candidato: discrepancia calculada, no verificada" y no le añade certeza.

El `Finding` `catalog_tension` lleva texto fijo escrito a mano (no pasa por el
redactor: este humo solo mide al Editor) y el `CatalogTension` real calculado
con `catalog_tension_from` sobre la evaluación sembrada por
`helpers.tension_smoke` (mismos datos que `test_write_tension_smoke.py`).
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, time
from time import monotonic
from uuid import UUID, uuid4

import pytest
from claude_agent_sdk import ResultMessage
from fakes.clock import FakeClock
from helpers.tension_smoke import PAPER_PUBLISHED_AT, seed_v1298_tension
from sqlalchemy import select

from nocturna.application.agents.prompt_loader import EDITOR_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.application.use_cases.edit_night import EditNight, EditOutcome
from nocturna.domain.entities import Finding, FindingType, Item, ItemStatus
from nocturna.domain.llm import AgentRole
from nocturna.domain.tension import catalog_tension_from
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.exoplanet_archive.mappers import planet_overview_url
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

pytestmark = [pytest.mark.manual]

_NO_API_KEY_REMEDY = (
    "La clave de API de Anthropic está definida en el entorno de este proceso. Este test hace "
    "una llamada real y debe cobrarse contra la suscripción Claude Max (CLI 'claude' logueado), "
    "nunca contra una API key (CLAUDE.md, 'Restricción que gobierna todo el diseño'). Quítala "
    "del entorno y repite con: "
    "env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s"
)

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)

# Tope de este humo (T76, D9): una llamada del Editor y 10.000 tokens.
_SMOKE_TOKEN_CAP = 10_000
_SMOKE_EDITOR_RESERVE = 1_000


def _test_policy() -> BudgetPolicy:
    return BudgetPolicy(
        nightly_tokens=_SMOKE_TOKEN_CAP,
        editor_reserve_tokens=_SMOKE_EDITOR_RESERVE,
        writer_reserve_tokens=0,
        max_items_per_night=2,
        max_turns_per_agent=1,
        max_editor_calls_per_night=1,
        max_writer_calls_per_night=0,
        max_calls_per_item=2,
        item_timeout_s=60,
        editor_timeout_s=120,
        run_timeout_s=180,
        window_start=time(0, 0),
        window_hard_stop=time(4, 45),
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _work_factory(db_session_factory, run_id: UUID, policy: BudgetPolicy) -> AgentWorkFactory:
    clock = FakeClock(_WITHIN_WINDOW)

    @contextmanager
    def _open() -> Generator[AgentWork, None, None]:
        with unit_of_work(db_session_factory) as session:
            runs = SqlAlchemyRunRepository(session)
            agent_calls = SqlAlchemyAgentCallRepository(session)
            guard = BudgetGuard(
                run_id=run_id, policy=policy, runs=runs, agent_calls=agent_calls, clock=clock
            )
            yield AgentWork(
                guard=guard,
                runs=runs,
                items=SqlAlchemyItemRepository(session),
                readings=SqlAlchemyReadingRepository(session),
                findings=SqlAlchemyFindingRepository(session),
                agent_calls=agent_calls,
            )

    return _open


@pytest.mark.anyio
async def test_smoke_editor_v3_decide_un_paper_y_una_tension_en_una_sola_llamada(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
) -> None:
    if "ANTHROPIC_API_KEY" in os.environ:
        pytest.fail(_NO_API_KEY_REMEDY)
    assert EDITOR_PROMPT_VERSION == "editor-v3"

    captured_calls: list[list[object]] = []
    real_query = agent_sdk_provider.query

    async def _spying_query(*args: object, **kwargs: object):
        messages: list[object] = []
        captured_calls.append(messages)
        inner = real_query(*args, **kwargs)
        try:
            async for message in inner:
                messages.append(message)
                yield message
        finally:
            await inner.aclose()

    monkeypatch.setattr(agent_sdk_provider, "query", _spying_query, raising=True)

    config = load_pipeline_config()
    policy = _test_policy()
    seeded = seed_v1298_tension(db_session_factory, run_budget_tokens=_SMOKE_TOKEN_CAP)
    run_id = seeded.run_id
    assert seeded.evaluation.result is not None
    tension = catalog_tension_from(
        seeded.evaluation.result,
        threshold_sigma=config.tension.threshold_sigma,
        archive_url=planet_overview_url("V1298 Tau b"),
    )

    with unit_of_work(db_session_factory) as session:
        paper_item = Item(
            source="arxiv",
            external_id=f"9999.{uuid4().hex[:5]}",
            title="Una binaria ultra-compacta estrella de neutrones-enana blanca",
            abstract="Abstract original, no usado directamente por el Editor.",
            categories=["astro-ph.HE"],
            published_at=PAPER_PUBLISHED_AT,
            fetched_at=PAPER_PUBLISHED_AT,
            status=ItemStatus.READ,
        )
        SqlAlchemyItemRepository(session).add_many([paper_item])
        session.flush()
        findings = SqlAlchemyFindingRepository(session)
        findings.add(
            Finding(
                item_id=paper_item.id,
                run_id=run_id,
                type=FindingType.PAPER_EXPLAINED,
                title="Una pareja de estrellas muertas que giran cada 83 minutos",
                level_curious=(
                    "Un telescopio ha encontrado dos estrellas muertas que se orbitan cada "
                    "83 minutos, una de las órbitas más rápidas conocidas de este tipo."
                ),
                level_amateur="Binaria compacta de estrella de neutrones y enana blanca.",
                level_technical="Periodo orbital de 83 min en una binaria ultra-compacta.",
            )
        )
        findings.add(
            Finding(
                item_id=seeded.item.id,
                run_id=run_id,
                type=FindingType.CATALOG_TENSION,
                title="V1298 Tau b: su masa difiere de la publicada en el archivo",
                level_curious=(
                    "Una nueva medida de la masa de este planeta no coincide con la que "
                    "figura en el archivo, por unas 3,4 veces su incertidumbre."
                ),
                level_amateur="La masa medida difiere unos 3,4 sigma de la referencia del archivo.",
                level_technical="Masa de V1298 Tau b: 3,37 sigma frente a Livingston et al. 2026.",
                catalog_tension=tension,
                tension_evaluation_id=seeded.evaluation.id,
            )
        )

    work = _work_factory(db_session_factory, run_id, policy)
    edit_night = EditNight(
        work=work,
        provider=AgentSDKProvider(),
        clock=FakeClock(_WITHIN_WINDOW),
        system_prompt=load_prompt(EDITOR_PROMPT_VERSION),
        prompt_version=EDITOR_PROMPT_VERSION,
        model=config.models.editor,
        max_turns=policy.max_turns_per_agent,
        max_attempts=policy.max_editor_calls_per_night,
        base_tokens=config.budget.editor_base_tokens,
        tokens_per_candidate=config.budget.editor_tokens_per_candidate,
    )

    started_at = monotonic()
    result = await edit_night(run_id=run_id)
    duration_ms = int((monotonic() - started_at) * 1000)

    print(
        f"\n--- resumen: outcome={result.outcome.value} candidatos={result.candidates} "
        f"intentos={result.attempts} tokens_spent={result.tokens_spent} "
        f"duration_ms={duration_ms} run_id={run_id} modelo={config.models.editor} ---"
    )
    for attempt_index, messages in enumerate(captured_calls, start=1):
        result_messages = [m for m in messages if isinstance(m, ResultMessage)]
        if result_messages:
            last = result_messages[-1]
            print(f"--- intento {attempt_index}: usage crudo --- {last.usage!r}")
            print(f"--- intento {attempt_index}: output_text crudo ---\n{last.result!r}")
        else:
            print(f"(intento {attempt_index}: no se recibió ningún ResultMessage)")

    assert len(captured_calls) == result.attempts == 1, "el Editor se llama una sola vez"
    assert result.candidates == 2, "el Editor recibe los dos tipos de candidato en la misma llamada"

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        db_tokens_used = agent_calls.tokens_used_for_run(run_id)
        db_editor_calls = agent_calls.count_for_run(run_id, AgentRole.EDITOR)
    assert db_tokens_used == result.tokens_spent
    assert db_editor_calls == 1
    assert 0 < db_tokens_used <= _SMOKE_TOKEN_CAP, "el gasto debe caber en el tope del humo"

    if result.outcome is not EditOutcome.EDITED:
        pytest.fail(
            f"la llamada real a editor-v3 no produjo una decisión válida: outcome="
            f"{result.outcome.value} (tokens_spent={result.tokens_spent}, ya contabilizados). "
            "Revisa el output_text crudo de arriba."
        )

    print(f"\n--- decisión: {len(result.published)} publicados de {result.candidates} ---")
    for finding in result.published:
        print(
            f"  publicado [{finding.type.value}]: {finding.title!r} "
            f"confidence={finding.confidence} motivo={result.reasons.get(finding.id, '')!r}"
        )
    for finding in result.discarded:
        print(f"  descartado [{finding.type.value}]: {finding.title!r}")

    with db_session_factory() as check_session:
        rows = list(
            check_session.execute(
                select(AgentCallRow).where(AgentCallRow.run_id == run_id)
            ).scalars()
        )
    assert len(rows) == 1
    (call,) = rows
    assert call.agent is AgentRole.EDITOR
    assert call.prompt_version == EDITOR_PROMPT_VERSION
    assert call.model == config.models.editor
    assert call.tokens_in > 0 and call.tokens_out > 0
