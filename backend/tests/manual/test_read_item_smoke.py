"""Segundo test de todo el proyecto que llama a Claude de verdad (T41, "Nota crítica").

**NO SE LANZA en la suite normal.** Excluido por `addopts = "-m 'not manual'"`
(`pyproject.toml`) y, desde este cambio, por la fixture autouse
`_require_real_claude_opt_in` de `tests/manual/conftest.py`, que salta
cualquier test de este directorio si `NOCTURNA_ALLOW_REAL_CLAUDE` no está en
el entorno -- sin importar desde dónde se lance pytest ni qué `-m` se pida
(ver `docs/OPEN_DECISIONS.md`). Lo lanza el autor a mano, con:

    env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s

**Solo lleva los marcadores `manual` y `anyio`, deliberadamente sin `db`**:
la misma regresión que `test_manual_markers_guard.py` congela para
`test_sdk_smoke.py` (T40) -- un segundo marcador filtrable en
`tests/manual/` reabriría un camino de selección (`pytest -m db`) que no
pasa por `-m 'not manual'`. Este fichero sigue necesitando PostgreSQL (usa
`db_session_factory` vía el reexport de `tests/manual/conftest.py`), pero
eso no lo hace aparecer bajo `-m db`.

## Por qué existe: la "Nota crítica" de T41

`tests/manual/test_sdk_smoke.py` (T40) demuestra que `AgentSDKProvider`
extrae tokens correctamente **en aislamiento**: llama al proveedor
directamente, sin pasar por `ReadItem`. Eso es necesario pero no suficiente:
`application/use_cases/read_item.py::ReadItem` es quien de verdad encadena
`BudgetGuard.authorize` → `provider.run_agent` → `BudgetGuard.record_call`
en producción (`cli.py::_run_item`, T44 más adelante), y esa cadena completa
--incluida la persistencia final de `AgentCall`-- no la ejercita ningún test
con un proveedor real. Este fichero es esa comprobación: construye un
`ReadItem` de verdad, con `AgentSDKProvider` de verdad, sobre un `Item` con
un abstract real, y compara lo que queda en base de datos contra lo que el
stream de mensajes del SDK reportó de verdad.

Hereda el patrón de gasto de `test_sdk_smoke.py` al pie de la letra, pero
`ReadItem` ya lo aplica internamente (T41, ADR 0006 § 2) en vez de que este
test lo repita a mano:

(a) `ANTHROPIC_API_KEY` ausente del entorno, comprobada aquí igual que en
    `test_sdk_smoke.py` -- `AgentSDKProvider.__init__` también la comprueba,
    pero fallar aquí primero da un mensaje más claro que el de
    `ApiKeyInEnvironment`;
(b) un `FakeClock` fijado dentro de la ventana de ejecución (00:00-04:45),
    para que el test se pueda lanzar a cualquier hora del día real;
(c) `authorize` en su propia unidad de trabajo, la llamada al LLM fuera de
    toda transacción, y `record_call` en una unidad de trabajo nueva y
    separada -- los tres pasos viven dentro de `ReadItem.__call__`, este
    test solo verifica que ocurrieron construyendo el mismo
    `AgentWorkFactory` contra PostgreSQL real que usaría `cli.py`
    (`_work_factory`, más abajo, con el mismo patrón que
    `cli.py::_agent_work_factory`);
(d) el camino de fallo contabiliza el gasto igual que el de éxito, también
    aquí: si la llamada real no produce un `Reading` válido (JSON inválido
    agotados los reintentos, límite de tasa, timeout, error genérico),
    `ReadItem` ya registró el `AgentCall` correspondiente antes de que este
    test lo compruebe y llame a `pytest.fail`;
(e) un volcado con `print()` (visible con `-s`) del tipo de cada mensaje
    capturado por intento, el `output_text` crudo del último `ResultMessage`
    (de dónde sale `output_text` en `AgentSDKProvider`, según su propio
    docstring) y el `usage` crudo de cada `ResultMessage` -- es lo único que
    dice si el prompt real de `prompts/reader.md` produce JSON limpio al
    primer intento o si dispara el reintento (T41 usa
    `config.limits.max_calls_per_item`, el valor real de
    `config/pipeline.toml`, no un `1` de conveniencia, precisamente para
    poder observar esto).

Se interpone un espía transparente delante de `agent_sdk_provider.query`
(reenvía cada mensaje tal cual, solo los colecciona de paso, uno por
intento) en vez de repetir la llamada aparte: una sola llamada real por
intento en todo el test.

Afirma, en el camino de éxito: `Reading` persistida con `interest_score` en
1-5, `AgentCall` con `prompt_version` poblado igual a `READER_PROMPT_VERSION`,
el ítem en `read`, y que el acumulado de `AgentCallRepository` (leído con una
sesión nueva, como si fuera un reinicio) coincide exactamente con
`ReadItemResult.tokens_spent` -- la comprobación central de la "Nota
crítica": la cadena completa no pierde ni duplica tokens entre `authorize` y
la fila que queda en `agent_calls`.

Presupuesto de esta llamada: se espera que quede por debajo de 8 000 tokens
(`budget.reader_estimated_tokens` en `config/pipeline.toml` ya documenta la
composición aproximada: ~290 del prompt de rol + abstract + ~950 de gasto
lateral de Haiku que T40 midió por llamada + salida, doblado por margen). El
`assert` de más abajo comprueba esa expectativa a posteriori, sobre el gasto
ya incurrido -- no es una garantía que este test imponga de antemano, y un
segundo intento (si el primero produce JSON inválido) la dobla sin que eso
sea, por sí mismo, un fallo de este test.
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
from sqlalchemy import select

from nocturna.application.agents.prompt_loader import READER_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.application.use_cases.read_item import ReadItem, ReadOutcome
from nocturna.domain.entities import Item, ItemStatus, Run
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow, ItemRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
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
# y CLAUDE.md), igual que (d) en tests/manual/test_sdk_smoke.py.
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_WINDOW_START = time(0, 0)
_WINDOW_HARD_STOP = time(4, 45)

# Presupuesto de conveniencia para que `BudgetGuard.authorize` autorice de
# sobra la llamada; el tope real de esta llamada lo acota el prompt y
# `max_calls_per_item`, no esta cifra (ver `test_sdk_smoke.py`, mismo
# comentario en `_TEST_RUN_BUDGET_TOKENS`).
_TEST_RUN_BUDGET_TOKENS = 50_000
_TEST_EDITOR_RESERVE_TOKENS = 1_000
# Igual que `budget.reader_estimated_tokens` en config/pipeline.toml: no un
# literal inventado para este test, el mismo valor calibrado que usaría
# run-item/T44 de verdad.
_ESTIMATED_TOKENS = 6_000

# Un abstract real de astro-ph.HE (estilo, no un paper existente), lo
# bastante largo para que el Reader tenga algo que resumir de verdad, lo
# bastante corto para no inflar tokens_in sin necesidad.
_TITLE = "Discovery of an ultra-compact neutron star - white dwarf binary"
_ABSTRACT = (
    "We report the discovery of a compact binary system consisting of a neutron star and "
    "a low-mass white dwarf companion, identified through high-cadence photometric "
    "monitoring with a ground-based time-domain survey. The system shows an orbital "
    "period of 83 minutes, among the shortest known for this class of binary. Follow-up "
    "spectroscopy confirms a neutron star mass consistent with the canonical value of "
    "1.4 solar masses and a white dwarf companion of approximately 0.2 solar masses. We "
    "discuss implications for the formation channel of ultra-compact X-ray binaries and "
    "predict a gravitational-wave signal potentially detectable by future space-based "
    "interferometers within the next decade."
)


def _test_policy(*, max_calls_per_item: int) -> BudgetPolicy:
    return BudgetPolicy(
        nightly_tokens=_TEST_RUN_BUDGET_TOKENS,
        editor_reserve_tokens=_TEST_EDITOR_RESERVE_TOKENS,
        max_items_per_night=1,
        max_turns_per_agent=1,
        max_editor_calls_per_night=1,
        max_calls_per_item=max_calls_per_item,
        # 30s es cómodo para una respuesta de un puñado de campos JSON; el
        # timeout real que se pasa a la llamada sale de
        # `BudgetGuard.timeout_for_call()`, no de este literal (ver (b) en
        # el docstring de test_sdk_smoke.py).
        item_timeout_s=30,
        run_timeout_s=60,
        window_start=_WINDOW_START,
        window_hard_stop=_WINDOW_HARD_STOP,
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _work_factory(db_session_factory, run_id: UUID, policy: BudgetPolicy) -> AgentWorkFactory:
    """Mismo patrón que `cli.py::_agent_work_factory` (T41, paso 8): cada
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
                agent_calls=agent_calls,
            )

    return _open


