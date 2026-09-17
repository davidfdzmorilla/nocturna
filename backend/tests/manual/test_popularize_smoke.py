"""Tercer test de todo el proyecto que llama a Claude de verdad (T42, equivalente
del "Nota crítica" de T41 para el Popularizer).

**NO SE LANZA en la suite normal.** Excluido por `addopts = "-m 'not manual'"`
(`pyproject.toml`) y por la fixture autouse `_require_real_claude_opt_in` de
`tests/manual/conftest.py`, que salta cualquier test de este directorio si
`NOCTURNA_ALLOW_REAL_CLAUDE` no está en el entorno -- sin importar desde
dónde se lance pytest ni qué `-m` se pida. Lo lanza el autor a mano, con:

    env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s

**Solo lleva los marcadores `manual` y `anyio`, deliberadamente sin `db`**:
misma regresión que `test_manual_markers_guard.py` congela para
`test_sdk_smoke.py` (T40) y que `test_read_item_smoke.py` (T41) ya hereda --
un segundo marcador filtrable en `tests/manual/` reabriría un camino de
selección (`pytest -m db`) que no pasa por `-m 'not manual'`. Este fichero
sigue necesitando PostgreSQL (usa `db_session_factory` vía el reexport de
`tests/manual/conftest.py`), pero eso no lo hace aparecer bajo `-m db`.

## Por qué existe

`tests/test_popularize_reading.py` demuestra que `PopularizeReading` es
correcto contra `FakeLLMProvider`. `tests/db/test_popularize_chain.py`
demuestra que la cadena completa (`PopularizeReading` -> `AgentRunner` ->
`BudgetGuard` -> repositorios reales -> `AgentSDKProvider` real) persiste el
gasto corregido con un `ResultMessage` construido a mano. Ninguno de los dos
demuestra que el prompt real de `prompts/popularizer.md` produce, contra el
modelo de verdad, un JSON válido con los tres niveles y el titular en la
forma y longitud que el prompt pide -- ni cuánto cuesta de verdad una
divulgación completa, mucho más larga en salida que una lectura (T41 midió
2.796 tokens reales frente a 6.000 estimados para el Reader; aquí la salida
es mucho más larga -- hasta 660 palabras entre los tres niveles y el
titular, según el propio prompt -- así que el número importa de verdad para
calibrar `popularizer_estimated_tokens` en T60).

Hereda el patrón de gasto de `test_read_item_smoke.py` al pie de la letra:
`authorize` en su propia unidad de trabajo, la llamada al LLM fuera de toda
transacción, `record_call` en una unidad de trabajo nueva y separada -- los
tres pasos viven dentro de `AgentRunner.run` (T42, compartido con el
Reader), este test solo verifica que ocurrieron construyendo el mismo
`AgentWorkFactory` contra PostgreSQL real que usaría `cli.py`
(`_work_factory`, más abajo, mismo patrón que `cli.py::_agent_work_factory`).

A diferencia de `test_read_item_smoke.py`, este test NO empieza desde un
`Item` `NEW`: `PopularizeReading` solo transiciona `Item` desde `READ`
(`Item.discard()`, `_ITEM_TRANSITIONS`), así que el `Item` se construye ya
`READ` y la `Reading` que lo acompaña se construye a mano, con el mismo
`interest_score >= popularizer_min_interest_score` que dispararía la
llamada real en producción -- sin pasar por el Reader, que ya tiene su
propio humo (T41).

Afirma, en el camino de éxito: `Finding` sin publicar en base de datos
(`confidence`/`published_at` a `NULL`), `AgentCall` de rol `popularizer` con
`tokens_in`/`tokens_out` > 0 y `prompt_version` poblado igual a
`POPULARIZER_PROMPT_VERSION`, y que el `Item` se queda `READ` (publicar es
del Editor, T43).

Vuelca con `print()` (visible con `-s`) los tres niveles completos y el
titular -- es lo único que dice si el prompt produce textos de la longitud y
el registro pedidos, o si hay que reescribirlo antes de T60 -- y también el
`usage` crudo de cada `ResultMessage` y el gasto real total, contrastado con
los 7.000 tokens estimados de `budget.popularizer_estimated_tokens`
(`config/pipeline.toml`).

Presupuesto de esta llamada: el `assert` de más abajo comprueba, a
posteriori, que el gasto real quedó por debajo de 12.000 tokens -- **no es
una garantía que este test imponga de antemano**, es una comprobación de lo
que de verdad costó esta llamada, con margen sobre los 7.000 estimados por
si el prompt real dispara un reintento (JSON inválido) o el modelo se acerca
al límite de palabras de los tres niveles.

**Revisado en T42** (mismo criterio que 'Corrección T42' de
`test_sdk_smoke.py`): a diferencia del assert de contexto que T40 rompió,
`result.tokens_spent` no afirma nada estrecho sobre un solo modelo -- es
justo la cifra total que `BudgetGuard` carga contra el presupuesto de la
noche, así que el preámbulo de Haiku por intento debe contar aquí, no
aislarse. Con datos reales de una llamada real: **9.120 tokens con
reintento, ~4.560 sin él**. El umbral de 12.000 deja ~2.880 tokens (un 24%)
de margen sobre el peor caso observado -- muy por encima de los ~234 tokens
que T42 vio crecer el preámbulo de Haiku por llamada entre dos versiones del
CLI (como mucho ~470 tokens extra en dos intentos), así que un umbral más
ajustado no aportaría nada salvo más falsos positivos; se mantiene en
12.000.
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

from nocturna.application.agents.prompt_loader import POPULARIZER_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.application.use_cases.popularize_reading import PopularizeOutcome, PopularizeReading
from nocturna.domain.entities import Item, ItemStatus, Reading, Run
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
# y CLAUDE.md), igual que en test_read_item_smoke.py.
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_WINDOW_START = time(0, 0)
_WINDOW_HARD_STOP = time(4, 45)

# Presupuesto de conveniencia para que `BudgetGuard.authorize` autorice de
# sobra la llamada; el tope real de esta llamada lo acota el prompt y
# `max_calls_per_item`, no esta cifra (mismo comentario que
# `_TEST_RUN_BUDGET_TOKENS` en `test_read_item_smoke.py`).
_TEST_RUN_BUDGET_TOKENS = 50_000
_TEST_EDITOR_RESERVE_TOKENS = 1_000

# Un abstract real de astro-ph.HE (estilo, no un paper existente) y la
# Reading que un Reader real habría producido a partir de él -- el mismo
# escenario narrativo que `test_read_item_smoke.py`, para que el Popularizer
# tenga algo sustancioso que divulgar.
_TITLE = "Discovery of an ultra-compact neutron star - white dwarf binary"
_SUMMARY = (
    "Se ha descubierto un sistema binario ultra-compacto formado por una estrella de "
    "neutrones y una enana blanca de baja masa, identificado mediante monitorización "
    "fotométrica de alta cadencia con un sondeo terrestre de dominio temporal. El sistema "
    "muestra un periodo orbital de 83 minutos, uno de los más cortos conocidos para esta "
    "clase de binaria. El seguimiento espectroscópico confirma una masa de la estrella de "
    "neutrones consistente con el valor canónico de 1.4 masas solares y una compañera "
    "enana blanca de aproximadamente 0.2 masas solares."
)
_OBJECTS = ("estrella de neutrones ultra-compacta", "enana blanca compañera")
_CLAIMS = (
    "El sistema tiene un periodo orbital de 83 minutos.",
    "La estrella de neutrones tiene una masa de 1.4 masas solares.",
    "La compañera enana blanca tiene una masa de 0.2 masas solares.",
)


def _test_policy(*, max_calls_per_item: int) -> BudgetPolicy:
    return BudgetPolicy(
        nightly_tokens=_TEST_RUN_BUDGET_TOKENS,
        editor_reserve_tokens=_TEST_EDITOR_RESERVE_TOKENS,
        max_items_per_night=1,
        max_turns_per_agent=1,
        max_editor_calls_per_night=1,
        max_calls_per_item=max_calls_per_item,
        # 60s es cómodo para una respuesta con tres niveles de texto (más
        # larga que la de una Reading); el timeout real que se pasa a la
        # llamada sale de `BudgetGuard.timeout_for_call()`, no de este
        # literal (mismo comentario que test_read_item_smoke.py).
        item_timeout_s=60,
        editor_timeout_s=300,
        run_timeout_s=120,
        window_start=_WINDOW_START,
        window_hard_stop=_WINDOW_HARD_STOP,
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _work_factory(db_session_factory, run_id: UUID, policy: BudgetPolicy) -> AgentWorkFactory:
    """Mismo patrón que `cli.py::_agent_work_factory` y
    `test_read_item_smoke.py::_work_factory`: cada llamada abre una
    `unit_of_work` nueva contra PostgreSQL real, con un `BudgetGuard`
    construido sobre el reloj fijo de este módulo."""
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
async def test_smoke_popularize_reading_llamada_real_produce_finding_y_gasto_contabilizado(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
) -> None:
    # --- ANTHROPIC_API_KEY ausente del entorno, igual que test_read_item_smoke.py -
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
    policy = _test_policy(max_calls_per_item=config.limits.max_calls_per_item)
    assert config.limits.popularizer_min_interest_score <= 5, (
        "el escenario fija interest_score=5 en la Reading a mano; si el umbral real "
        "subiera por encima de 5 este test dejaría de disparar la llamada al Popularizer"
    )

    # --- Item ya READ (como lo dejaría un Reader real) y Run reales ----------
    with unit_of_work(db_session_factory) as session:
        item = Item(
            source="arxiv",
            external_id=f"9999.{uuid4().hex[:5]}",
            title=_TITLE,
            abstract=(
                "Abstract original de arXiv, no usado por el Popularizer directamente: "
                "solo el título y la Reading viajan en el prompt (ver "
                "PopularizeReading._build_prompt)."
            ),
            categories=["astro-ph.HE"],
            published_at=_WITHIN_WINDOW,
            fetched_at=_WITHIN_WINDOW,
            status=ItemStatus.READ,
        )
        SqlAlchemyItemRepository(session).add_many([item])

        runs = SqlAlchemyRunRepository(session)
        run = Run(started_at=_WITHIN_WINDOW, budget_tokens=_TEST_RUN_BUDGET_TOKENS)
        runs.add(run)
        session.flush()
        run_id = run.id

    reading = Reading(
        item_id=item.id,
        summary=_SUMMARY,
        objects=_OBJECTS,
        claims=_CLAIMS,
        interest_score=5,
        tokens_in=1200,
        tokens_out=300,
        model=config.models.reader,
    )

    work = _work_factory(db_session_factory, run_id, policy)

    popularize = PopularizeReading(
        work=work,
        provider=AgentSDKProvider(),
        system_prompt=load_prompt("popularizer"),
        prompt_version=POPULARIZER_PROMPT_VERSION,
        model=config.models.popularizer,
        max_turns=policy.max_turns_per_agent,
        estimated_tokens=config.budget.popularizer_estimated_tokens,
        max_attempts=policy.max_calls_per_item,
        min_interest_score=config.limits.popularizer_min_interest_score,
    )

    # --- la llamada real (uno o dos intentos, según haga falta) -------------
    started_at = monotonic()
    result = await popularize(item=item, reading=reading)
    duration_ms = int((monotonic() - started_at) * 1000)

    # --- volcado para las decisiones abiertas y para calibrar T60 -----------
    print(
        f"\n--- resumen: outcome={result.outcome.value} intentos={result.attempts} "
        f"tokens_spent={result.tokens_spent} duration_ms={duration_ms} run_id={run_id} "
        f"estimado(popularizer_estimated_tokens)={config.budget.popularizer_estimated_tokens} ---"
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
                f"AgentResult.output_text) ---\n{last.result!r}"
            )
        else:
            print(f"(intento {attempt_index}: no se recibió ningún ResultMessage)")

    assert len(captured_calls) == result.attempts, (
        "cada intento de PopularizeReading debe corresponder a exactamente una llamada "
        "real a query()"
    )

    # --- el gasto se contabiliza siempre, éxito o fallo ----------------------
    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        db_tokens_used = agent_calls.tokens_used_for_run(run_id)
        db_popularizer_calls = agent_calls.count_for_run(run_id, AgentRole.POPULARIZER)

    assert db_tokens_used == result.tokens_spent, (
        "el acumulado que lee BudgetGuard (AgentCallRepository.tokens_used_for_run) debe "
        "coincidir exactamente con lo que PopularizeReading dice haber gastado -- la misma "
        "comprobación central que test_read_item_smoke.py hace para el Reader"
    )
    assert db_popularizer_calls == result.attempts

    if result.outcome is not PopularizeOutcome.POPULARIZED:
        pytest.fail(
            f"la llamada real al Popularizer no produjo un Finding válido: outcome="
            f"{result.outcome.value} tras {result.attempts} intento(s) (tokens_spent="
            f"{result.tokens_spent}, ya contabilizados en AgentCall, ver arriba). Revisa "
            "el volcado de output_text de más arriba: si es JSON con forma incorrecta de "
            "forma sistemática, el prompt de prompts/popularizer.md necesita ajuste."
        )

    # --- camino de éxito: Finding, Item, AgentCall.prompt_version -----------
    finding = result.finding
    assert finding is not None
    assert finding.confidence is None
    assert finding.published_at is None

    # --- volcado íntegro de los tres niveles y el titular: es lo único que --
    # --- dice si el prompt produce la longitud y el registro pedidos --------
    print(f"\n--- titular ---\n{finding.title}")
    print(f"\n--- nivel curioso ({len(finding.level_curious.split())} palabras) ---")
    print(finding.level_curious)
    print(f"\n--- nivel aficionado ({len(finding.level_amateur.split())} palabras) ---")
    print(finding.level_amateur)
    print(f"\n--- nivel técnico ({len(finding.level_technical.split())} palabras) ---")
    print(finding.level_technical)

    with db_session_factory() as check_session:
        item_row = check_session.get(ItemRow, item.id)
        assert item_row is not None
        assert item_row.status is ItemStatus.READ, (
            "publicar es del Editor (T43); un Item con Finding sin publicar se queda READ"
        )

        finding_row = check_session.get(FindingRow, finding.id)
        assert finding_row is not None
        assert finding_row.confidence is None
        assert finding_row.published_at is None
        assert finding_row.title == finding.title

        call_rows = list(
            check_session.execute(
                select(AgentCallRow).where(AgentCallRow.run_id == run_id)
            ).scalars()
        )
        assert len(call_rows) == result.attempts
        for row in call_rows:
            assert row.tokens_in > 0
            assert row.tokens_out > 0
            assert row.agent is AgentRole.POPULARIZER
            assert row.prompt_version == POPULARIZER_PROMPT_VERSION, (
                "cada AgentCall del Popularizer debe llevar la versión de prompt real "
                "(POPULARIZER_PROMPT_VERSION), no None"
            )

    # Tope de gasto a posteriori (ver docstring del módulo, 'Revisado en
    # T42'): no una garantía previa, una comprobación de lo que de verdad
    # costó esta llamada, con margen real sobre 9.120 (con reintento) /
    # ~4.560 (sin él) observados -- no sobre el total mezclado con el
    # preámbulo de Haiku de un único intento, que aquí sí cuenta a propósito
    # (es gasto real cargado contra BudgetGuard, no ruido a aislar).
    assert result.tokens_spent < 12_000, (
        f"la llamada real gastó {result.tokens_spent} tokens, por encima del tope de "
        "12 000 (ver 'Revisado en T42' en el docstring del módulo: 24% de margen sobre los "
        "9.120 observados con reintento). Si esto salta sin que el prompt haya cambiado, "
        "sospecha primero del preámbulo de Haiku (imprime model_usage del volcado de arriba "
        "y compáralo entrada por entrada, intento por intento) antes de subir el umbral sin "
        "más; contrasta también "
        f"contra los {config.budget.popularizer_estimated_tokens} estimados en "
        "config/pipeline.toml"
    )
