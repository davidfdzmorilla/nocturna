"""`AgentSDKProvider.run_agent`: caminos de fallo del transporte, no del
contenido de la respuesta (eso vive en `test_agent_sdk_usage.py`).

El test de cancelación es, en la práctica, un test de control de gasto
disfrazado: `AgentSDKProvider` nunca debe traducir un `asyncio.CancelledError`
externo a `LLMTimeout`. Es justo la distinción que necesita T44 para separar
"cortó `hard_stop`" (el orquestador cancela la tarea desde fuera, el Run se
marca `killed`) de "el modelo tardó más de `request.timeout_s`" (este método
marca el ítem `timeout` y la noche sigue). Si `CancelledError` se colase como
`LLMTimeout`, un corte de `hard_stop` a mitad de un ítem se registraría como
un simple timeout de ese ítem y la noche seguiría intentando llamadas después
de las 04:45.
"""

import asyncio
import logging

import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKError,
    ProcessError,
    RateLimitEvent,
    RateLimitInfo,
    ResultError,
    ResultMessage,
)
from helpers.sdk_doubles import (
    build_fake_query,
    build_fake_query_with_unreliable_aclose,
    make_result_message,
)

from nocturna.domain.errors import LLMAuthenticationFailed, LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRequest, AgentRole
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider, _llm_error_class


def _request(**overrides: object) -> AgentRequest:
    defaults: dict[str, object] = dict(
        role=AgentRole.READER,
        model="claude-sonnet-4-5",
        prompt="lee este abstract",
        max_turns=3,
        timeout_s=5,
    )
    defaults.update(overrides)
    return AgentRequest(**defaults)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_query_que_no_responde_a_tiempo_lanza_llm_timeout_y_cierra_el_iterador(
    monkeypatch,
):
    closed_flag: list[bool] = []
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(sleep_before_s=10, closed_flag=closed_flag),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMTimeout):
        await provider.run_agent(_request(timeout_s=0))

    assert closed_flag == [True]


@pytest.mark.anyio
async def test_timeout_tras_result_message_con_model_usage_no_se_pierde_el_gasto_de_otros_modelos(
    monkeypatch,
):
    """Ancla en `model_usage` la rama `except TimeoutError` de
    `_extract_tokens` -- mutación superviviente de la revisión de T40: es
    el final rutinario de un ítem lento con `item_timeout_s=180`, donde
    perder tokens es lo normal, no lo excepcional. El generador emite un
    `ResultMessage` con `usage` y `model_usage` poblados y luego se queda
    dormido más allá del deadline propio (`timeout_s=0.05`), forzando el
    `TimeoutError` real de `asyncio.timeout` (`cm.expired() is True`), no
    uno ajeno. El gasto de `LLMTimeout` debe ser 1452/23 (Haiku + Sonnet
    del hallazgo real de T40), no solo 523/6 del `usage` del Sonnet
    pedido.
    """
    success_result = make_result_message(
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={
            "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
            "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(success_result,), sleep_after_s=10),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMTimeout) as excinfo:
        await provider.run_agent(_request(timeout_s=0.05))

    assert excinfo.value.tokens_in == 1452
    assert excinfo.value.tokens_out == 23


@pytest.mark.anyio
async def test_timeout_error_ajeno_al_deadline_propio_se_traduce_a_llm_error_no_a_llm_timeout(
    monkeypatch,
):
    """`TimeoutError` desde Python 3.11 comparte clase con el que levanta
    `asyncio.timeout(request.timeout_s)` al expirar, pero no toda
    `TimeoutError` capturada dentro de ese bloque viene del deadline
    propio: un timeout de socket o del sistema operativo, colándose por el
    generador de `query()` antes de que el deadline expire, es la misma
    clase con una causa completamente distinta. Antes de esta corrección,
    el docstring de este módulo daba por inevitable la confusión ("no hay
    forma de distinguirlos aquí sin acoplarse a mecanismos internos de
    `asyncio.timeout`"); es falso: `cm.expired()` (`asyncio.Timeout`, API
    pública desde 3.11, no un mecanismo interno) los distingue con
    fiabilidad. Un `TimeoutError` con `cm.expired() is False` no puede
    traducirse a `LLMTimeout` -- la suscripción no perdió
    `request.timeout_s` segundos esperando al agente, algo ajeno rompió el
    transporte -- así que se trata como cualquier otra excepción no
    reconocida: `LLMError`, con los tokens del último `ResultMessage` visto
    si lo hubo. Sin la distinción, un fallo de red del SO se reportaría
    como "el agente no respondió en Ns" y el ítem quedaría `timeout` (la
    noche sigue intentando) en vez de `failed` -- la clase de mentira en un
    mensaje de error que cuesta una hora de depuración a las 3 de la
    mañana, porque señala un problema que no existe (el modelo no tardó
    nada) en vez del que existe de verdad (el transporte falló).
    """
    success_result = make_result_message(usage={"input_tokens": 50, "output_tokens": 5})
    original = TimeoutError("el socket subyacente agotó su propio timeout, no el deadline")
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(success_result,), raise_after=original),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request(timeout_s=3600))

    assert type(excinfo.value) is LLMError
    assert not isinstance(excinfo.value, LLMTimeout)
    assert excinfo.value.__cause__ is original
    assert excinfo.value.tokens_in == 50
    assert excinfo.value.tokens_out == 5


