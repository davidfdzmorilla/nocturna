"""Único test de todo el proyecto que llama a Claude de verdad (T40, paso 5).

**NO SE LANZA en la suite normal.** `pyproject.toml` lo excluye por
`addopts = "-m 'not manual'"`; lo lanza el autor a mano, con:

    env -u ANTHROPIC_API_KEY uv run pytest -m manual -s

**Solo lleva el marcador `manual`, deliberadamente sin `db`.** Sigue
usando `db_session_factory` (PostgreSQL de `nocturna_test`, igual que el
resto de `tests/db/`; requiere `docker compose up -d`), pero `-m` en la
línea de comandos de pytest sustituye por completo el `-m 'not manual'` de
`addopts` en vez de componerse con él (comportamiento de `argparse`, no
un bug de este proyecto): `uv run pytest -m db` —el comando documentado
para lanzar los tests de base de datos— recolectaba y ejecutaba este test
de verdad, sin que la fixture `_no_claude` (que solo se autoexime por el
marcador `manual`) actuara como segunda barrera. Quitar `db` de aquí es la
corrección mínima y reversible: este fichero sigue necesitando Postgres,
pero ya no aparece bajo ningún filtro `-m` que no sea exactamente
`manual`. Un mecanismo más fuerte (variable de entorno
`NOCTURNA_ALLOW_REAL_CLAUDE=1`, un flag `--run-manual` propio) queda como
decisión abierta para cuando T41 añada más tests manuales — no se
implementa aquí porque vive en `conftest.py`/`pyproject.toml`, fuera del
alcance de este fichero.

Precondiciones:

- `docker compose up -d` (PostgreSQL de `nocturna_test`, igual que el resto
  de `tests/db/`).
- El CLI `claude` logueado con la suscripción Claude Max del autor
  (`CLAUDE.md`, "Restricción que gobierna todo el diseño").
- `ANTHROPIC_API_KEY` ausente del entorno — el propio test lo comprueba y
  falla (no se salta) si no lo está, ver `test_smoke_end_to_end...` más
  abajo.

Qué cubre, en el orden que pide el plan de T40:

(a) que no hay `ANTHROPIC_API_KEY` en el entorno;
(b) una llamada mínima de verdad a través de `AgentSDKProvider.run_agent`,
    con el modelo leído de `pipeline.toml` (nunca hardcodeado), `max_turns`
    leído de `BudgetPolicy.max_turns_per_agent` y `timeout_s` leído de
    `BudgetGuard.timeout_for_call()` (nunca un literal: ese método es
    quien garantiza que ninguna llamada sobrevive a `hard_stop`), y
    afirmaciones sobre el `AgentResult`;
(c) la frontera de gasto completa, en el orden que T41-T44 deben repetir
    cada noche: `BudgetGuard.authorize` dentro de una `unit_of_work`, la
    llamada al LLM **fuera de cualquier transacción** (una transacción de
    PostgreSQL abierta durante hasta `item_timeout_s` de espera al LLM
    bloquearía una conexión del pool para nada), y `BudgetGuard.record_call`
    en una `unit_of_work` nueva y separada. El camino de fallo se modela
    igual de explícito que el de éxito: si `run_agent` lanza `LLMError`
    con tokens ya conocidos (`domain/errors.py` los transporta justo para
    esto), esos tokens se contabilizan en `AgentCall` exactamente igual
    que si la llamada hubiera tenido éxito — la suscripción ya los pagó,
    con independencia de si el resultado fue utilizable. Un `Run` cerrado
    o un fallo por cualquier otro motivo antes de ese `record_call` sí
    haría rollback (`unit_of_work`, ADR 0003); ese caso no es el que este
    test ejercita;
(d) un `FakeClock` fijado dentro de la ventana de ejecución (00:00-04:45),
    para que el test se pueda lanzar a cualquier hora del día;
(e) un volcado con `print()` (visible con `-s`) del **tipo de cada
    mensaje** capturado durante la llamada real, y el contenido completo
    de los `ResultMessage`/`AssistantMessage`, para cerrar tres decisiones
    abiertas: si el SDK expone un id de modelo "efectivo" (`model_usage`),
    qué valores trae de verdad `usage` en las claves de caché, y si
    `allowed_tools=[]` permitió invocar alguna herramienta. Imprimir el
    tipo de *todos* los mensajes (no solo esos dos) es donde aparecería un
    `RateLimitEvent` real si el CLI lo emitiera: el paquete instalado
    exporta `RateLimitEvent`/`RateLimitInfo` con `status`, `utilization` y
    `resets_at` — `utilization` es justo lo que T60 manda leer a mano en
    Settings > Usage, así que verlo una vez de cerca tiene valor, aunque
    una sola llamada de humo no tenga por qué llegar a generar uno.

Una sola llamada real a Claude en todo el test: el volcado de (e) se
consigue interponiendo un "espía" transparente delante de
`agent_sdk_provider.query` (reenvía cada mensaje tal cual, solo los
colecciona de paso) en vez de repetir la llamada con `query()` a mano. El
espía propaga el cierre del generador real (`aclose()` en un `finally`):
si quien consume el espía lo cierra antes de agotarlo, el generador de
`query()` real debe cerrarse con él, o dejaría un subproceso `claude` del
CLI vivo de fondo.

Presupuesto de esta llamada: se espera que quede por debajo de 5 000
tokens (menos del 2% de una noche de 300 000) gracias a un prompt de una
línea, `max_turns=1` y una palabra de salida esperada — pero eso acota el
lado de entrada por diseño, no el de salida: nada impide que el modelo
decida explicarse de más en el turno de respuesta. El `assert` de más
abajo comprueba esa expectativa a posteriori, sobre el gasto ya incurrido;
no es una garantía que este test imponga de antemano.

**Corrección T42 (el humo manual de T40 había fallado, mal planteado).** El
assert original comprobaba `result.tokens_in` -- el máximo entre `usage` y
la suma de **todo** `model_usage` (ver `_extract_tokens` en
`agent_sdk_provider.py`) -- para afirmar algo mucho más estrecho: que no se
estaba cargando contexto de proyecto pese a `setting_sources=[]`. Esa cifra
mezcla el modelo pedido con el preámbulo de control de sesión del CLI
(Haiku, "gasto lateral" documentado en `agent_sdk_provider.py`), que no
controlamos y que se mueve con cualquier actualización del binario `claude`:
entre dos ejecuciones reales, sin que cambiara ni una línea de prompt, el
Haiku pasó de 929 a 1.163 tokens de entrada y tumbó un umbral que solo tenía
un 3% de margen (1.500 sobre 1.452 observados). Ahora el assert que afirma
"no hay fuga de contexto" aísla `usage` del último `ResultMessage` -- que, a
diferencia de `model_usage`, solo reporta el modelo pedido en
`AgentRequest`, confirmado en el docstring de `agent_sdk_provider.py` -- así
que el ruido de Haiku ya no puede tumbarlo ni ocultar una fuga real. El
volcado añade las versiones instaladas del CLI y del SDK y el gasto de
Haiku por separado, para que la próxima desviación de este tipo se lea de
inmediato en vez de reconstruirse desde cero (como hubo que hacer esta vez)
y para que T60 sepa contra qué versión se midió cada noche.
"""

