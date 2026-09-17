"""Cuarto test de todo el proyecto que llama a Claude de verdad (T43, equivalente
del "Nota crítica" de T41/T42 para el Editor).

**NO SE LANZA en la suite normal.** Excluido por `addopts = "-m 'not manual'"`
(`pyproject.toml`) y por la fixture autouse `_require_real_claude_opt_in` de
`tests/manual/conftest.py`, que salta cualquier test de este directorio si
`NOCTURNA_ALLOW_REAL_CLAUDE` no está en el entorno -- sin importar desde
dónde se lance pytest ni qué `-m` se pida. Lo lanza el autor a mano, con:

    env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s

**Solo lleva los marcadores `manual` y `anyio`, deliberadamente sin `db`**:
misma regresión que `test_manual_markers_guard.py` congela para
`test_sdk_smoke.py` (T40) y que `test_read_item_smoke.py`/
`test_popularize_smoke.py` (T41/T42) ya heredan -- un segundo marcador
filtrable en `tests/manual/` reabriría un camino de selección
(`pytest -m db`) que no pasa por `-m 'not manual'`. Este fichero sigue
necesitando PostgreSQL (usa `db_session_factory` vía el reexport de
`tests/manual/conftest.py`), pero eso no lo hace aparecer bajo `-m db`.

## Por qué existe

`tests/test_edit_night.py` demuestra que `EditNight` es correcto contra
`FakeLLMProvider`. `tests/db/test_edit_night_db.py` demuestra que la
publicación persiste de verdad, con un `ResultMessage` construido a mano.
Ninguno de los dos demuestra que el prompt real de `prompts/editor.md`
produce, contra el modelo de verdad, un JSON válido con `publish` en la
forma que el prompt pide -- ni cuánto cuesta de verdad la única llamada
del Editor cada noche.

**Es el primer uso de Opus de todo el proyecto.** Reader y Popularizer
(T41/T42) corren en Sonnet; el Editor es el único agente en `opus`
(`config/pipeline.toml`, `[models]`). `editor_base_tokens` /
`editor_tokens_per_candidate` (`config/pipeline.toml`, `[budget]`) están
escritos a mano, sin un solo dato real detrás -- este humo es el primero
que produce alguno. Se ofrecen **2-3 candidatos sintéticos** (no un `Run`
de una noche completa, fuera del alcance de T43/T44) para que el volcado
de tokens por candidato tenga algo de variación real que comparar contra
`editor_tokens_per_candidate = 700`.

Hereda el patrón de gasto de `test_read_item_smoke.py`/`test_popularize_smoke.py`
al pie de la letra: `authorize` en su propia unidad de trabajo, la llamada
al LLM fuera de toda transacción, `record_call` en una unidad de trabajo
nueva y separada -- los tres pasos viven dentro de `AgentRunner.run` (T42,
compartido por los tres agentes desde T43), este test solo verifica que
ocurrieron construyendo el mismo `AgentWorkFactory` contra PostgreSQL real
que usaría `cli.py` (`_work_factory`, más abajo, mismo patrón que
`cli.py::_agent_work_factory`).

A diferencia de `test_read_item_smoke.py`/`test_popularize_smoke.py`, este
test no arranca desde un único `Item`: construye 2-3 `Item` ya `READ`, con
sendos `Finding` sin publicar (como los dejaría un Popularizer real), y dos
de ellos con méritos claramente distintos (uno con un hallazgo llamativo,
otro con un `level_curious` deliberadamente flojo) para que la decisión del
Editor real tenga algo de verdad que decidir, no una aprobación automática.

**Registro de versión de CLI y SDK junto a los tokens (deuda de T42, saldada
aquí y en el humo).** `_cli_version()` (idéntica a la de `test_sdk_smoke.py`,
no reimportada porque ese fichero no expone un símbolo público reutilizable
sin acoplar dos ficheros de test entre sí) y `claude_agent_sdk.__version__`
se vuelcan junto al resumen de tokens, para que T60 sepa contra qué versión
se midió cada noche -- exactamente el hueco que T42 dejó anotado en
`docs/TECHNICAL_DEBT.md` ("gasto lateral del CLI que no ata calibración a
versión usada").

Vuelca con `print()` (visible con `-s`) el JSON de `publish` que devuelve el
Editor real, el desglose de tokens **por intento** (máximo entre `usage` y
la suma de `model_usage`, igual que T40/T41/T42), y el `usage`/`model_usage`
crudos de cada `ResultMessage` -- es lo único que dice si el prompt real de
`prompts/editor.md` produce la forma pedida contra Opus de verdad, y cuánto
cuesta.

**Ningún umbral de coste fijado por adelantado.** A diferencia de
`test_read_item_smoke.py`/`test_popularize_smoke.py` (que sí imponen un
`assert result.tokens_spent < N` a posteriori, con datos reales previos de
qué costar), este es el PRIMER dato real de Opus para el Editor: no hay
cifra previa con la que fijar un umbral con criterio, y una `assert` en
falso aquí sería puro teatro. El volcado es la salida útil de este test;
el autor decide a mano si `editor_base_tokens`/`editor_tokens_per_candidate`
necesitan ajuste, igual que T42 hizo con `popularizer_estimated_tokens`
tras su propio humo -- registrar el dato para T60, no fijar un tope todavía.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, time
from time import monotonic
from uuid import UUID, uuid4

import claude_agent_sdk
import pytest
from claude_agent_sdk import ResultMessage
from fakes.clock import FakeClock
from sqlalchemy import select

from nocturna.application.agents.prompt_loader import EDITOR_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.application.use_cases.edit_night import EditNight, EditOutcome
from nocturna.domain.entities import Finding, FindingType, Item, ItemStatus, Run
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow, FindingRow, ItemRow
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
    "ANTHROPIC_API_KEY está definida en el entorno de este proceso. Este test hace una "
    "llamada real y debe cobrarse contra la suscripción Claude Max (CLI 'claude' logueado), "
    "nunca contra una API key (CLAUDE.md, 'Restricción que gobierna todo el diseño'). Quítala "
    "del entorno y repite con: "
    "env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s"
)

# Dentro de la ventana real de ejecución (00:00-04:45, ver config/pipeline.toml
# y CLAUDE.md), igual que en test_read_item_smoke.py/test_popularize_smoke.py.
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_WINDOW_START = time(0, 0)
_WINDOW_HARD_STOP = time(4, 45)

# Presupuesto de conveniencia para que `BudgetGuard.authorize` autorice de
# sobra la llamada; el tope real de esta llamada lo acota el prompt y
# `max_editor_calls_per_night`, no esta cifra (mismo comentario que
# `_TEST_RUN_BUDGET_TOKENS` en `test_read_item_smoke.py`/`test_popularize_smoke.py`).
_TEST_RUN_BUDGET_TOKENS = 50_000
_TEST_EDITOR_RESERVE_TOKENS = 1_000

# Tres candidatos sintéticos: dos con hallazgos claramente publicables, uno
# deliberadamente flojo (para que la decisión del Editor real tenga algo
# que descartar, no una aprobación automática de todo lo que se le ofrece).
_CANDIDATES = [
    {
        "title": "Descubrimiento de una binaria ultra-compacta estrella de neutrones-enana blanca",
        "level_curious": (
            "Un telescopio ha encontrado una pareja de estrellas muertas que giran una "
            "alrededor de la otra cada 83 minutos, una de las órbitas más rápidas conocidas "
            "de este tipo."
        ),
    },
    {
        "title": (
            "Confirmación espectroscópica de un exoplaneta en tránsito alrededor de una enana M"
        ),
        "level_curious": (
            "Se ha confirmado un planeta del tamaño de la Tierra orbitando una estrella "
            "pequeña y fría, con posibilidad de tener agua líquida en su superficie."
        ),
    },
    {
        "title": "Medición rutinaria de metalicidad en un cúmulo estelar ya catalogado",
        "level_curious": (
            "Un equipo ha vuelto a medir la composición química de un cúmulo de estrellas "
            "ya estudiado antes, con resultados que coinciden con lo ya conocido y sin "
            "ningún elemento nuevo que destacar."
        ),
    },
]


def _cli_version() -> str:
    """Versión instalada del binario `claude` (idéntica a `test_sdk_smoke.py::_cli_version`,
    ver docstring del módulo: T42 dejó anotado que los humos deben registrar
    versión de CLI y SDK junto a los tokens, deuda saldada aquí también para
    el Editor). Invoca el binario local con `--version`: no es tráfico hacia
    Claude, no consume presupuesto ni cuenta como llamada a un agente."""
    try:
        completed = subprocess.run(
            ["claude", "--version"], capture_output=True, text=True, timeout=5, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"(no se pudo determinar: {exc!r})"
    output = completed.stdout.strip() or completed.stderr.strip()
    return output or f"(salida vacía, returncode={completed.returncode})"


def _test_policy(*, max_editor_calls_per_night: int) -> BudgetPolicy:
    return BudgetPolicy(
        nightly_tokens=_TEST_RUN_BUDGET_TOKENS,
        editor_reserve_tokens=_TEST_EDITOR_RESERVE_TOKENS,
        max_items_per_night=len(_CANDIDATES),
        max_turns_per_agent=1,
        max_editor_calls_per_night=max_editor_calls_per_night,
        max_calls_per_item=2,
        item_timeout_s=60,
        # editor_timeout_s: el Editor recibe varios candidatos en una sola
        # llamada a Opus; 120 s es cómodo para 2-3 candidatos sintéticos de
        # una frase cada uno (mucho menos texto que los hasta
        # max_items_per_night reales de una noche completa). El timeout real
        # que se pasa a la llamada sale de `BudgetGuard.timeout_for_call()`,
        # no de este literal (mismo comentario que los humos de T41/T42).
        editor_timeout_s=120,
        run_timeout_s=180,
        window_start=_WINDOW_START,
        window_hard_stop=_WINDOW_HARD_STOP,
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _work_factory(db_session_factory, run_id: UUID, policy: BudgetPolicy) -> AgentWorkFactory:
    """Mismo patrón que `cli.py::_agent_work_factory` y
    `test_read_item_smoke.py`/`test_popularize_smoke.py::_work_factory`: cada
    llamada abre una `unit_of_work` nueva contra PostgreSQL real, con un
    `BudgetGuard` construido sobre el reloj fijo de este módulo."""
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
async def test_smoke_edit_night_llamada_real_produce_decision_y_gasto_contabilizado(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
) -> None:
    # --- ANTHROPIC_API_KEY ausente del entorno, igual que los humos de T40-T42 -
    if "ANTHROPIC_API_KEY" in os.environ:
        pytest.fail(_NO_API_KEY_REMEDY)

    # --- espía transparente delante de `query()`, para el volcado de usage ---
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
    policy = _test_policy(max_editor_calls_per_night=config.limits.max_editor_calls_per_night)

    # --- Run e Items ya READ, con sus Finding sin publicar, reales --------
    with unit_of_work(db_session_factory) as session:
        items_repo = SqlAlchemyItemRepository(session)
        findings_repo = SqlAlchemyFindingRepository(session)

        created_items: list[Item] = []
        created_findings: list[Finding] = []
        for candidate in _CANDIDATES:
            item = Item(
                source="arxiv",
                external_id=f"9999.{uuid4().hex[:5]}",
                title=candidate["title"],
                abstract="Abstract original de arXiv, no usado directamente por el Editor.",
                categories=["astro-ph.HE"],
                published_at=_WITHIN_WINDOW,
                fetched_at=_WITHIN_WINDOW,
                status=ItemStatus.READ,
            )
            created_items.append(item)
        items_repo.add_many(created_items)

        runs = SqlAlchemyRunRepository(session)
        run = Run(started_at=_WITHIN_WINDOW, budget_tokens=_TEST_RUN_BUDGET_TOKENS)
        runs.add(run)
        session.flush()
        run_id = run.id

        for item, candidate in zip(created_items, _CANDIDATES, strict=True):
            finding = Finding(
                item_id=item.id,
                run_id=run_id,
                type=FindingType.PAPER_EXPLAINED,
                title=candidate["title"],
                level_curious=candidate["level_curious"],
                level_amateur=candidate["level_curious"],
                level_technical=candidate["level_curious"],
            )
            findings_repo.add(finding)
            created_findings.append(finding)

    work = _work_factory(db_session_factory, run_id, policy)

    edit_night = EditNight(
        work=work,
        provider=AgentSDKProvider(),
        clock=FakeClock(_WITHIN_WINDOW),
        system_prompt=load_prompt("editor"),
        prompt_version=EDITOR_PROMPT_VERSION,
        model=config.models.editor,
        max_turns=policy.max_turns_per_agent,
        max_attempts=policy.max_editor_calls_per_night,
        base_tokens=config.budget.editor_base_tokens,
        tokens_per_candidate=config.budget.editor_tokens_per_candidate,
    )

    # --- la llamada real (uno o dos intentos, según haga falta) -------------
    started_at = monotonic()
    result = await edit_night(run_id=run_id)
    duration_ms = int((monotonic() - started_at) * 1000)

    # --- volcado para las decisiones abiertas y para calibrar T60 -----------
    cli_version = _cli_version()
    print(f"\n--- versión del CLI 'claude' --- {cli_version}")
    print(f"--- versión de claude_agent_sdk --- {claude_agent_sdk.__version__}")
    estimated_tokens = (
        config.budget.editor_base_tokens
        + result.candidates * config.budget.editor_tokens_per_candidate
    )
    print(
        f"\n--- resumen: outcome={result.outcome.value} candidatos={result.candidates} "
        f"intentos={result.attempts} tokens_spent={result.tokens_spent} "
        f"duration_ms={duration_ms} run_id={run_id} "
        f"estimado(base={config.budget.editor_base_tokens}"
        f"+{result.candidates}*{config.budget.editor_tokens_per_candidate}={estimated_tokens}) "
        f"cli={cli_version} sdk={claude_agent_sdk.__version__} ---"
    )
    for attempt_index, messages in enumerate(captured_calls, start=1):
        print(f"\n--- intento {attempt_index}: tipo de cada mensaje capturado ---")
        for message in messages:
            print(type(message).__name__)
        if not messages:
            print("(no se capturó ningún mensaje; algo va mal si esto se lee)")

        result_messages = [m for m in messages if isinstance(m, ResultMessage)]
        if result_messages:
            last = result_messages[-1]
            print(f"--- intento {attempt_index}: usage crudo del ResultMessage --- {last.usage!r}")
            print(
                f"--- intento {attempt_index}: model_usage crudo del ResultMessage --- "
                f"{last.model_usage!r}"
            )
            print(
                f"--- intento {attempt_index}: output_text crudo (de donde sale "
                f"AgentResult.output_text, el JSON 'publish' del Editor) ---\n{last.result!r}"
            )
        else:
            print(f"(intento {attempt_index}: no se recibió ningún ResultMessage)")

    assert len(captured_calls) == result.attempts, (
        "cada intento de EditNight debe corresponder a exactamente una llamada real a query()"
    )

    # --- el gasto se contabiliza siempre, éxito o fallo ----------------------
    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        db_tokens_used = agent_calls.tokens_used_for_run(run_id)
        db_editor_calls = agent_calls.count_for_run(run_id, AgentRole.EDITOR)

    assert db_tokens_used == result.tokens_spent, (
        "el acumulado que lee BudgetGuard (AgentCallRepository.tokens_used_for_run) debe "
        "coincidir exactamente con lo que EditNight dice haber gastado -- misma comprobación "
        "central que test_read_item_smoke.py/test_popularize_smoke.py hacen para sus agentes"
    )
    assert db_editor_calls == result.attempts

    if result.outcome is not EditOutcome.EDITED:
        pytest.fail(
            f"la llamada real al Editor no produjo una decisión válida: outcome="
            f"{result.outcome.value} tras {result.attempts} intento(s) (tokens_spent="
            f"{result.tokens_spent}, ya contabilizados en AgentCall, ver arriba). Revisa "
            "el volcado de output_text de más arriba: si es JSON con forma incorrecta de "
            "forma sistemática, el prompt de prompts/editor.md necesita ajuste."
        )

    # --- camino de éxito: publica lo aprobado, descarta el resto ------------
    print(
        f"\n--- decisión del Editor: {len(result.published)} publicados de {result.candidates} ---"
    )
    for finding in result.published:
        reason = result.reasons.get(finding.item_id, "")
        print(f"  publicado: {finding.title!r} confidence={finding.confidence} motivo={reason!r}")
    for finding in result.discarded:
        print(f"  descartado: {finding.title!r}")
    if result.unknown_item_ids:
        print(f"  AVISO: item_id desconocidos devueltos por el Editor: {result.unknown_item_ids}")

    with db_session_factory() as check_session:
        for finding in created_findings:
            row = check_session.get(FindingRow, finding.id)
            assert row is not None
            item_row = check_session.get(ItemRow, finding.item_id)
            assert item_row is not None
            if finding in result.published:
                assert row.confidence is not None
                assert row.published_at is not None
                assert item_row.status is ItemStatus.PUBLISHED
            else:
                assert row.confidence is None
                assert row.published_at is None
                assert item_row.status is ItemStatus.DISCARDED

    # --- AgentCall.prompt_version poblado, model real usado -----------------
    with db_session_factory() as check_session:
        rows = list(
            check_session.execute(
                select(AgentCallRow).where(AgentCallRow.run_id == run_id)
            ).scalars()
        )
        assert len(rows) == result.attempts
        for row in rows:
            assert row.tokens_in > 0
            assert row.tokens_out > 0
            assert row.agent is AgentRole.EDITOR
            assert row.item_id is None
            assert row.prompt_version == EDITOR_PROMPT_VERSION, (
                "cada AgentCall del Editor debe llevar la versión de prompt real "
                "(EDITOR_PROMPT_VERSION), no None"
            )
            assert row.model == config.models.editor