@pytest.mark.anyio
async def test_cancelled_error_externo_se_propaga_tal_cual_nunca_como_llm_timeout(monkeypatch):
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(sleep_before_s=10),
        raising=True,
    )
    provider = AgentSDKProvider()

    task = asyncio.ensure_future(provider.run_agent(_request(timeout_s=3600)))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_excepcion_del_sdk_se_traduce_a_llm_error_con_cause_encadenada(monkeypatch):
    original = ClaudeSDKError("el CLI de claude terminó con un error de proceso")
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(raise_after=original),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.__cause__ is original


@pytest.mark.anyio
async def test_claude_sdk_error_tras_un_result_message_de_exito_propaga_sus_tokens(monkeypatch):
    """Si el generador ya emitió un `ResultMessage` de éxito con `usage` y
    LUEGO lanza un `ClaudeSDKError` genérico (no `ResultError`, que tiene su
    propio camino ya cubierto arriba), esos tokens no pueden perderse: la
    suscripción ya los pagó. Antes de esta corrección, la rama `except
    ClaudeSDKError` no miraba `last_result.usage` y la `LLMError` salía con
    tokens a cero.
    """
    success_result = make_result_message(usage={"input_tokens": 900, "output_tokens": 120})
    original = ClaudeSDKError("el CLI se cayó después de responder")
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(success_result,), raise_after=original),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 900
    assert excinfo.value.tokens_out == 120


@pytest.mark.anyio
async def test_claude_sdk_error_tras_result_message_con_model_usage_no_pierde_gasto_extra(
    monkeypatch,
):
    """Ancla en `model_usage` la rama `except ClaudeSDKError` de
    `_extract_tokens` -- mutación superviviente de la revisión de T40.
    Gemelo de
    `test_claude_sdk_error_tras_un_result_message_de_exito_propaga_sus_tokens`
    con `model_usage` poblado además de `usage`: el resultado debe ser
    1452/23 (Haiku + Sonnet del hallazgo real de T40), no solo 523/6 del
    `usage` del Sonnet pedido.
    """
    success_result = make_result_message(
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={
            "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
            "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
        },
    )
    original = ClaudeSDKError("el CLI se cayó después de responder")
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(success_result,), raise_after=original),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 1452
    assert excinfo.value.tokens_out == 23


@pytest.mark.anyio
async def test_aclose_que_falla_con_error_previo_en_vuelo_no_sustituye_al_original(monkeypatch):
    """Con un generador async real, `aclose()` nunca puede fallar de verdad:
    en todo camino por el que `run_agent` sale de su `async for` (agotamiento
    normal, `raise_after`, o una cancelación) el generador ya está cerrado
    cuando `run_agent` llega a su `finally`, y `aclose()` sobre un generador
    ya cerrado es un no-op garantizado por el lenguaje (ver docstring de
    `helpers/sdk_doubles.build_fake_query_with_unreliable_aclose`). La rama
    `except Exception as close_exc` de `run_agent` es, por tanto, código
    muerto defensivo en producción -- pero barato de mantener y correcto si
    algún transporte no estándar del SDK rompiera esa garantía algún día.
    Este test la ejercita igualmente con el doble de `aclose()` forzado a
    fallar: si ya había una `LLMError`/`LLMTimeout` en curso, ese fallo de
    cierre se descarta (se registra con `logging`) para no perder la
    excepción original ni los tokens que transporta -- el mismo fallo de
    forma que T30 corrigió en `BudgetGuard.record_call`.
    """
    original = ClaudeSDKError("el CLI de claude terminó con un error de proceso")
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query_with_unreliable_aclose(
            raise_after=original,
            aclose_raises=RuntimeError("aclose() fallo limpiando el subproceso"),
        ),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.__cause__ is original


@pytest.mark.anyio
async def test_aclose_que_falla_sin_error_previo_se_propaga_porque_es_la_unica_senal(
    monkeypatch,
):
    """Sin ninguna excepción de dominio en curso (el `query()` termina
    limpio, con un `ResultMessage` de éxito), un fallo de `aclose()` es la
    única señal de que algo fue mal: debe propagarse tal cual, no
    descartarse.
    """
    success_result = make_result_message(usage={"input_tokens": 10, "output_tokens": 5})
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query_with_unreliable_aclose(
            messages=(success_result,),
            aclose_raises=RuntimeError("aclose() fallo limpiando el subproceso"),
        ),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(RuntimeError, match="aclose"):
        await provider.run_agent(_request())


