"""Humo del redactor del resumen semanal (T84.r): una semana tipo W42, una llamada real a Sonnet.

**NO SE LANZA en la suite normal.** Excluido por `addopts = "-m 'not manual'"` y por la
fixture autouse de `tests/manual/conftest.py`, que salta todo este directorio si
`NOCTURNA_ALLOW_REAL_CLAUDE` no esta en el entorno. Lo lanza el orquestador una sola vez, con
la autorizacion expresa del autor (D15), contra `nocturna_test`:

    NOCTURNA_ALLOW_REAL_CLAUDE=1 \
        NOCTURNA_DATABASE_URL=postgresql+psycopg://nocturna:nocturna@localhost:5433/nocturna_test \
        uv run pytest -m manual -s tests/db/test_archive_repository.py \
        tests/manual/test_write_digest_smoke.py

(`tests/db/test_archive_repository.py` va delante solo para que pytest ponga `tests/db` en la
ruta de importación: sus tests no son `manual` y quedan fuera).

(con el entorno sin ninguna clave de API: el humo falla si la hay). Solo lleva los marcadores
`manual` y `anyio` (ver `test_manual_markers_guard.py`).

## Que demuestra

Que el prompt real de `prompts/writer-digest-v1.md` produce, contra el modelo de verdad, un
JSON `{title, summary}` valido a partir del bloque `<weekly_digest>` de una semana con cambios
de referencia y planetas nuevos (forma de la W42 real: 2 cambios y 3 altas); que la llamada pasa
por `BudgetGuard` (Run de prueba de 26.000 tokens, 2 llamadas como maximo), queda como
`AgentCall` de rol `writer` SIN item y con `prompt_version = writer-digest-v1`, y que el texto
no usa "pipeline", ni `\\n` literal, ni nombres de campo, ni hashes, ni URLs. Imprime los tokens
frente a la estimacion `writer_estimated_tokens` (12.000) y el texto para que el autor lo lea.
"""

from __future__ import annotations

import dataclasses
import os
import re
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, time
from time import monotonic
from uuid import UUID
from zoneinfo import ZoneInfo

import claude_agent_sdk
import pytest
from claude_agent_sdk import ResultMessage
from fakes.clock import FakeClock
from sqlalchemy import select
from test_archive_repository import save, snapshot, solution

from nocturna import cli
from nocturna.application.agents.prompt_loader import WRITER_DIGEST_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.application.use_cases.write_archive_digest import DigestWriterWork
from nocturna.application.use_cases.write_tensions import WriteOutcome
from nocturna.domain.archive import ArchiveParameterValue, SnapshotKind
from nocturna.domain.archive_digest import DigestTextStatus
from nocturna.domain.entities import Run
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyArchiveDigestReader,
    SqlAlchemyArchiveDigestTextRepository,
    SqlAlchemyArchiveRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

pytestmark = [pytest.mark.manual]

# Nombre de la variable de clave de API, armado por partes: nada de este fichero la escribe.
_API_KEY_ENV = "ANTHROPIC" + "_API_KEY"
_NO_API_KEY_REMEDY = (
    "Hay una clave de API de Anthropic definida en el entorno de este proceso. Este test hace "
    "una llamada real y debe cobrarse contra la suscripción Claude Max (CLI 'claude' logueado), "
    "nunca contra una API key (CLAUDE.md, 'Restricción que gobierna todo el diseño'). Quítala "
    "del entorno y repite con NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s."
)

# Nombres de campo del bloque y de la entrada que nunca deben aparecer en el texto.
_FIELD_NAMES = (
    r"\b(changed|new_planet|regained|lost|old_solution|new_solution|weekly_digest|snapshots"
    r"|parameter_changes|planet_url|solution_key|ref_text|kind|detail|previous_reference"
    r"|new_reference|planets|names|reference|no_change_in_mass_radius_period)\b"
)
_HEX64 = r"\b[0-9a-f]{64}\b"
_URL = r"https?://|www\."

# Dentro de la ventana real de ejecución (00:00-04:45) y con la W42 ya cerrada.
_TZ = ZoneInfo("Europe/Madrid")
_NOW = datetime(2026, 10, 20, 2, 0, 0, tzinfo=_TZ)
_WEEK = "2026-W42"

# Topes de este humo: Run de prueba de 26.000 tokens y como máximo 2 llamadas.
_SMOKE_TOKEN_CAP = 26_000
_SMOKE_MAX_WRITER_CALLS = 2


def _cli_version() -> str:
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


def _seed_week_42(factory) -> UUID:
    """Semana W42 sintética con la forma de la real: 2 cambios de referencia y 3 altas.

    Los snapshots caen el lunes 12 (soluciones iniciales) y el miércoles 14 de octubre de 2026
    (cambios y altas); un `Run` de prueba da la FK de la fila de texto.
    """
    with unit_of_work(factory) as session:
        repo = SqlAlchemyArchiveRepository(session)
        first = [
            solution("Kepler-9999 b", "Smith et al. 2020", is_default=True, mass=11.2),
            solution("Kepler-9999 c", "Smith et al. 2020", is_default=True, mass=4.8),
        ]
        snap = dataclasses.replace(snapshot(rows=2), taken_at=datetime(2026, 10, 12, 1, tzinfo=UTC))
        save(repo, snap, first)
        second = [
            solution(
                "Kepler-9999 b",
                "Brown et al. 2026",
                is_default=True,
                mass=17.9,
                radius=ArchiveParameterValue(value=3.1, err1=0.2, err2=-0.2, lim=0),
            ),
            solution("Kepler-9999 c", "Brown et al. 2026", is_default=True, mass=6.0),
            solution("TOI-0001 b", "Brown et al. 2026", is_default=True, mass=2.2),
            solution("TOI-0002 b", "Brown et al. 2026", is_default=True, mass=9.0),
            solution("TOI-0003 b", "Lee et al. 2026", is_default=True, mass=1.1),
        ]
        snap2 = dataclasses.replace(
            snapshot(kind=SnapshotKind.INCREMENTAL), taken_at=datetime(2026, 10, 14, 1, tzinfo=UTC)
        )
        save(repo, snap2, second, removal_scope=frozenset(s.pl_name for s in first + second))
    with unit_of_work(factory) as session:
        run = Run(started_at=_NOW, budget_tokens=_SMOKE_TOKEN_CAP)
        SqlAlchemyRunRepository(session).add(run)
        return run.id