import os
import subprocess
from datetime import UTC, datetime, time
from time import monotonic

import claude_agent_sdk
import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage
from fakes.clock import FakeClock

from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.domain.entities import AgentCall, AgentCallStatus, Run
from nocturna.domain.errors import LLMError, LLMTimeout
from nocturna.domain.llm import AgentRequest, AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
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
    "del entorno y repite con: env -u ANTHROPIC_API_KEY uv run pytest -m manual -s"
)

# Dentro de la ventana real de ejecución (00:00-04:45, ver config/pipeline.toml
# y CLAUDE.md), para que el test se pueda lanzar a cualquier hora del día real
# sin que `BudgetGuard` lo rechace por `OUTSIDE_WINDOW`.
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_WINDOW_START = time(0, 0)
_WINDOW_HARD_STOP = time(4, 45)

# Presupuesto del `Run`/`BudgetPolicy` de este test: un valor de conveniencia
# para que `BudgetGuard.authorize` autorice de sobra una llamada de menos de
# 5 000 tokens, no una cifra que limite el gasto real (eso ya lo hace el
# prompt mínimo y `max_turns=1`).
_TEST_RUN_BUDGET_TOKENS = 50_000
_TEST_EDITOR_RESERVE_TOKENS = 1_000
_ESTIMATED_TOKENS = 2_000