@pytest.mark.anyio
async def test_cancelled_error_de_aclose_gana_sobre_llm_timeout_en_vuelo(monkeypatch):
    """Si `aclose()` levanta `CancelledError` mientras ya había una
    `LLMTimeout` en curso, gana el `CancelledError` -- nunca se relanza ni
    se descarta como un fallo de cierre cualquiera. Es control de gasto: si
    la cancelación se pierde o se enmascara aquí, el corte duro de
    `hard_stop` (T44) deja de propagarse por este camino y el pipeline
    puede abrir una segunda sesión de cinco horas a las 05:00.
    """
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query_with_unreliable_aclose(
            sleep_before_s=10,
            aclose_raises=asyncio.CancelledError(),
        ),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(asyncio.CancelledError):
        await provider.run_agent(_request(timeout_s=0))


@pytest.mark.anyio
async def test_cancelled_error_externo_no_se_pierde_si_aclose_falla_con_excepcion_corriente(
    monkeypatch,
):
    """Sonda de mutación (revisión de T40): borrar `in_flight_exception = True`
    de la rama `except asyncio.CancelledError` de `run_agent` deja la suite
    entera en verde sin este test -- no es un mutante equivalente. Con la
    línea presente, una cancelación externa en vuelo marca
    `in_flight_exception = True` antes de llegar al `finally`; si `aclose()`
    falla ahí con una excepción corriente (no `CancelledError`), la rama
    `except Exception as close_exc` la ve en `True` y descarta el fallo de
    cierre para no sustituir al `CancelledError` original -- exactamente el
    caso de `test_aclose_que_falla_con_error_previo_en_vuelo_no_sustituye_al_original`
    de arriba, pero para la rama de cancelación en vez de la de
    `ClaudeSDKError`. Sin la línea, `in_flight_exception` se queda en
    `False` y esa misma rama toma el camino `else: raise`, sustituyendo el
    `CancelledError` por la excepción de `aclose()` -- justo el corte de
    `hard_stop` que T44 necesita que sobreviva a este método sin perderse
    ni enmascararse.
    """
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query_with_unreliable_aclose(
            sleep_before_s=10,
            aclose_raises=RuntimeError("aclose() fallo limpiando el subproceso"),
        ),
        raising=True,
    )
    provider = AgentSDKProvider()

    task = asyncio.ensure_future(provider.run_agent(_request(timeout_s=3600)))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.anyio
async def test_aclose_que_falla_en_camino_de_exito_se_propaga_aunque_el_llamador_este_en_un_except(
    monkeypatch,
):
    """Reproduce el bug real de `sys.exc_info()` (ciclo de corrección de
    T40): antes de la corrección, `run_agent` decidía si había "una
    excepción en vuelo" leyendo `sys.exc_info()`, que refleja el estado de
    excepción del *hilo*, no el de este método. T41 escribirá el reintento
    de JSON inválido dentro del `except` del intento anterior -- exactamente
    la forma en que este test invoca `run_agent`, anidado dentro de un
    `except ValueError` externo -- así que `sys.exc_info()` no era `None`
    aunque el `query()` de este `run_agent` terminara limpio, con un
    `ResultMessage` de éxito. Con el bug, ese camino de éxito se confundía
    con un camino de error y el fallo real de `aclose()` se descartaba en
    silencio; con la corrección (una bandera local puesta solo en los
    `except` propios de `run_agent`), el `RuntimeError` de `aclose()` sigue
    siendo la única señal de que algo fue mal y debe propagarse.
    """
    success_result = make_result_message(usage={"input_tokens": 10, "output_tokens": 5})
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query_with_unreliable_aclose(
            messages=(success_result,),
            aclose_raises=RuntimeError("aclose() fallo limpiando el subproceso"),
        ),
        raising=True,
    )
    provider = AgentSDKProvider()

    try:
        raise ValueError("fallo de un intento anterior, ya manejado por el llamador")
    except ValueError:
        with pytest.raises(RuntimeError, match="aclose"):
            await provider.run_agent(_request())


