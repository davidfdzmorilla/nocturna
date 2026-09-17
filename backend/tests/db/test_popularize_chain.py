"""Cadena real del Popularizer (T42), equivalente de `test_read_item_chain.py` (T41).

`PopularizeReading` real + `AgentRunner` real (`application/agents/runner.py`,
compartido con el Reader desde el refactor de T42) + `BudgetGuard` real +
repositorios SQLAlchemy reales + `AgentSDKProvider` real. Lo único doblado es
`agent_sdk_provider.query` (`tests/helpers/sdk_doubles.py`), sustituido por un
`ResultMessage` real con la MISMA forma que T40 volcó de una llamada de
verdad: `usage` en snake_case con solo el modelo pedido (Sonnet), y
`model_usage` en camelCase con una entrada adicional de Haiku -- el gasto
lateral de sesión que el CLI factura pero que `usage` por sí solo no ve. Si
`PopularizeReading` (o el runner, o el proveedor) solo contabilizara `usage`,
el `AgentCall` persistido subregistraría el gasto igual que documenta
`test_read_item_chain.py` para el Reader; este fichero reproduce la misma
comprobación para el Popularizer, sobre el mismo `AgentRunner` que ahora
comparten los dos agentes.

`tests/test_popularize_reading.py` ya prueba `PopularizeReading` de punta a
punta con `FakeLLMProvider` y repositorios en memoria (T42): eso demuestra
que la lógica del caso de uso es correcta contra un doble. Lo que ese
fichero NO puede demostrar es que la cadena sobrevive al paso por el
proveedor real y por PostgreSQL de verdad -- en concreto:

- que el `Finding` que persiste `PopularizeReading` (sin publicar,
  `confidence`/`published_at` a `NULL`) pasa de verdad por el `CHECK
  confidence_published_at_together` de la tabla `findings`
  (`infrastructure/db/models.py`): que la base lo acepte es lo que confirma
  que ese `CHECK` y el invariante de `Finding.__post_init__` (`ambos
  informados o ambos vacíos`) están alineados, no solo que la entidad de
  dominio lo permite en memoria;
- que `FindingRepository.unpublished_for_run(run_id)` (el método real,
  `SqlAlchemyFindingRepository`, `infrastructure/db/repositories.py`) lo
  devuelve: ese es el camino por el que T43 (el Editor) encontrará a los
  candidatos de la noche;
- que el `AgentCall` de rol `popularizer` queda con los tokens correctos
  (máximo entre `usage` y la suma de `model_usage`, Haiku incluido) y
  `prompt_version == POPULARIZER_PROMPT_VERSION`, no `None`;
- que `Run.tokens_used` queda incrementado en la misma cantidad.

## La sonda de pool (ADR 0006 § 2)

Igual que `test_read_item_chain.py`, tercer test: `AgentRunner.run` (T42
extrajo esta maquinaria de `ReadItem`, compartida ahora por los dos
agentes) abre la llamada al LLM **fuera** de toda unidad de trabajo. La
única forma de comprobarlo es observar `test_engine.pool.checkedout()` --
el número de conexiones del pool en uso -- justo antes de que el doble de
`query()` emita el primer mensaje: debe ser `0`. El contador de unidades de
trabajo (cuántas veces se invocó `AgentWorkFactory`) no detecta una fusión
de `authorize`+`record_call` que mantuviera la sesión abierta durante la
espera al LLM; solo esta sonda lo hace (verificado por mutación, ver el
informe de la tarea).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, time
from typing import Any

import pytest
import sqlalchemy as sa
from factories import make_item, make_reading, make_run
from fakes.clock import FakeClock
from helpers.sdk_doubles import build_fake_query, make_result_message

from nocturna import cli
from nocturna.application.agents.prompt_loader import POPULARIZER_PROMPT_VERSION, load_prompt
from nocturna.application.budget import BudgetPolicy
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.popularize_reading import PopularizeOutcome, PopularizeReading
from nocturna.domain.entities import AgentCallStatus, ItemStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.models import AgentCallRow, FindingRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_MODEL = "claude-sonnet-5"
_MIN_INTEREST_SCORE = 4


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 300_000,
        "editor_reserve_tokens": 60_000,
        "max_items_per_night": 10,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 2,
        "item_timeout_s": 180,
        "run_timeout_s": 16_200,
        "window_start": time(0, 0),
        "window_hard_stop": time(4, 45),
        "weekly_reset_weekday": 0,
        "weekly_reset_hour": 0,
        "reset_day_multiplier": 1.0,
    }
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


def _valid_popularizer_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "title": "Un titular real generado por el orquestador",
        "level_curious": "Explicación para curiosos, sin jerga.",
        "level_amateur": "Explicación para aficionados, con algo de vocabulario técnico.",
        "level_technical": "Explicación técnica, con cifras y limitaciones.",
    }
    defaults.update(overrides)
    return defaults


def _persist_run_and_read_item(db_session_factory, *, budget_tokens: int) -> tuple[Any, Any]:
    """Crea un `Run` y un `Item` ya `READ` persistidos de verdad.

    `PopularizeReading` solo acepta transicionar `Item` desde `READ`
    (`Item.discard()`, `_ITEM_TRANSITIONS`): un `Item` recién ingerido
    (`NEW`, el valor por defecto de `make_item`) simularía un ítem que el
    Reader nunca tocó, que no es el escenario de este fichero.
    """
    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        items = SqlAlchemyItemRepository(session)
        run = make_run(budget_tokens=budget_tokens)
        runs.add(run)
        item = make_item(status=ItemStatus.READ)
        items.add_many([item])
        session.flush()
        return run.id, item.id


def _make_popularize_reading(
    *,
    work: AgentWorkFactory,
    provider: AgentSDKProvider,
    max_attempts: int,
    estimated_tokens: int = 100,
    min_interest_score: int = _MIN_INTEREST_SCORE,
) -> PopularizeReading:
    return PopularizeReading(
        work=work,
        provider=provider,
        system_prompt=load_prompt("popularizer"),
        prompt_version=POPULARIZER_PROMPT_VERSION,
        model=_MODEL,
        max_turns=3,
        estimated_tokens=estimated_tokens,
        max_attempts=max_attempts,
        min_interest_score=min_interest_score,
    )


def _agent_call_rows_for_run(db_session_factory, run_id) -> list[AgentCallRow]:
    """Sesión NUEVA, abierta después del commit: la fila debe sobrevivir fuera
    de la unidad de trabajo que la escribió, igual que exige `test_read_item_chain.py`."""
    with db_session_factory() as check_session:
        stmt = sa.select(AgentCallRow).where(AgentCallRow.run_id == run_id)
        return list(check_session.execute(stmt).scalars().all())


# --- 1. Camino feliz: Finding sin publicar, unpublished_for_run lo ve, -----
# --- el AgentCall queda correcto y Run.tokens_used se incrementa -----------


async def test_orquestador_real_persiste_finding_sin_publicar_y_agent_call_de_popularizer(
    db_session_factory, monkeypatch
) -> None:
    # Misma forma que el volcado real de T40 (docstring de
    # `agent_sdk_provider.py`): `usage` solo ve el modelo pedido (Sonnet);
    # `model_usage` añade una entrada de Haiku que el CLI gastó por su
    # cuenta y que SÍ se factura.
    usage = {
        "input_tokens": 980,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "output_tokens": 442,
    }
    model_usage = {
        "claude-haiku-4-5-20251001": {
            "inputTokens": 910,
            "outputTokens": 12,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
        _MODEL: {
            "inputTokens": 980,
            "outputTokens": 442,
            "cacheCreationInputTokens": 0,
            "cacheReadInputTokens": 0,
        },
    }
    # Igual que en `test_read_item_chain.py`: el esperado sale del máximo
    # componente a componente entre `usage` (solo el modelo pedido) y la
    # SUMA de todas las entradas de `model_usage` (Haiku incluido) -- nunca
    # la suma de ambos. Si `PopularizeReading`/`AgentRunner` solo miraran
    # `usage`, registrarían 980/442 en vez de esto.
    expected_tokens_in = max(
        usage["input_tokens"], sum(v["inputTokens"] for v in model_usage.values())
    )
    expected_tokens_out = max(
        usage["output_tokens"], sum(v["outputTokens"] for v in model_usage.values())
    )
    assert (expected_tokens_in, expected_tokens_out) == (1890, 454), (
        "el escenario debe reproducir el mismo tipo de subregistro que el hallazgo de T40, "
        "o esta prueba no distinguiría un AgentRunner que solo mirara 'usage'"
    )

    frame = make_result_message(
        result=json.dumps(_valid_popularizer_json()),
        usage=usage,
        model_usage=model_usage,
    )
    # `sleep_before_s` garantiza duration_ms > 0 de verdad (monotonic() no
    # avanza de forma fiable entre dos llamadas consecutivas sin él).
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([frame], sleep_before_s=0.005), raising=True
    )

    policy = _policy()
    run_id, item_id = _persist_run_and_read_item(
        db_session_factory, budget_tokens=policy.nightly_tokens
    )
    work = cli._agent_work_factory(db_session_factory, run_id, policy, FakeClock(_WITHIN_WINDOW))
    provider = AgentSDKProvider()

    with work() as w:
        item = w.items.get(item_id)
    assert item is not None

    reading = make_reading(item_id, interest_score=5)

    popularize = _make_popularize_reading(work=work, provider=provider, max_attempts=1)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.POPULARIZED
    assert result.finding is not None
    assert result.tokens_spent == expected_tokens_in + expected_tokens_out

    rows = _agent_call_rows_for_run(db_session_factory, run_id)
    assert len(rows) == 1, "exactamente una fila de AgentCall para esta llamada"
    row = rows[0]
    assert (row.tokens_in, row.tokens_out) == (expected_tokens_in, expected_tokens_out), (
        "la fila debe reflejar el gasto REAL (máximo entre usage y model_usage, Haiku "
        "incluido), no el subregistro de 'usage' a secas"
    )
    assert row.agent is AgentRole.POPULARIZER
    assert row.item_id == item_id
    assert row.model == _MODEL
    assert row.status is AgentCallStatus.OK
    assert row.duration_ms > 0
    assert row.prompt_version == POPULARIZER_PROMPT_VERSION

    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == expected_tokens_in + expected_tokens_out

        run_row = SqlAlchemyRunRepository(check_session).get(run_id)
        assert run_row.tokens_used == expected_tokens_in + expected_tokens_out

        # --- el CHECK de la tabla acepta confidence/published_at a NULL ----
        finding_row = check_session.get(FindingRow, result.finding.id)
        assert finding_row is not None
        assert finding_row.confidence is None
        assert finding_row.published_at is None
        assert finding_row.title == _valid_popularizer_json()["title"]

        # --- el método real que usará el Editor (T43) lo encuentra ---------
        findings = SqlAlchemyFindingRepository(check_session)
        unpublished = findings.unpublished_for_run(run_id)
        assert [f.id for f in unpublished] == [result.finding.id]
        assert unpublished[0].confidence is None
        assert unpublished[0].published_at is None

        persisted_item = SqlAlchemyItemRepository(check_session).get(item_id)
        assert persisted_item is not None
        assert persisted_item.status is ItemStatus.READ, (
            "publicar es del Editor (T43); un Item con Finding sin publicar se queda READ"
        )


# --- 2. ADR 0006 § 2: la llamada al LLM ocurre fuera de toda transacción ---
# --- (la sonda de pool) -----------------------------------------------------


async def test_llamada_al_llm_ocurre_fuera_de_toda_transaccion_pool_checkedout_en_cero(
    db_session_factory, test_engine, monkeypatch
) -> None:
    """ADR 0006 § 2, para el Popularizer sobre el mismo `AgentRunner` (T42)
    que el Reader ya verifica en `test_read_item_chain.py`: la llamada al
    proveedor ocurre **fuera** de toda unidad de trabajo, para que ninguna
    conexión del pool de PostgreSQL quede retenida mientras se espera al
    modelo. Registra `test_engine.pool.checkedout()` -- desde una sesión
    completamente ajena a las que `PopularizeReading`/`AgentRunner` abren
    por dentro -- justo antes de que el doble de `query()` emita el primer
    mensaje de la llamada real. Con el código actual debe dar `0`; una
    fusión que retuviera la sesión durante la llamada daría `1` (verificado
    por mutación, ver el informe de la tarea).
    """
    observed: list[int] = []
    frame = make_result_message(
        result=json.dumps(_valid_popularizer_json()),
        usage={
            "input_tokens": 900,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "output_tokens": 400,
        },
        model_usage=None,
    )

    async def _probing_query(*, prompt: str, options: object):
        observed.append(test_engine.pool.checkedout())
        yield frame

    monkeypatch.setattr(agent_sdk_provider, "query", _probing_query, raising=True)

    policy = _policy()
    run_id, item_id = _persist_run_and_read_item(
        db_session_factory, budget_tokens=policy.nightly_tokens
    )
    work = cli._agent_work_factory(db_session_factory, run_id, policy, FakeClock(_WITHIN_WINDOW))

    with work() as w:
        item = w.items.get(item_id)
    assert item is not None

    reading = make_reading(item_id, interest_score=5)
    popularize = _make_popularize_reading(work=work, provider=AgentSDKProvider(), max_attempts=1)

    result = await popularize(item=item, reading=reading)

    assert result.outcome is PopularizeOutcome.POPULARIZED
    assert observed == [0], (
        "el pool debe tener 0 conexiones en uso justo antes de la llamada real al "
        "LLM: si el guardia y el registro de gasto compartieran la misma unidad de "
        "trabajo que la llamada, aquí habría al menos 1 -- ver ADR 0006 § 2"
    )