# Umbral del assert que afirma "no hay fuga de contexto de proyecto pese a
# setting_sources=[]" (ver 'Corrección T42' en el docstring del módulo). Deja
# ~2x de margen sobre los 523 tokens de entrada que el modelo pedido (Sonnet)
# gastó la primera vez que se corrió este test con un prompt de una línea y
# sin `system_prompt` propio -- lejos de los miles que aportaría cargar
# CLAUDE.md/docs de verdad, y sin depender del preámbulo de Haiku (que no
# cuenta para esta cifra, ver `_requested_model_tokens_in`).
_REQUESTED_MODEL_TOKENS_IN_THRESHOLD = 1_000


def _requested_model_tokens_in(usage: dict[str, object] | None) -> int:
    """Tokens de entrada del modelo PEDIDO en `AgentRequest`, aislados del
    preámbulo de control de sesión del CLI (Haiku).

    A diferencia de `AgentResult.tokens_in` (que toma el máximo entre
    `usage` y la suma de **todo** `model_usage`, ver `_extract_tokens` en
    `agent_sdk_provider.py`) o de sumar `model_usage` a mano, `usage` -- el
    objeto de nivel superior de `ResultMessage` -- solo reporta el modelo
    que pedimos: confirmado en el docstring de `agent_sdk_provider.py`
    ("usage solo reporta el modelo pedido"). Es la única cifra de las tres
    que no se mueve si el CLI cambia cuánto gasta Haiku por su cuenta, así
    que es la única que sirve para afirmar "no se está cargando contexto de
    proyecto".

    Misma fórmula que `_tokens_from_usage` del proveedor (la caché cuenta
    como entrada); reimplementada aquí a propósito en vez de importada: este
    fichero no debe depender de símbolos privados de `infrastructure/llm`.
    """
    if not usage:
        return 0
    return (
        int(usage.get("input_tokens") or 0)
        + int(usage.get("cache_creation_input_tokens") or 0)
        + int(usage.get("cache_read_input_tokens") or 0)
    )


def _haiku_tokens_in(model_usage: dict[str, dict[str, object]] | None) -> int | None:
    """Suma de `tokens_in` de las entradas de `model_usage` cuyo nombre de
    modelo contiene "haiku" -- el preámbulo de control de sesión del CLI que
    T40 encontró y que T42 tuvo que reconstruir a mano tras un fallo confuso
    (ver 'Corrección T42' en el docstring del módulo). `None` si no hay
    `model_usage` o ninguna entrada de Haiku en él, para distinguir "no hay
    desglose" de "el desglose no incluye Haiku esta vez"."""
    if not model_usage:
        return None
    total = 0
    found = False
    for name, entry in model_usage.items():
        if "haiku" not in name.lower():
            continue
        found = True
        total += int(entry.get("inputTokens") or 0)
        total += int(entry.get("cacheCreationInputTokens") or 0)
        total += int(entry.get("cacheReadInputTokens") or 0)
    return total if found else None