@pytest.mark.anyio
async def test_result_error_expone_api_error_status_como_atributo_no_solo_en_el_texto(
    monkeypatch,
):
    """`LLMError.api_error_status` viene de `ResultError.api_error_status`
    cuando el CLI lo informa. Se comprueba como atributo, no parseando
    `str(exc)`: T44 necesita poder escribir `exc.api_error_status == 529`
    para distinguir un fallo transitorio del servidor de otros fallos.
    """

    result_error = ResultError(
        "el servicio está sobrecargado",
        data={"api_error_status": 529, "usage": {"input_tokens": 30, "output_tokens": 2}},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(raise_after=result_error), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.api_error_status == 529


@pytest.mark.anyio
async def test_excepcion_pelada_del_sdk_tras_result_message_de_exito_se_traduce_con_tokens(
    monkeypatch,
):
    """El paquete instalado (`claude-agent-sdk` 0.2.153) lanza `Exception`
    pelados en varios puntos de `_internal/query.py` que no heredan de
    `ClaudeSDKError` (ver docstring de `AgentSDKProvider.run_agent`, rama
    catch-all). Sin la rama `except Exception`, esto escaparía sin traducir
    a `LLMError` y, con un `ResultMessage` de éxito ya visto, esos tokens se
    perderían.
    """
    success_result = make_result_message(usage={"input_tokens": 60, "output_tokens": 7})
    original = RuntimeError("{'type': 'error'} del CLI que el SDK no traduce")
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(success_result,), raise_after=original),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 60
    assert excinfo.value.tokens_out == 7
    assert excinfo.value.api_error_status is None
    assert excinfo.value.__cause__ is original


@pytest.mark.anyio
async def test_excepcion_pelada_tras_result_message_con_model_usage_no_pierde_gasto_extra(
    monkeypatch,
):
    """Ancla en `model_usage` la rama catch-all `except Exception` de
    `_extract_tokens` -- mutación superviviente de la revisión de T40.
    Gemelo de
    `test_excepcion_pelada_del_sdk_tras_result_message_de_exito_se_traduce_con_tokens`
    con `model_usage` poblado además de `usage`: el resultado debe ser
    1452/23 (Haiku + Sonnet del hallazgo real de T40), no solo 523/6 del
    `usage` del Sonnet pedido.
    """
    success_result = make_result_message(
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={
            "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
            "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
        },
    )
    original = RuntimeError("{'type': 'error'} del CLI que el SDK no traduce")
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(success_result,), raise_after=original),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 1452
    assert excinfo.value.tokens_out == 23


@pytest.mark.anyio
async def test_excepcion_pelada_del_sdk_sin_result_message_previo_se_traduce_con_tokens_a_cero(
    monkeypatch,
):
    """Misma rama catch-all que el test anterior, pero sin ningún
    `ResultMessage` visto antes de la excepción pelada: no hay `usage` del
    que tirar, así que los tokens van a cero en vez de perder la excepción.
    """
    original = RuntimeError("{'type': 'error'} del CLI que el SDK no traduce")
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(raise_after=original), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 0
    assert excinfo.value.tokens_out == 0
    assert excinfo.value.__cause__ is original


@pytest.mark.anyio
async def test_cancelled_error_externo_lleva_los_tokens_del_result_message_ya_visto(monkeypatch):
    """Control de gasto: si `hard_stop` cancela `run_agent` después de que
    el `query()` ya emitiera un `ResultMessage` con `usage` (pero antes de
    que el generador termine), ese gasto no puede perderse -- viaja como
    `tokens_in`/`tokens_out` sobre la misma instancia de `CancelledError`
    que se relanza, que sigue siendo exactamente esa: no se traduce a
    `LLMError` ni a ningún otro tipo.
    """
    success_result = make_result_message(usage={"input_tokens": 400, "output_tokens": 90})
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(success_result,), sleep_after_s=10),
        raising=True,
    )
    provider = AgentSDKProvider()

    task = asyncio.ensure_future(provider.run_agent(_request(timeout_s=3600)))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await task

    assert type(excinfo.value) is asyncio.CancelledError
    assert excinfo.value.tokens_in == 400
    assert excinfo.value.tokens_out == 90


