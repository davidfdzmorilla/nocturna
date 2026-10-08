"""Humo del redactor de tensiones (T76): V1298 Tau b, una llamada real a Sonnet.

**NO SE LANZA en la suite normal.** Excluido por `addopts = "-m 'not manual'"`
y por la fixture autouse de `tests/manual/conftest.py`, que salta todo este
directorio si `NOCTURNA_ALLOW_REAL_CLAUDE` no está en el entorno. Lo lanza el
autor a mano, y solo con su autorización explícita (T76, D9):

    env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s \
        tests/manual/test_write_tension_smoke.py

Solo lleva los marcadores `manual` y `anyio` (ver `test_manual_markers_guard.py`).

## Qué demuestra

Que el prompt real de `prompts/writer-v1.md` produce, contra el modelo de
verdad, un JSON con título y los tres niveles a partir de una tensión real
(masa de V1298 Tau b, ~3,4 sigma frente a Livingston et al. 2026), que la
llamada pasa por `BudgetGuard` (tope de este humo: 2 llamadas y 24.000
tokens) y queda registrada como `AgentCall` de rol `writer`. Imprime el
texto para que el autor lo lea.

## De dónde salen los datos

La evaluación se arma desde `tests/fixtures/exoplanet_archive/ps_v1298tau.csv`
y las medidas de 2609.30038 (`tests/fixtures/t71c/`), vía
`helpers.tension_writer.v1298_evaluation`. El CSV grabado no trae
`pl_pubdate`; la base real dice que la referencia por defecto (Livingston et
al. 2026) tiene `pl_pubdate = "2026-01"` (dato real de la base a 2026-10-08),
y `helpers.tension_smoke.REAL_REFERENCE_OVERRIDES` lo fija sin tocar el CSV.
`tests/db/test_tension_smoke_seed_db.py` comprueba, sin llamar a Claude, que
esa siembra deja una tensión elegible.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, time
from time import monotonic
from uuid import UUID

import claude_agent_sdk
import pytest
from claude_agent_sdk import ResultMessage
from fakes.clock import FakeClock
from helpers.tension_smoke import seed_v1298_tension
from sqlalchemy import select

from nocturna import cli
from nocturna.application.agents.prompt_loader import WRITER_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.application.use_cases.write_tensions import WriteOutcome
from nocturna.domain.entities import FindingType
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow, FindingRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
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

# Dentro de la ventana real de ejecución (00:00-04:45).
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)

# Topes de este humo (T76, D9): como máximo 2 llamadas y 24.000 tokens.
_SMOKE_TOKEN_CAP = 24_000
_SMOKE_MAX_WRITER_CALLS = 2


def _cli_version() -> str:
    """Versión instalada del binario `claude` (`--version` no es tráfico hacia
    Claude ni consume presupuesto); se vuelca junto a los tokens para atar la
    calibración a la versión usada."""
    try:
        completed = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(no se pudo determinar: {exc!r})"
    output = completed.stdout.strip() or completed.stderr.strip()
    return output or f"(salida vacía, returncode={completed.returncode})"


def _test_policy() -> BudgetPolicy:
    return BudgetPolicy(
        nightly_tokens=_SMOKE_TOKEN_CAP,
        editor_reserve_tokens=0,
        writer_reserve_tokens=_SMOKE_TOKEN_CAP,
        max_items_per_night=1,
        max_turns_per_agent=1,
        max_editor_calls_per_night=1,
        max_writer_calls_per_night=_SMOKE_MAX_WRITER_CALLS,
        max_calls_per_item=_SMOKE_MAX_WRITER_CALLS,
        item_timeout_s=120,
        editor_timeout_s=120,
        run_timeout_s=240,
        window_start=time(0, 0),
        window_hard_stop=time(4, 45),
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _work_factory(db_session_factory, run_id: UUID, policy: BudgetPolicy) -> AgentWorkFactory:
    """Mismo patrón que `cli.py::_agent_work_factory`: cada llamada abre una
    `unit_of_work` nueva contra PostgreSQL real, con un `BudgetGuard` sobre el
    reloj fijo de este módulo."""
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
async def test_smoke_write_tension_llamada_real_redacta_v1298_tau_b_y_contabiliza_el_gasto(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
) -> None:
    if "ANTHROPIC_API_KEY" in os.environ:
        pytest.fail(_NO_API_KEY_REMEDY)

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

    writer = cli.write_tensions_from_config(
        config,
        work=_work_factory(db_session_factory, run_id, policy),
        provider=AgentSDKProvider(),
        system_prompt=load_prompt(WRITER_PROMPT_VERSION),
    )
    pending = cli.select_tensions_from_config(
        config, candidates_work=cli._tension_writer_work_factory(db_session_factory)
    ).pending()
    assert pending.skipped == (), f"la tensión sembrada debería ser elegible: {pending.skipped}"
    (candidate,) = pending.candidates

    started_at = monotonic()
    result = await writer(candidate)
    duration_ms = int((monotonic() - started_at) * 1000)

    cli_version = _cli_version()
    print(f"\n--- versión del CLI 'claude' --- {cli_version}")
    print(f"--- versión de claude_agent_sdk --- {claude_agent_sdk.__version__}")
    print(
        f"\n--- resumen: outcome={result.outcome.value} intentos={result.attempts} "
        f"tokens_spent={result.tokens_spent} duration_ms={duration_ms} run_id={run_id} "
        f"estimado={config.budget.writer_estimated_tokens} modelo={config.models.writer} "
        f"cli={cli_version} sdk={claude_agent_sdk.__version__} ---"
    )
    for attempt_index, messages in enumerate(captured_calls, start=1):
        result_messages = [m for m in messages if isinstance(m, ResultMessage)]
        if result_messages:
            last = result_messages[-1]
            print(f"--- intento {attempt_index}: usage crudo --- {last.usage!r}")
            print(f"--- intento {attempt_index}: model_usage crudo --- {last.model_usage!r}")
            print(f"--- intento {attempt_index}: output_text crudo ---\n{last.result!r}")
        else:
            print(f"(intento {attempt_index}: no se recibió ningún ResultMessage)")

    assert len(captured_calls) == result.attempts

    # --- el gasto se contabiliza siempre, éxito o fallo -------------------
    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        db_tokens_used = agent_calls.tokens_used_for_run(run_id)
        db_writer_calls = agent_calls.count_for_run(run_id, AgentRole.WRITER)
    assert db_tokens_used == result.tokens_spent
    assert db_writer_calls == result.attempts
    assert 0 < db_tokens_used <= _SMOKE_TOKEN_CAP, "el gasto debe caber en el tope del humo"
    assert result.attempts <= _SMOKE_MAX_WRITER_CALLS

    if result.outcome is not WriteOutcome.WRITTEN:
        pytest.fail(
            f"la llamada real al redactor no produjo un texto válido: outcome="
            f"{result.outcome.value} tras {result.attempts} intento(s) (tokens_spent="
            f"{result.tokens_spent}, ya contabilizados). Revisa el output_text crudo de "
            "arriba: si es sistemático, prompts/writer-v1.md necesita ajuste."
        )

    finding = result.finding
    assert finding is not None
    for text in (
        finding.title,
        finding.level_curious,
        finding.level_amateur,
        finding.level_technical,
    ):
        assert text.strip()
    assert finding.type is FindingType.CATALOG_TENSION
    assert finding.tension_evaluation_id == seeded.evaluation.id
    assert finding.published_at is None

    print("\n--- texto redactado (V1298 Tau b, masa) ---")
    print(f"titulo: {finding.title}")
    print(f"curioso: {finding.level_curious}")
    print(f"aficionado: {finding.level_amateur}")
    print(f"tecnico: {finding.level_technical}")

    with db_session_factory() as check_session:
        row = check_session.get(FindingRow, finding.id)
        assert row is not None
        assert row.published_at is None and row.confidence is None
        rows = list(
            check_session.execute(
                select(AgentCallRow).where(AgentCallRow.run_id == run_id)
            ).scalars()
        )
    assert len(rows) == result.attempts
    for call in rows:
        assert call.agent is AgentRole.WRITER
        assert call.item_id == seeded.item.id
        assert call.prompt_version == WRITER_PROMPT_VERSION
        assert call.model == config.models.writer
        assert call.tokens_in > 0 and call.tokens_out > 0
