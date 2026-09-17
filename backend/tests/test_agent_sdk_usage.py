"""`AgentSDKProvider.run_agent`: traducción de los mensajes reales del SDK
a `AgentResult`, y a `LLMError` cuando el resultado no es utilizable.

Todos los tests parchean `agent_sdk_provider.query` con un doble construido
en `helpers/sdk_doubles.py` a partir de los tipos REALES del paquete
instalado (`ResultMessage`), no de la documentación pública (ver hallazgo
del paso 1 de T40). El `usage` de `ResultMessage` es `dict[str, Any] | None`.
"""

import pytest
from helpers.sdk_doubles import build_fake_query, make_result_message

from nocturna.domain.errors import LLMError
from nocturna.domain.llm import AgentRequest, AgentRole
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider


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
async def test_solo_input_y_output_tokens_suman_correctamente(monkeypatch):
    result_message = make_result_message(
        usage={"input_tokens": 100, "output_tokens": 40},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 100
    assert result.tokens_out == 40


@pytest.mark.anyio
async def test_tokens_de_cache_se_suman_a_tokens_in(monkeypatch):
    """Política conservadora de conteo (docstring de `_tokens_from_usage`):
    `tokens_in` incluye caché de creación y de lectura, porque la
    suscripción paga esos tokens igual que los de entrada normales. Si este
    test se rompe, el significado del control de gasto (`BudgetGuard`)
    cambia sin que nadie lo haya decidido.
    """
    result_message = make_result_message(
        usage={
            "input_tokens": 100,
            "output_tokens": 40,
            "cache_creation_input_tokens": 500,
            "cache_read_input_tokens": 250,
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 100 + 500 + 250
    assert result.tokens_out == 40


@pytest.mark.anyio
async def test_claves_de_cache_ausentes_o_none_cuentan_como_cero(monkeypatch):
    result_message = make_result_message(
        usage={
            "input_tokens": 100,
            "output_tokens": 40,
            "cache_creation_input_tokens": None,
            # cache_read_input_tokens directamente ausente del dict
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 100
    assert result.tokens_out == 40


@pytest.mark.anyio
async def test_output_text_sale_de_result_message_result(monkeypatch):
    result_message = make_result_message(
        usage={"input_tokens": 10, "output_tokens": 5},
        result='{"interest_score": 4}',
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.output_text == '{"interest_score": 4}'


@pytest.mark.anyio
async def test_duration_ms_es_mayor_que_cero(monkeypatch):
    result_message = make_result_message(usage={"input_tokens": 1, "output_tokens": 1})
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query([result_message], sleep_before_s=0.01),
        raising=True,
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.duration_ms > 0


@pytest.mark.anyio
async def test_sin_ningun_result_message_lanza_llm_error(monkeypatch):
    monkeypatch.setattr(agent_sdk_provider, "query", build_fake_query([]), raising=True)
    provider = AgentSDKProvider()

    with pytest.raises(LLMError):
        await provider.run_agent(_request())


@pytest.mark.anyio
async def test_subtype_distinto_de_success_lanza_llm_error_con_tokens_del_usage(monkeypatch):
    """Reproduce la secuencia real del SDK para un fallo: `query()` emite un
    `ResultMessage` con `is_error=True` y DESPUÉS el generador lanza
    `ResultError` (ver docstring de `helpers/sdk_doubles.build_fake_query`).
    Un doble que solo emitiera el `ResultMessage` de error sin la
    `ResultError` posterior no ejercitaría el camino que de verdad recorre
    `AgentSDKProvider` (`except ResultError`), y este test dejaría de probar
    lo que dice probar.
    """
    from claude_agent_sdk import ResultError

    error_result = make_result_message(
        subtype="error_max_turns",
        is_error=True,
        result=None,
        usage={"input_tokens": 77, "output_tokens": 3},
    )
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query([error_result], raise_after=ResultError("el agente se quedó sin turnos")),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 77
    assert excinfo.value.tokens_out == 3


@pytest.mark.anyio
async def test_subtype_success_con_is_error_lanza_llm_error_con_tokens_y_api_error_status(
    monkeypatch,
):
    """El CLI puede marcar `subtype="success"` con `is_error=True` cuando un
    429/500/529 interrumpe el turno sin que el propio agente falle (ver
    `ResultMessage.api_error_status`, citado en el docstring de
    `AgentSDKProvider.run_agent`). A diferencia del test anterior, aquí no
    hay `raise_after`: el generador termina limpio (agotamiento normal,
    sin `ResultError`), así que este test ejercita la rama posterior al
    bucle (`if last_result.subtype != "success" or last_result.is_error`),
    no la de `except ResultError`. Antes de esta corrección, el criterio
    era solo `subtype != "success"`, así que un 429 con `subtype="success"`
    salía como un `AgentResult` de éxito con el gasto sin contabilizar.
    """
    rate_limited_result = make_result_message(
        subtype="success",
        is_error=True,
        api_error_status=429,
        usage={"input_tokens": 50, "output_tokens": 10},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([rate_limited_result]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 50
    assert excinfo.value.tokens_out == 10
    assert "429" in str(excinfo.value)
    assert excinfo.value.api_error_status == 429


@pytest.mark.anyio
async def test_usage_ausente_en_result_message_se_recupera_del_payload_de_result_error(
    monkeypatch,
):
    """El `ResultMessage` de error puede llegar sin `usage` (p. ej. si el
    CLI no llegó a completar el turno) mientras que el payload de la
    `ResultError` posterior sí lo trae en `data['usage']`. Antes de esta
    corrección, la extracción de `usage` en `except ResultError` era un
    ternario anidado que solo miraba `exc.data` cuando `last_result` era
    `None`, no cuando `last_result.usage` era `None`: ese gasto se perdía.
    """
    from claude_agent_sdk import ResultError

    error_result = make_result_message(
        subtype="error_during_execution",
        is_error=True,
        result=None,
        usage=None,
    )
    result_error = ResultError(
        "el agente falló a mitad de turno",
        data={"usage": {"input_tokens": 20, "output_tokens": 5}},
    )
    monkeypatch.setattr(
        agent_sdk_provider,
        "query",
        build_fake_query([error_result], raise_after=result_error),
        raising=True,
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 20
    assert excinfo.value.tokens_out == 5


@pytest.mark.anyio
async def test_terminal_reason_max_turns_en_result_de_exito_lanza_llm_error_con_tokens(
    monkeypatch,
):
    """`terminal_reason="max_turns"` con `subtype="success"` e
    `is_error=False` es un éxito a medias para el CLI: el texto de `result`
    puede estar incompleto, así que `AgentSDKProvider` lo trata como fallo
    sin perder el gasto ya contabilizado en `usage`.
    """
    incomplete_result = make_result_message(
        usage={"input_tokens": 200, "output_tokens": 30},
        terminal_reason="max_turns",
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([incomplete_result]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 200
    assert excinfo.value.tokens_out == 30


@pytest.mark.anyio
async def test_terminal_reason_completed_no_se_trata_como_fallo(monkeypatch):
    """`terminal_reason="completed"` (no solo `None`) sigue siendo un éxito
    normal -- no regresión sobre el resto de tests de este fichero, que no
    fijan `terminal_reason` y por tanto ejercitan solo el caso `None`.
    """
    result_message = make_result_message(
        usage={"input_tokens": 10, "output_tokens": 5},
        terminal_reason="completed",
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 10
    assert result.tokens_out == 5


@pytest.mark.anyio
async def test_result_message_de_exito_sin_texto_de_resultado_lanza_llm_error_con_tokens(
    monkeypatch,
):
    """`ResultMessage` de éxito (`subtype="success"`, `is_error=False`, con
    `usage`) pero `result=None`: sin texto de salida utilizable, se trata
    como fallo sin perder el gasto. Si se borrara la comprobación
    `last_result.result is None` en `run_agent`, esto devolvería un
    `AgentResult(output_text=None)` que viola en silencio el tipo del
    dataclass (`output_text: str`).
    """
    result_message = make_result_message(
        usage={"input_tokens": 15, "output_tokens": 8},
        result=None,
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 15
    assert excinfo.value.tokens_out == 8


@pytest.mark.anyio
async def test_result_message_de_exito_sin_usage_lanza_llm_error(monkeypatch):
    """`ResultMessage` de éxito sin `usage` no puede contabilizar gasto: se
    trata como fallo. Si se borrara la comprobación `last_result.usage is
    None` en `run_agent`, esto devolvería un `AgentResult` de éxito con 0
    tokens contabilizados en silencio -- el mismo tipo de fallo silencioso
    que rompería `BudgetGuard` sin que nadie lo note.
    """
    result_message = make_result_message(usage=None, result="algo de texto")
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError):
        await provider.run_agent(_request())