@pytest.mark.anyio
async def test_result_error_sin_result_message_recupera_model_usage_del_payload(monkeypatch):
    """Gemelo de `test_usage_ausente_en_result_message_se_recupera_del_payload_de_result_error`
    (`test_agent_sdk_usage.py`), pero para `model_usage`: el `query()`
    termina sin emitir ningún `ResultMessage` (`last_result is None`) y
    lanza directamente una `ResultError` cuyo payload trae `modelUsage`
    (las dos entradas del hallazgo real de T40: Haiku y Sonnet). La rama
    `except ResultError` debe extraer el gasto de `exc.data.get("modelUsage")`
    -- 929 + 523 = 1452 de entrada, 17 + 6 = 23 de salida -- sin que haga
    falta ningún `ResultMessage` visto en el stream.
    """

    result_error = ResultError(
        "el agente falló antes de emitir ningún ResultMessage",
        data={
            "modelUsage": {
                "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
                "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
            },
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(raise_after=result_error), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 1452
    assert excinfo.value.tokens_out == 23


@pytest.mark.anyio
async def test_cancelled_error_externo_lleva_los_tokens_de_model_usage_no_solo_de_usage(
    monkeypatch,
):
    """Gemelo de `test_cancelled_error_externo_lleva_los_tokens_del_result_message_ya_visto`,
    pero con `model_usage` poblado en el `ResultMessage` ya visto antes de
    la cancelación (las cifras del hallazgo real de T40): los atributos
    `tokens_in`/`tokens_out` que `run_agent` adjunta al `CancelledError`
    relanzado deben salir de `_extract_tokens` -- es decir, de la suma de
    `model_usage` (1452/23), no de `usage` solo (523/6) -- para que T44 no
    pierda el gasto de Haiku cuando `hard_stop` cancela a mitad de una
    llamada que ya emitió su `ResultMessage`.
    """
    success_result = make_result_message(
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={
            "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
            "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(success_result,), sleep_after_s=10),
        raising=True,
    )
    provider = AgentSDKProvider()

    task = asyncio.ensure_future(provider.run_agent(_request(timeout_s=3600)))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await task

    assert type(excinfo.value) is asyncio.CancelledError
    assert excinfo.value.tokens_in == 1452
    assert excinfo.value.tokens_out == 23


@pytest.mark.anyio
async def test_cancelled_error_externo_antes_de_cualquier_result_message_lleva_tokens_a_cero(
    monkeypatch,
):
    """Cancelación antes de que el `query()` emitiera ningún `ResultMessage`:
    no hay `usage` del que tirar, así que los atributos de gasto van a cero
    en vez de faltar (T44 puede leerlos sin comprobar `hasattr` primero).
    """
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(sleep_before_s=10),
        raising=True,
    )
    provider = AgentSDKProvider()

    task = asyncio.ensure_future(provider.run_agent(_request(timeout_s=3600)))
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError) as excinfo:
        await task

    assert excinfo.value.tokens_in == 0
    assert excinfo.value.tokens_out == 0


# --- _llm_error_class: elección de excepción según api_error_status --------


def test_llm_error_class_con_429_devuelve_llm_rate_limited():
    assert _llm_error_class(429) is LLMRateLimited


def test_llm_error_class_con_500_devuelve_llm_error_a_secas():
    assert _llm_error_class(500) is LLMError


def test_llm_error_class_con_none_devuelve_llm_error_a_secas():
    assert _llm_error_class(None) is LLMError


@pytest.mark.anyio
async def test_result_error_con_api_error_status_429_lanza_llm_rate_limited(monkeypatch):
    """Integración: un fallo de `ResultError` con `api_error_status=429` debe
    propagarse como `LLMRateLimited`, no como `LLMError` a secas -- para que
    T44 pueda distinguir un límite de tasa de cualquier otro fallo de API y
    cortar la noche sin reintentar.
    """

    result_error = ResultError(
        "límite de tasa alcanzado",
        data={"api_error_status": 429, "usage": {"input_tokens": 30, "output_tokens": 2}},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(raise_after=result_error), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMRateLimited) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.api_error_status == 429


@pytest.mark.anyio
async def test_llm_rate_limited_sigue_siendo_capturable_como_llm_error(monkeypatch):
    """`LLMRateLimited` es subclase de `LLMError`: el consumidor del paso 6
    (T41-siguiente) ordena sus `except` contando con que un `except LLMError`
    también atrape un límite de tasa, no solo `LLMError` a secas.
    """

    result_error = ResultError(
        "límite de tasa alcanzado",
        data={"api_error_status": 429, "usage": {"input_tokens": 30, "output_tokens": 2}},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(raise_after=result_error), raising=True
    )
    provider = AgentSDKProvider()

    try:
        await provider.run_agent(_request())
        pytest.fail("se esperaba que run_agent lanzara una excepción")
    except LLMError as exc:
        assert isinstance(exc, LLMRateLimited)


# --- T111: señales de límite/autenticación del SDK (ADR 0049) ---------------


def _assistant(error: str | None) -> AssistantMessage:
    return AssistantMessage(content=[], model="claude-sonnet-4-5", error=error)  # type: ignore[arg-type]


def _rate_limit_event(status: str, rate_limit_type: str | None = None) -> RateLimitEvent:
    return RateLimitEvent(
        rate_limit_info=RateLimitInfo(
            status=status,  # type: ignore[arg-type]
            resets_at=1_788_000_000,
            rate_limit_type=rate_limit_type,  # type: ignore[arg-type]
        ),
        uuid="uuid-de-test",
        session_id="session-de-test",
    )


def _result_429(**overrides: object) -> ResultMessage:
    kwargs: dict[str, object] = dict(
        is_error=True,
        api_error_status=429,
        result="You've hit your limit",
        usage={"input_tokens": 40, "output_tokens": 3},
    )
    kwargs.update(overrides)
    return make_result_message(**kwargs)  # type: ignore[arg-type]


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def provider_logs():
    """Handler propio sobre el logger del módulo: no depende de la config global."""
    logger = agent_sdk_provider._logger
    handler = _Capture()
    old_level = logger.level
    old_disabled = logger.disabled
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    # Otro test (p. ej. `fileConfig` de Alembic) puede haber dejado el logger
    # deshabilitado (`disable_existing_loggers`); se fuerza aquí.
    logger.disabled = False
    try:
        yield handler
    finally:
        logger.removeHandler(handler)
        logger.setLevel(old_level)
        logger.disabled = old_disabled


def _failure_records(handler: _Capture) -> list[logging.LogRecord]:
    return [r for r in handler.records if r.getMessage() == "llm.call_failed"]


def test_llm_error_class_con_assistant_error_rate_limit_devuelve_llm_rate_limited():
    assert _llm_error_class(None, "rate_limit") is LLMRateLimited
    assert _llm_error_class(None, "authentication_failed") is LLMAuthenticationFailed
    assert _llm_error_class(500, "server_error") is LLMError


@pytest.mark.anyio
async def test_caso_10_29_assistant_rate_limit_y_resultado_429_lanza_llm_rate_limited(monkeypatch):
    messages = (_assistant("rate_limit"), _result_429())
    original = ResultError("limite", data={"api_error_status": 429})
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=messages, raise_after=original),
        raising=True,
    )
    with pytest.raises(LLMRateLimited) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert excinfo.value.api_error_status == 429