def _work_factory(db_session_factory, run_id: UUID, policy: BudgetPolicy) -> AgentWorkFactory:
    """Como `cli.py::_agent_work_factory`: una `unit_of_work` nueva por llamada, con un
    `BudgetGuard` sobre el reloj fijo de este módulo y los textos del resumen en el `AgentWork`."""
    clock = FakeClock(_NOW)

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
                digest_texts=SqlAlchemyArchiveDigestTextRepository(session),
            )

    return _open


def _digest_work_factory(db_session_factory):
    @contextmanager
    def _open() -> Generator[DigestWriterWork, None, None]:
        with unit_of_work(db_session_factory, commit=False) as session:
            yield DigestWriterWork(
                reader=SqlAlchemyArchiveDigestReader(session),
                digest_texts=SqlAlchemyArchiveDigestTextRepository(session),
            )

    return _open


@pytest.mark.anyio
async def test_smoke_write_digest_llamada_real_redacta_la_semana_y_contabiliza_el_gasto(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
) -> None:
    if _API_KEY_ENV in os.environ:
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
    run_id = _seed_week_42(db_session_factory)

    pending = cli.select_digest_weeks_from_config(
        config, work=_digest_work_factory(db_session_factory), clock=FakeClock(_NOW)
    ).pending()
    (candidate,) = [c for c in pending.candidates if c.digest.week == _WEEK]
    print(f"\n--- bloque ({len(candidate.block.encode())} bytes) ---\n{candidate.block}")

    writer = cli.write_digest_text_from_config(
        config,
        work=_work_factory(db_session_factory, run_id, policy),
        provider=AgentSDKProvider(),
        system_prompt=load_prompt(WRITER_DIGEST_PROMPT_VERSION),
        clock=FakeClock(_NOW),
    )

    started_at = monotonic()
    result = await writer(candidate)
    duration_ms = int((monotonic() - started_at) * 1000)

    cli_version = _cli_version()
    estimated = config.budget.writer_estimated_tokens
    print(f"\n--- versión del CLI 'claude' --- {cli_version}")
    print(f"--- versión de claude_agent_sdk --- {claude_agent_sdk.__version__}")
    print(
        f"\n--- resumen: outcome={result.outcome.value} intentos={result.attempts} "
        f"tokens_spent={result.tokens_spent} (estimado={estimated}, "
        f"{result.tokens_spent / estimated:.0%}) duration_ms={duration_ms} run_id={run_id} "
        f"modelo={config.models.writer} cli={cli_version} sdk={claude_agent_sdk.__version__} ---"
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

    # --- el gasto se contabiliza siempre, éxito o fallo -------------------------------
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
            "arriba: si es sistemático, prompts/writer-digest-v1.md necesita ajuste."
        )

    text = result.text
    assert text is not None and text.status is DigestTextStatus.WRITTEN
    assert text.title and text.summary and len(text.title) <= 90
    assert len(text.summary.split()) <= 220 * 2, "el resumen se pasa mucho de 220 palabras"
    print("\n--- texto redactado (semana tipo W42) ---")
    print(f"titulo: {text.title}")
    print(f"resumen:\n{text.summary}")

    with db_session_factory() as check_session:
        stored = SqlAlchemyArchiveDigestTextRepository(check_session).get(_WEEK)
        rows = list(
            check_session.execute(
                select(AgentCallRow).where(AgentCallRow.run_id == run_id)
            ).scalars()
        )
    assert stored == text and stored.input_sha256 == candidate.input_sha256
    assert len(rows) == result.attempts
    for call in rows:
        assert call.agent is AgentRole.WRITER
        assert call.item_id is None
        assert call.prompt_version == WRITER_DIGEST_PROMPT_VERSION
        assert call.model == config.models.writer
        assert call.tokens_in > 0 and call.tokens_out > 0

    # Estilo de writer-digest-v1 (reglas de T96): fallan el humo, no solo se imprimen.
    joined = f"{text.title} {text.summary}"
    assert not re.search(r"pipeline", joined, re.IGNORECASE), "usa 'pipeline'"
    assert "\\n" not in joined, "lleva un \\n literal"
    assert not re.search(_FIELD_NAMES, joined), "copia nombres de campo"
    assert not re.search(_HEX64, joined), "lleva un hash de 64 hex"
    assert not re.search(_URL, joined, re.IGNORECASE), "lleva una URL"
    assert not re.search(r"\bbloque\b", joined, re.IGNORECASE), "menciona el bloque de datos"
    # Un punto seguido de 3 cifras es separador de miles, no decimal.
    assert not re.search(r"\d\.(?!\d{3}\b)\d", joined), "usa punto decimal"