def _cli_version() -> str:
    """Versión instalada del binario `claude`, para que el volcado de humo
    diga contra qué versión se midió el gasto (T60 la necesita para
    interpretar desviaciones como la de 'Corrección T42' sin reconstruirlas a
    mano). Invoca el binario local con `--version`: no es tráfico hacia
    Claude, no consume presupuesto ni cuenta como llamada a un agente (mismo
    binario que arranca `query()`, pero sin sesión ni prompt)."""
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
        nightly_tokens=_TEST_RUN_BUDGET_TOKENS,
        editor_reserve_tokens=_TEST_EDITOR_RESERVE_TOKENS,
        max_items_per_night=1,
        max_turns_per_agent=1,
        max_editor_calls_per_night=1,
        max_calls_per_item=1,
        # `item_timeout_s=30` es cómodo para un test manual (una palabra de
        # salida no necesita más); el timeout real que se pasa a la llamada
        # no sale de este literal, sale de `BudgetGuard.timeout_for_call()`
        # aplicado sobre esta política, ver (b) en el docstring del módulo.
        item_timeout_s=30,
        run_timeout_s=60,
        window_start=_WINDOW_START,
        window_hard_stop=_WINDOW_HARD_STOP,
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _build_guard(session, run_id, policy: BudgetPolicy) -> BudgetGuard:
    return BudgetGuard(
        run_id=run_id,
        policy=policy,
        runs=SqlAlchemyRunRepository(session),
        agent_calls=SqlAlchemyAgentCallRepository(session),
        clock=FakeClock(_WITHIN_WINDOW),  # (d) reloj fijo, dentro de ventana
    )