@pytest.mark.anyio
async def test_resultado_429_seguido_de_process_error_lanza_llm_rate_limited(monkeypatch):
    original = ProcessError("Command failed", exit_code=1)
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(_result_429(),), raise_after=original),
        raising=True,
    )
    with pytest.raises(LLMRateLimited) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert excinfo.value.api_error_status == 429
    assert excinfo.value.__cause__ is original
    # los tokens ya gastados se conservan.
    assert excinfo.value.tokens_in == 40
    assert excinfo.value.tokens_out == 3


@pytest.mark.anyio
async def test_resultado_429_seguido_de_exception_pelada_lanza_llm_rate_limited(monkeypatch):
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(_result_429(),), raise_after=RuntimeError("otro mensaje")),
        raising=True,
    )
    with pytest.raises(LLMRateLimited) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert excinfo.value.api_error_status == 429


@pytest.mark.anyio
async def test_assistant_rate_limit_con_resultado_de_error_sin_status_lanza_llm_rate_limited(
    monkeypatch,
):
    messages = (_assistant("rate_limit"), _result_429(api_error_status=None))
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    with pytest.raises(LLMRateLimited) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert excinfo.value.api_error_status is None


@pytest.mark.anyio
async def test_caso_10_30_authentication_failed_lanza_llm_authentication_failed(monkeypatch):
    messages = (
        _assistant("authentication_failed"),
        make_result_message(is_error=True, result="Failed to authenticate"),
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    with pytest.raises(LLMAuthenticationFailed) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert type(excinfo.value) is LLMAuthenticationFailed


@pytest.mark.anyio
async def test_resultado_500_con_server_error_es_llm_error_a_secas(monkeypatch):
    messages = (_assistant("server_error"), _result_429(api_error_status=500))
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    with pytest.raises(LLMError) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert type(excinfo.value) is LLMError
    assert excinfo.value.api_error_status == 500


@pytest.mark.anyio
async def test_exito_tras_senales_de_limite_previas_devuelve_agent_result(
    monkeypatch, provider_logs
):
    messages = (
        _assistant("rate_limit"),
        _rate_limit_event("rejected", "seven_day"),
        make_result_message(usage={"input_tokens": 10, "output_tokens": 2}),
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    result = await AgentSDKProvider().run_agent(_request())
    assert result.output_text == "resultado del agente"
    assert (result.tokens_in, result.tokens_out) == (10, 2)
    # en éxito no hay llm.call_failed.
    assert _failure_records(provider_logs) == []


@pytest.mark.anyio
async def test_log_de_fallo_de_autenticacion_lleva_campos_y_no_contenido(
    monkeypatch, provider_logs
):
    messages = (
        _assistant("authentication_failed"),
        make_result_message(
            is_error=True,
            result="Failed to authenticate. API Error: 401",
            session_id="sesion-secreta",
        ),
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    with pytest.raises(LLMAuthenticationFailed):
        await AgentSDKProvider().run_agent(_request(prompt="PROMPT-CONFIDENCIAL"))

    records = _failure_records(provider_logs)
    assert len(records) == 1
    rec = records[0]
    assert rec.levelno == logging.WARNING
    assert rec.event == "llm.call_failed"
    assert rec.role == "reader"
    assert rec.item_id is None
    assert rec.error_class == "LLMAuthenticationFailed"
    assert rec.sdk_exception is None
    assert rec.subtype == "success"
    assert rec.is_error is True
    assert rec.api_error_status is None
    assert rec.assistant_error == "authentication_failed"
    assert rec.rate_limit_status is None
    assert rec.exit_code is None
    rendered = " ".join(f"{k}={v}" for k, v in vars(rec).items())
    assert "Failed to authenticate" not in rendered
    assert "PROMPT-CONFIDENCIAL" not in rendered
    assert "sesion-secreta" not in rendered
    assert not hasattr(rec, "session_id")
    assert not hasattr(rec, "result")


@pytest.mark.anyio
async def test_log_de_rate_limit_incluye_estado_y_tipo_del_evento(monkeypatch, provider_logs):
    messages = (
        _assistant("rate_limit"),
        _rate_limit_event("rejected", "seven_day"),
        _result_429(),
    )
    original = ProcessError("Command failed", exit_code=1, stderr="stderr-secreto")
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=messages, raise_after=original),
        raising=True,
    )
    with pytest.raises(LLMRateLimited):
        await AgentSDKProvider().run_agent(_request())

    records = _failure_records(provider_logs)
    assert len(records) == 1
    rec = records[0]
    assert rec.error_class == "LLMRateLimited"
    assert rec.sdk_exception == "ProcessError"
    assert rec.api_error_status == 429
    assert rec.assistant_error == "rate_limit"
    assert rec.rate_limit_status == "rejected"
    assert rec.rate_limit_type == "seven_day"
    assert rec.rate_limit_resets_at == 1_788_000_000
    assert rec.exit_code == 1
    rendered = " ".join(f"{k}={v}" for k, v in vars(rec).items())
    assert "stderr-secreto" not in rendered
    assert "hit your limit" not in rendered


@pytest.mark.anyio
async def test_resultado_ok_con_status_429_residual_y_process_error_no_es_limite(monkeypatch):
    """`api_error_status` solo cuenta si el último resultado fue de error."""
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(
            messages=(_result_429(is_error=False),),
            raise_after=ProcessError("Command failed", exit_code=1),
        ),
        raising=True,
    )
    with pytest.raises(LLMError) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert type(excinfo.value) is LLMError
    assert excinfo.value.api_error_status is None