@pytest.mark.anyio
async def test_smoke_read_item_llamada_real_produce_reading_persistida_y_gasto_contabilizado(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
) -> None:
    # --- (a) ANTHROPIC_API_KEY ausente del entorno --------------------------
    if "ANTHROPIC_API_KEY" in os.environ:
        pytest.fail(_NO_API_KEY_REMEDY)

    # --- espía transparente delante de `query()`, por intento, para (e) -----
    # Cada intento de ReadItem abre su propia `query()`; agrupar los mensajes
    # por intento (en vez de en una única lista plana) es lo que permite
    # comprobar `len(captured_calls) == result.attempts` más abajo.
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
    policy = _test_policy(max_calls_per_item=config.limits.max_calls_per_item)

    # --- Item y Run reales, en su propia unidad de trabajo -------------------
    with unit_of_work(db_session_factory) as session:
        item = Item(
            source="arxiv",
            external_id=f"9999.{uuid4().hex[:5]}",
            title=_TITLE,
            abstract=_ABSTRACT,
            categories=["astro-ph.HE"],
            published_at=_WITHIN_WINDOW,
            fetched_at=_WITHIN_WINDOW,
        )
        SqlAlchemyItemRepository(session).add_many([item])

        runs = SqlAlchemyRunRepository(session)
        run = Run(started_at=_WITHIN_WINDOW, budget_tokens=_TEST_RUN_BUDGET_TOKENS)
        runs.add(run)
        session.flush()
        run_id = run.id

    work = _work_factory(db_session_factory, run_id, policy)

    read_item = ReadItem(
        work=work,
        provider=AgentSDKProvider(),
        system_prompt=load_prompt("reader"),
        prompt_version=READER_PROMPT_VERSION,
        model=config.models.reader,
        max_turns=policy.max_turns_per_agent,
        estimated_tokens=_ESTIMATED_TOKENS,
        max_attempts=policy.max_calls_per_item,
    )

    # --- la llamada real (uno o dos intentos, según haga falta) -------------
    started_at = monotonic()
    result = await read_item(item)
    duration_ms = int((monotonic() - started_at) * 1000)

    # --- (e) volcado para las decisiones abiertas y para calibrar T60 -------
    print(
        f"\n--- resumen: outcome={result.outcome.value} intentos={result.attempts} "
        f"tokens_spent={result.tokens_spent} duration_ms={duration_ms} run_id={run_id} ---"
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
                f"--- intento {attempt_index}: output_text crudo (de donde sale "
                f"AgentResult.output_text) ---\n{last.result!r}"
            )
        else:
            print(f"(intento {attempt_index}: no se recibió ningún ResultMessage)")

    assert len(captured_calls) == result.attempts, (
        "cada intento de ReadItem debe corresponder a exactamente una llamada real a query()"
    )

    # --- (c)/(d) el gasto se contabiliza siempre, éxito o fallo -------------
    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        db_tokens_used = agent_calls.tokens_used_for_run(run_id)
        db_reader_calls = agent_calls.count_for_run(run_id, AgentRole.READER)

    assert db_tokens_used == result.tokens_spent, (
        "el acumulado que lee BudgetGuard (AgentCallRepository.tokens_used_for_run) debe "
        "coincidir exactamente con lo que ReadItem dice haber gastado -- la comprobación "
        "central de la 'Nota crítica' de T41: la cadena completa no pierde ni duplica "
        "tokens entre authorize() y la fila que queda en agent_calls"
    )
    assert db_reader_calls == result.attempts

    if result.outcome is not ReadOutcome.READ:
        pytest.fail(
            f"la llamada real al Reader no produjo un Reading válido: outcome="
            f"{result.outcome.value} tras {result.attempts} intento(s) (tokens_spent="
            f"{result.tokens_spent}, ya contabilizados en AgentCall, ver arriba). Revisa "
            "el volcado de output_text de más arriba: si es JSON con forma incorrecta de "
            "forma sistemática, el prompt de prompts/reader.md necesita ajuste."
        )

    # --- camino de éxito: Reading, Item, AgentCall.prompt_version ----------
    reading = result.reading
    assert reading is not None
    assert 1 <= reading.interest_score <= 5
    assert reading.model == config.models.reader
    assert reading.tokens_in > 0
    assert reading.tokens_out > 0

    with db_session_factory() as check_session:
        item_row = check_session.get(ItemRow, item.id)
        assert item_row is not None
        assert item_row.status is ItemStatus.READ

        persisted_reading = SqlAlchemyReadingRepository(check_session).get_for_item(item.id)
        assert persisted_reading is not None
        assert persisted_reading.interest_score == reading.interest_score
        assert persisted_reading.summary == reading.summary

        call_rows = list(
            check_session.execute(
                select(AgentCallRow).where(AgentCallRow.run_id == run_id)
            ).scalars()
        )
        assert len(call_rows) == result.attempts
        for row in call_rows:
            assert row.prompt_version == READER_PROMPT_VERSION, (
                "cada AgentCall del Reader debe llevar la versión de prompt real "
                "(READER_PROMPT_VERSION), no None"
            )

    # Tope de gasto a posteriori (ver docstring del módulo): no una garantía
    # previa, una comprobación de lo que de verdad costó esta llamada.
    assert result.tokens_spent < 8_000, (
        f"la llamada real gastó {result.tokens_spent} tokens, por encima del tope de "
        "8 000 esperado para una única lectura (ver docstring del módulo); revisa el "
        "volcado de usage/model_usage de arriba para entender el desglose"
    )