@pytest.mark.anyio
async def test_smoke_llamada_real_autorizada_ejecutada_y_contabilizada_en_budget_guard(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
) -> None:
    # --- (a) ANTHROPIC_API_KEY ausente del entorno ------------------------
    # No se salta con `pytest.skip`: correr este test con una API key en el
    # entorno facturaría por el canal equivocado, que es exactamente lo que
    # CLAUDE.md prohíbe. Se falla con el remedio explícito.
    if "ANTHROPIC_API_KEY" in os.environ:
        pytest.fail(_NO_API_KEY_REMEDY)

    # --- espía transparente delante de `query()`, para (e) -----------------
    # Reenvía cada mensaje del `query()` real tal cual, solo los colecciona
    # de paso: no es una segunda llamada, es la misma llamada observada.
    # Propaga el cierre (`aclose`) al generador real: si quien consume este
    # espía lo cierra sin agotarlo, el `query()` real debe cerrarse con él.
    captured_messages: list[object] = []
    real_query = agent_sdk_provider.query

    async def _spying_query(*args: object, **kwargs: object):
        inner = real_query(*args, **kwargs)
        try:
            async for message in inner:
                captured_messages.append(message)
                yield message
        finally:
            await inner.aclose()

    monkeypatch.setattr(agent_sdk_provider, "query", _spying_query, raising=True)

    # --- (c.1) crea el Run -------------------------------------------------
    with unit_of_work(db_session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        run = Run(started_at=_WITHIN_WINDOW, budget_tokens=_TEST_RUN_BUDGET_TOKENS)
        runs.add(run)
        session.flush()
        run_id = run.id

    policy = _test_policy()

    # --- (c.2) `authorize` ANTES de la llamada, en su propia unidad --------
    # Si deniega, lanza y no se llama a Claude. `timeout_for_call()` no toca
    # ningún repositorio (es puro sobre el reloj y la política), así que
    # leerlo aquí, dentro de la misma unidad que hizo `authorize`, no amplía
    # la transacción más allá de lo que ya hacía `authorize` en solitario.
    with unit_of_work(db_session_factory) as session:
        guard = _build_guard(session, run_id, policy)
        guard.authorize(AgentRole.READER, _ESTIMATED_TOKENS)
        timeout_s = guard.timeout_for_call()

    # --- (b) una llamada mínima real, nada hardcodeado que deba venir de
    # configuración o de la política de gasto ------------------------------
    config = load_pipeline_config()
    request = AgentRequest(
        role=AgentRole.READER,
        model=config.models.reader,
        prompt=(
            "Responde con una sola palabra en minúsculas, sin explicación ni "
            "puntuación: el nombre de la constelación que contiene la estrella "
            "polar."
        ),
        max_turns=policy.max_turns_per_agent,
        timeout_s=timeout_s,
    )
    provider = AgentSDKProvider()

    # --- la llamada real, FUERA de cualquier transacción --------------------
    # Ninguna conexión de PostgreSQL permanece abierta mientras se espera al
    # LLM (hasta `timeout_s`): `authorize` ya cerró su unidad de trabajo, y
    # `record_call` abre la suya propia más abajo, después de que esta
    # llamada termine (con éxito o con excepción).
    started_at = monotonic()
    try:
        result = await provider.run_agent(request)
    except LLMError as exc:
        # (c) Camino de fallo: los tokens que trae la excepción son gasto ya
        # incurrido (`domain/errors.py`) y deben contabilizarse igual que un
        # éxito, en su propia `unit_of_work` — el patrón que T41 hereda.
        duration_ms = int((monotonic() - started_at) * 1000)
        call = AgentCall(
            run_id=run_id,
            item_id=None,
            agent=AgentRole.READER,
            model=request.model,
            tokens_in=exc.tokens_in,
            tokens_out=exc.tokens_out,
            duration_ms=duration_ms,
            status=AgentCallStatus.TIMEOUT
            if isinstance(exc, LLMTimeout)
            else AgentCallStatus.ERROR,
        )
        with unit_of_work(db_session_factory) as session:
            guard = _build_guard(session, run_id, policy)
            current_run = SqlAlchemyRunRepository(session).get(run_id)
            assert current_run is not None
            guard.record_call(call, current_run)

        with db_session_factory() as check_session:
            agent_calls = SqlAlchemyAgentCallRepository(check_session)
            assert agent_calls.tokens_used_for_run(run_id) == call.tokens_in + call.tokens_out

        pytest.fail(
            f"la llamada real a '{request.role.value}' falló ({exc!r}), pero el gasto "
            f"conocido (tokens_in={exc.tokens_in}, tokens_out={exc.tokens_out}) quedó "
            "contabilizado en AgentCall antes de fallar este test."
        )

    # --- (c) camino de éxito: registrar en una unidad de trabajo nueva -----
    duration_ms = int((monotonic() - started_at) * 1000)

    assert result.output_text.strip() != ""
    assert result.tokens_in > 0
    assert result.tokens_out > 0
    assert result.duration_ms > 0
    assert result.model == request.model

    # Mensajes capturados por el espía, calculados ya aquí (no solo en el
    # volcado de más abajo): los dos asserts que siguen los necesitan para
    # aislar el gasto del modelo pedido del preámbulo de Haiku (ver
    # 'Corrección T42' en el docstring del módulo).
    result_messages = [m for m in captured_messages if isinstance(m, ResultMessage)]
    assistant_messages = [m for m in captured_messages if isinstance(m, AssistantMessage)]
    last_result = result_messages[-1] if result_messages else None

    requested_model_tokens_in = _requested_model_tokens_in(
        last_result.usage if last_result is not None else None
    )
    haiku_tokens_in = _haiku_tokens_in(last_result.model_usage if last_result is not None else None)

    # Acota el lado de entrada del MODELO PEDIDO, no el total: un prompt de
    # una línea sin `system_prompt` propio no debería inflar esto salvo que
    # `setting_sources=[]` no esté evitando cargar contexto de proyecto (ver
    # `build_options`). A diferencia del assert original (T40), esta cifra no
    # incluye el preámbulo de control de sesión del CLI (Haiku): un cambio de
    # versión del CLI no puede tumbar este assert por su cuenta.
    assert requested_model_tokens_in < _REQUESTED_MODEL_TOKENS_IN_THRESHOLD, (
        f"tokens_in del modelo pedido ({request.model})={requested_model_tokens_in} es alto "
        "para un prompt de una línea sin system_prompt propio -- esta cifra viene de "
        "'usage', que solo reporta el modelo pedido (a diferencia de result.tokens_in, que "
        "mezcla esto con el gasto de Haiku): SE ESTÁ CARGANDO CONTEXTO DE PROYECTO pese a "
        "setting_sources=[], revisa build_options()."
    )
    assert result.total_tokens < 5_000, (
        "se esperaba que la llamada de este test quedara por debajo de 5 000 tokens (ver "
        f"docstring del módulo); gastó {result.total_tokens} en total (modelo pedido="
        f"{requested_model_tokens_in}, Haiku="
        f"{haiku_tokens_in if haiku_tokens_in is not None else '(no reportado)'}, salida="
        f"{result.tokens_out}). El assert de arriba (tokens_in del modelo pedido) ya pasó, "
        "así que esto NO es una fuga de contexto: es que EL PREÁMBULO DEL CLI HA CAMBIADO "
        "(la versión de 'claude --version' del volcado de más abajo) -- compara "
        "haiku_tokens_in contra el histórico del docstring de agent_sdk_provider.py (929, "
        "luego 1.163 en la versión que tumbó este test la vez anterior) y decide si hay que "
        "actualizar ese hallazgo, no este umbral sin más."
    )

    call = AgentCall(
        run_id=run_id,
        item_id=None,
        agent=AgentRole.READER,
        model=result.model,
        tokens_in=result.tokens_in,
        tokens_out=result.tokens_out,
        duration_ms=result.duration_ms,
        status=AgentCallStatus.OK,
    )
    with unit_of_work(db_session_factory) as session:
        guard = _build_guard(session, run_id, policy)
        current_run = SqlAlchemyRunRepository(session).get(run_id)
        assert current_run is not None
        guard.record_call(call, current_run)

    # Sesión nueva, sin relación con la que hizo el commit: confirma que el
    # acumulado sobrevive fuera de la unidad de trabajo que lo escribió.
    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        assert agent_calls.tokens_used_for_run(run_id) == result.total_tokens

    # --- (e) volcado para las decisiones abiertas ---------------------------
    # Versiones primero: es lo que hubiera hecho legible de inmediato la
    # desviación que motivó 'Corrección T42' en vez de un test rojo confuso
    # (ver docstring del módulo), y T60 necesita saber contra qué versión se
    # midió cada noche.
    cli_version = _cli_version()
    print(f"\n--- versión del CLI 'claude' --- {cli_version}")
    print(f"--- versión de claude_agent_sdk --- {claude_agent_sdk.__version__}")
    print(
        "\n--- desglose: modelo pedido vs. preámbulo del CLI (Haiku) ---\n"
        f"tokens_in del modelo pedido ({request.model})={requested_model_tokens_in}\n"
        "tokens_in de Haiku (gasto lateral, coste fijo de sesión)="
        f"{haiku_tokens_in if haiku_tokens_in is not None else '(no reportado)'}"
    )

    print("\n--- (e.0) tipo de cada mensaje capturado durante la llamada ---")
    for message in captured_messages:
        print(type(message).__name__)
    if not captured_messages:
        print("(no se capturó ningún mensaje; algo va mal si esto se lee)")

    print(
        "\n--- (e.1) ResultMessage completo -- revisa si hay un campo 'model_usage' "
        "(dict[str, ModelUsage] | None) y qué valores traen de verdad las claves de "
        "caché de 'usage' (cache_creation_input_tokens / cache_read_input_tokens) ---"
    )
    for message in result_messages:
        print(repr(message))
    if not result_messages:
        print("(no se recibió ningún ResultMessage; algo va mal si esto se lee)")

    print(
        "\n--- (e.2) AssistantMessage completo -- revisa si aparece algún "
        "ToolUseBlock: con allowed_tools=[] no debería poder haber invocado "
        "ninguna herramienta (Read, Bash, ...) ---"
    )
    if assistant_messages:
        print(repr(assistant_messages[0]))
    else:
        print("(el SDK no emitió ningún AssistantMessage en esta llamada)")

    print(
        f"\n--- resumen: run_id={run_id} total_tokens={result.total_tokens} "
        f"modelo_pedido_tokens_in={requested_model_tokens_in} haiku_tokens_in="
        f"{haiku_tokens_in if haiku_tokens_in is not None else '(no reportado)'} "
        f"cli={cli_version} sdk={claude_agent_sdk.__version__} ---"
    )