@pytest.mark.anyio
async def test_result_error_registra_llm_call_failed_sin_texto(monkeypatch, provider_logs):
    result_error = ResultError(
        "TEXTO-DEL-MODELO",
        data={"api_error_status": 429, "usage": {"input_tokens": 5, "output_tokens": 1}},
    )
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(), raise_after=result_error),
        raising=True,
    )
    with pytest.raises(LLMRateLimited):
        await AgentSDKProvider().run_agent(_request())

    records = _failure_records(provider_logs)
    assert len(records) == 1
    assert records[0].error_class == "LLMRateLimited"
    assert records[0].sdk_exception == "ResultError"
    rendered = " ".join(f"{k}={v}" for k, v in vars(records[0]).items())
    assert "TEXTO-DEL-MODELO" not in rendered


# --- T112: sesión caducada (authentication_failed) -> LLMAuthenticationFailed ---


def _auth_result(**overrides: object) -> ResultMessage:
    kwargs: dict[str, object] = dict(
        is_error=True,
        api_error_status=None,
        result="Failed to authenticate",
        usage={"input_tokens": 40, "output_tokens": 3},
    )
    kwargs.update(overrides)
    return make_result_message(**kwargs)  # type: ignore[arg-type]


_AUTH_BRANCHES = {
    "final_del_bucle": dict(messages=(_assistant("authentication_failed"), _auth_result())),
    "result_error": dict(
        messages=(_assistant("authentication_failed"), _auth_result()),
        raise_after=ResultError("fallo", data={"api_error_status": None}),
    ),
    "claude_sdk_error": dict(
        messages=(_assistant("authentication_failed"), _auth_result()),
        raise_after=ClaudeSDKError("transporte"),
    ),
    "process_error": dict(
        messages=(_assistant("authentication_failed"), _auth_result()),
        raise_after=ProcessError("Command failed", exit_code=1),
    ),
    "exception_generica": dict(
        messages=(_assistant("authentication_failed"), _auth_result()),
        raise_after=RuntimeError("otro"),
    ),
    "last_result_none": dict(messages=(_assistant("authentication_failed"),)),
    "timeout_error_ajeno": dict(
        messages=(_assistant("authentication_failed"), _auth_result()),
        raise_after=TimeoutError("socket"),
    ),
}


@pytest.mark.anyio
@pytest.mark.parametrize("branch", list(_AUTH_BRANCHES))
async def test_authentication_failed_lanza_llm_authentication_failed_en_cada_rama(
    monkeypatch, branch
):
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(**_AUTH_BRANCHES[branch]),  # type: ignore[arg-type]
        raising=True,
    )
    with pytest.raises(LLMAuthenticationFailed) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    exc = excinfo.value
    assert type(exc) is LLMAuthenticationFailed
    assert isinstance(exc, LLMError)
    assert not isinstance(exc, LLMRateLimited)
    if branch != "last_result_none":
        # los tokens ya gastados se conservan.
        assert (exc.tokens_in, exc.tokens_out) == (40, 3)


@pytest.mark.anyio
async def test_authentication_failed_conserva_los_tokens_y_es_instancia_de_llm_error(monkeypatch):
    messages = (_assistant("authentication_failed"), _auth_result())
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    with pytest.raises(LLMError) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert isinstance(excinfo.value, LLMAuthenticationFailed)
    assert (excinfo.value.tokens_in, excinfo.value.tokens_out) == (40, 3)


@pytest.mark.anyio
async def test_authentication_failed_con_429_gana_llm_authentication_failed(monkeypatch):
    messages = (_assistant("authentication_failed"), _result_429())
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    with pytest.raises(LLMAuthenticationFailed) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert type(excinfo.value) is LLMAuthenticationFailed
    assert excinfo.value.api_error_status == 429


@pytest.mark.anyio
async def test_exito_con_assistant_error_authentication_failed_no_lanza_y_conserva_tokens(
    monkeypatch, provider_logs
):
    messages = (
        _assistant("authentication_failed"),
        make_result_message(usage={"input_tokens": 10, "output_tokens": 2}),
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    result = await AgentSDKProvider().run_agent(_request())
    assert (result.tokens_in, result.tokens_out) == (10, 2)
    assert _failure_records(provider_logs) == []


@pytest.mark.anyio
@pytest.mark.parametrize("branch", list(_AUTH_BRANCHES))
async def test_llm_call_failed_de_autenticacion_nombra_la_clase_y_no_vuelca_el_texto(
    monkeypatch, provider_logs, branch
):
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(**_AUTH_BRANCHES[branch]),  # type: ignore[arg-type]
        raising=True,
    )
    with pytest.raises(LLMAuthenticationFailed) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    records = _failure_records(provider_logs)
    assert len(records) == 1
    assert records[0].error_class == "LLMAuthenticationFailed"
    assert records[0].assistant_error == "authentication_failed"
    rendered = " ".join(f"{k}={v}" for k, v in vars(records[0]).items())
    assert str(excinfo.value) not in rendered


@pytest.mark.parametrize(
    ("status", "assistant_error", "expected"),
    [
        (None, "authentication_failed", LLMAuthenticationFailed),
        (429, "authentication_failed", LLMAuthenticationFailed),
        (500, "authentication_failed", LLMAuthenticationFailed),
        (429, "rate_limit", LLMRateLimited),
        (429, None, LLMRateLimited),
        (None, "rate_limit", LLMRateLimited),
        (500, "server_error", LLMError),
        (None, None, LLMError),
        (401, None, LLMError),
        (None, "Authentication_Failed", LLMError),
    ],
)
def test_llm_error_class_tabla_completa(status, assistant_error, expected):
    assert _llm_error_class(status, assistant_error) is expected


# --- T112: revisión (terminal_reason, deadline, cancelación con la señal) ---


def _terminal_result(**overrides: object) -> ResultMessage:
    kwargs: dict[str, object] = dict(
        terminal_reason="max_turns", usage={"input_tokens": 10, "output_tokens": 2}
    )
    kwargs.update(overrides)
    return make_result_message(**kwargs)  # type: ignore[arg-type]


@pytest.mark.anyio
async def test_terminal_reason_con_authentication_failed_lanza_llm_authentication_failed(
    monkeypatch,
):
    messages = (_assistant("authentication_failed"), _terminal_result())
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query(messages=messages), raising=True
    )
    with pytest.raises(LLMAuthenticationFailed) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert (excinfo.value.tokens_in, excinfo.value.tokens_out) == (10, 2)


@pytest.mark.anyio
async def test_terminal_reason_sin_senal_sigue_siendo_llm_error_a_secas(monkeypatch):
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(_terminal_result(),)),
        raising=True,
    )
    with pytest.raises(LLMError) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert type(excinfo.value) is LLMError


@pytest.mark.anyio
async def test_terminal_reason_ignora_un_status_429_residual_sin_is_error(monkeypatch):
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(_terminal_result(api_error_status=429),)),
        raising=True,
    )
    with pytest.raises(LLMError) as excinfo:
        await AgentSDKProvider().run_agent(_request())
    assert type(excinfo.value) is LLMError


@pytest.mark.anyio
async def test_authentication_failed_con_deadline_real_expirado_sigue_siendo_llm_timeout(
    monkeypatch,
):
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(_assistant("authentication_failed"),), sleep_after_s=10),
        raising=True,
    )
    with pytest.raises(LLMTimeout) as excinfo:
        await AgentSDKProvider().run_agent(_request(timeout_s=0.2))
    assert type(excinfo.value) is LLMTimeout


@pytest.mark.anyio
async def test_cancelled_error_con_authentication_failed_se_propaga_como_cancelled_error(
    monkeypatch,
):
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query(messages=(_assistant("authentication_failed"),), sleep_after_s=10),
        raising=True,
    )
    task = asyncio.ensure_future(AgentSDKProvider().run_agent(_request(timeout_s=3600)))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
