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
async def test_model_usage_con_una_sola_entrada_igual_al_modelo_pedido(monkeypatch):
    """`model_usage` con una única entrada, la del modelo pedido en
    `AgentRequest` y con las mismas cifras que `usage`, debe dar los mismos
    tokens que si solo hubiera `usage` (comparar con
    `test_solo_input_y_output_tokens_suman_correctamente`, 100/40): el caso
    base antes de que aparezca ningún modelo extra (Haiku) que el CLI
    gaste por su cuenta. `usage` no puede ser `None` aquí: `run_agent`
    exige `usage` para tratar el `ResultMessage` como utilizable, con
    independencia de `model_usage` (rama `if last_result.usage is None`).
    """
    result_message = make_result_message(
        usage={"input_tokens": 100, "output_tokens": 40},
        model_usage={"claude-sonnet-4-5": {"inputTokens": 100, "outputTokens": 40}},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 100
    assert result.tokens_out == 40


@pytest.mark.anyio
async def test_model_usage_con_gasto_de_otro_modelo_no_pedido_no_se_pierde(monkeypatch):
    """El test del hallazgo real (T40, humo manual, 2026-09-17): una sola
    llamada al Reader con `model="sonnet"` cuyo `usage` solo reportaba el
    Sonnet pedido (523 tokens de entrada, 6 de salida), mientras el CLI
    gastó además 946 tokens de Haiku por su cuenta (929 de entrada, 17 de
    salida) que **sí se facturan** -- lo confirma que la suma de
    `costUSD` de ambas entradas (0.001014 + 0.001106) coincide con
    `total_cost_usd` (0.00212) del volcado real. Contar solo `usage`
    registraba el 36% de lo gastado (subregistro de 2,8x). Este es el test
    que impide que ese subregistro vuelva: `model_usage` trae las dos
    entradas reales (Haiku y Sonnet), `usage` solo la del Sonnet como en
    la llamada real, y el resultado debe sumar TODAS las entradas de
    `model_usage` -- 929 + 523 = 1452 de entrada, 17 + 6 = 23 de salida --
    no solo la del modelo pedido.
    """
    result_message = make_result_message(
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={
            "claude-haiku-4-5-20251001": {
                "inputTokens": 929,
                "outputTokens": 17,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
            },
            "claude-sonnet-5": {
                "inputTokens": 523,
                "outputTokens": 6,
                "cacheReadInputTokens": 0,
                "cacheCreationInputTokens": 0,
            },
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 1452
    assert result.tokens_out == 23


@pytest.mark.anyio
async def test_model_usage_none_respalda_en_usage(monkeypatch):
    """`model_usage=None` (CLI antiguo, o un camino que no lo informa):
    respaldo a `usage`, mismo comportamiento que si `model_usage` nunca
    hubiera existido como campo.
    """
    result_message = make_result_message(
        usage={"input_tokens": 100, "output_tokens": 40},
        model_usage=None,
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 100
    assert result.tokens_out == 40


@pytest.mark.anyio
async def test_model_usage_vacio_respalda_en_usage_igual_que_none(monkeypatch):
    """`model_usage={}` (dict vacío, distinto de `None`) debe tratarse
    igual que `None`: respaldo a `usage`. `_tokens_from_model_usage`
    devuelve `None` tanto para `None` como para `{}` precisamente para que
    `_extract_tokens` no los distinga.
    """
    result_message = make_result_message(
        usage={"input_tokens": 100, "output_tokens": 40},
        model_usage={},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 100
    assert result.tokens_out == 40


@pytest.mark.anyio
async def test_claves_de_cache_ausentes_o_none_en_model_usage_cuentan_como_cero(monkeypatch):
    """Gemelo camelCase de `test_claves_de_cache_ausentes_o_none_cuentan_como_cero`:
    una entrada de `model_usage` puede llegar sin `cacheCreationInputTokens`
    (directamente ausente del dict) o con `cacheReadInputTokens=None`, y
    debe contar como cero sin romper -- misma política `or 0` que
    `_tokens_from_usage`, aplicada a las claves camelCase.

    `usage` se fija a propósito con cifras MENORES que `model_usage` (10/5
    frente a 100/40), no mayores. Antes de que `_extract_tokens` pasara a
    tomar el `max()` componente a componente entre las dos fuentes
    (revisión de T40), este test usaba un `usage` señuelo *mayor* (999/999)
    para demostrar que el resultado salía de `model_usage`; bajo `max()`
    ese señuelo gana legítimamente y el test deja de probar lo que dice
    probar (`assert 999 == 100` falla, no revela ningún bug). Con `usage`
    menor, no interfiere: si se rompiera el tratamiento `or 0` de las
    claves de caché de `model_usage`, este test seguiría cayendo. La
    afirmación de "de dónde sale el resultado cuando ambas fuentes están
    pobladas" queda cubierta por separado en
    `test_model_usage_con_gasto_de_otro_modelo_no_pedido_no_se_pierde` y
    `test_max_favorece_usage_cuando_model_usage_es_menor`; bajo `max()` no
    tiene sentido como una única afirmación aislada por test.
    """
    result_message = make_result_message(
        usage={"input_tokens": 10, "output_tokens": 5},
        model_usage={
            "claude-sonnet-4-5": {
                "inputTokens": 100,
                "outputTokens": 40,
                "cacheReadInputTokens": None,
                # cacheCreationInputTokens directamente ausente del dict
            },
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
async def test_model_usage_con_cache_no_cero_se_suma_a_tokens_in(monkeypatch):
    """Protege la suma real de `cacheCreationInputTokens`/
    `cacheReadInputTokens` en `_tokens_from_model_usage` (mutación
    superviviente de la revisión de T40: borrar esa suma dejaba la suite en
    verde -- los seis tests de `model_usage` de este fichero fijaban la
    caché a `0`, ausente o `None`, ninguno con un valor distinto de cero).
    Aquí `cacheReadInputTokens=50000` debe aparecer en `tokens_in`. `usage`
    va pequeño a propósito y no interfiere bajo `max()`.
    """
    result_message = make_result_message(
        usage={"input_tokens": 1, "output_tokens": 1},
        model_usage={
            "claude-sonnet-4-5": {
                "inputTokens": 100,
                "outputTokens": 40,
                "cacheReadInputTokens": 50000,
                "cacheCreationInputTokens": 0,
            },
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 100 + 50000
    assert result.tokens_out == 40


@pytest.mark.anyio
async def test_usage_y_model_usage_solapados_no_se_suman_dos_veces(monkeypatch):
    """No doblar la contabilidad: cuando `usage` y una entrada de
    `model_usage` traen las mismas cifras -- el caso real del hallazgo de
    T40, donde el Sonnet pedido aparece en ambos con 523/6 -- el resultado
    debe ser **solo** el de `model_usage` (523/6), no la suma de los dos
    (1046/12). Este es el test que protege contra el arreglo excesivo: si
    `_extract_tokens` sumara `usage` y `model_usage` en vez de usar uno u
    otro, este test lo detecta -- aunque
    `test_model_usage_con_gasto_de_otro_modelo_no_pedido_no_se_pierde`
    también lo haría de rebote (su `usage` también solapa con una entrada
    de `model_usage`), este test aísla el caso sin el ruido de la segunda
    entrada de Haiku.
    """
    result_message = make_result_message(
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={"claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6}},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 523
    assert result.tokens_out == 6


@pytest.mark.anyio
async def test_usage_none_con_model_usage_poblado_da_resultado_no_llm_error(monkeypatch):
    """El bloqueante corregido de T40: antes de esta corrección, `run_agent`
    lanzaba `LLMError` en cuanto `last_result.usage is None`, sin mirar
    `model_usage` y sin propagar tokens. Un `ResultMessage` de éxito con
    `usage=None` pero `model_usage` poblado es un camino real y utilizable
    (`_internal/message_parser.py` construye los dos campos con `.get()`
    independientes sobre el mismo frame, ver docstring de módulo): hay
    datos con los que contabilizar el gasto y texto de salida, así que debe
    devolver un `AgentResult` normal -- no `LLMError` -- con los tokens
    de `model_usage` (1452/23, Haiku + Sonnet del hallazgo real de T40).
    """
    result_message = make_result_message(
        usage=None,
        model_usage={
            "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
            "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 1452
    assert result.tokens_out == 23


@pytest.mark.anyio
async def test_usage_y_model_usage_ambos_ausentes_lanza_llm_error_con_tokens_a_cero(monkeypatch):
    """`ResultMessage` de éxito con `usage=None` **y** `model_usage=None`:
    a diferencia de `test_usage_none_con_model_usage_poblado_da_resultado_no_llm_error`,
    aquí no hay ninguna fuente utilizable, así que sigue siendo fallo. Si se
    borrara la comprobación combinada (`last_result.usage is None and not
    last_result.model_usage`) en `run_agent`, esto devolvería un
    `AgentResult` de éxito con 0 tokens contabilizados en silencio -- el
    mismo tipo de fallo silencioso que rompería `BudgetGuard` sin que nadie
    lo note.
    """
    result_message = make_result_message(usage=None, model_usage=None, result="algo de texto")
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 0
    assert excinfo.value.tokens_out == 0


@pytest.mark.anyio
async def test_usage_none_y_model_usage_vacio_lanza_llm_error_con_tokens_a_cero(monkeypatch):
    """Gemelo del test anterior con `model_usage={}` (dict vacío) en vez de
    `None`: `_tokens_from_model_usage` trata ambos igual (`not {}` es
    `True`), así que debe fallar igual, con los tokens a cero.
    """
    result_message = make_result_message(usage=None, model_usage={}, result="algo de texto")
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 0
    assert excinfo.value.tokens_out == 0


@pytest.mark.anyio
@pytest.mark.parametrize(
    "model_usage",
    [None, {}],
    ids=["model_usage_none", "model_usage_vacio"],
)
async def test_usage_vacio_con_model_usage_no_utilizable_lanza_llm_error(monkeypatch, model_usage):
    """Agujero real que motivó este test (no cubierto por ningún test previo
    a T40): la guardia combinada usaba `last_result.usage is None`, una
    comprobación de identidad. `usage={}` (dict vacío, no `None`) NO es
    `None`, así que con la guardia vieja un `ResultMessage` de éxito con
    `usage={}` y `model_usage` ausente/`None`/`{}` colaba de largo -- no
    entraba por ninguna otra rama de fallo (`subtype`, `is_error`,
    `terminal_reason` y `result` iban todos bien) -- y `run_agent` devolvía
    un `AgentResult` de ÉXITO con `tokens_in=0, tokens_out=0`.

    Ese es el peor caso posible para `BudgetGuard`: no un error visible que
    se registre como fallo, sino una llamada real que sí ocurrió (y que el
    proveedor sí pudo cobrar) contabilizada como gasto nulo en silencio.
    `BudgetGuard` suma ese cero, sigue creyendo que queda presupuesto, y
    autoriza la siguiente llamada sin que nadie se entere del agujero. La
    guardia actual usa `not last_result.usage`, veracidad en vez de
    identidad, que trata `{}` igual que `None` -- por eso aquí debe fallar
    con `LLMError`, no devolver un resultado.

    Parametrizado sobre las combinaciones de `model_usage` que dejan a
    `usage={}` sin ningún respaldo utilizable (`None` y `{}`, que
    `_tokens_from_model_usage` ya trata igual -- ver
    `test_model_usage_vacio_respalda_en_usage_igual_que_none`); un
    `model_usage` poblado no pertenece aquí porque entonces SÍ hay una
    fuente utilizable (ese caso ya lo cubre
    `test_usage_none_con_model_usage_poblado_da_resultado_no_llm_error`, con
    `usage=None` en vez de `usage={}`; el comportamiento con `usage={}` en
    su lugar es idéntico porque ambos son falsy).
    """
    result_message = make_result_message(usage={}, model_usage=model_usage, result="algo de texto")
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert "usage" in str(excinfo.value)
    assert "model_usage" in str(excinfo.value)
    assert excinfo.value.tokens_in == 0
    assert excinfo.value.tokens_out == 0


@pytest.mark.anyio
async def test_max_favorece_usage_cuando_model_usage_es_menor(monkeypatch):
    """Dirección del `max()` que motivó el cambio de política en
    `_extract_tokens` (revisión de T40): si `model_usage` llega parcial y
    su total (100/40) queda POR DEBAJO del de `usage` (200/80), una
    preferencia estricta por `model_usage` habría *reducido* el registro
    frente a `usage` solo -- justo lo contrario de la política
    conservadora del proyecto ante ambigüedad (sobreestimar, nunca
    infraestimar). El `max()` por componente evita eso: el resultado debe
    ser el de `usage`, 200/80, no el de `model_usage`, 100/40.
    """
    result_message = make_result_message(
        usage={"input_tokens": 200, "output_tokens": 80},
        model_usage={"claude-sonnet-4-5": {"inputTokens": 100, "outputTokens": 40}},
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    result = await provider.run_agent(_request())

    assert result.tokens_in == 200
    assert result.tokens_out == 80


@pytest.mark.anyio
async def test_is_error_con_subtype_success_con_model_usage_no_se_pierde_el_gasto_de_otros_modelos(
    monkeypatch,
):
    """Ancla en `model_usage` la rama posterior al bucle
    (`last_result.subtype != "success" or last_result.is_error`) de
    `_extract_tokens` -- mutación superviviente de la revisión de T40: es
    el camino que más se recorre una noche mala, cómo el CLI reporta un
    429/500/529. Gemelo de
    `test_subtype_success_con_is_error_lanza_llm_error_con_tokens_y_api_error_status`
    con `model_usage` poblado además de `usage`: el resultado debe ser
    1452/23 (Haiku + Sonnet), no solo 523/6 del `usage` del Sonnet pedido.
    """
    rate_limited_result = make_result_message(
        subtype="success",
        is_error=True,
        api_error_status=429,
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={
            "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
            "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
        },
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([rate_limited_result]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 1452
    assert excinfo.value.tokens_out == 23


@pytest.mark.anyio
async def test_terminal_reason_max_turns_con_model_usage_no_se_pierde_el_gasto_de_otros_modelos(
    monkeypatch,
):
    """Ancla en `model_usage` la rama `terminal_reason` de `_extract_tokens`
    -- mutación superviviente de la revisión de T40. Gemelo de
    `test_terminal_reason_max_turns_en_result_de_exito_lanza_llm_error_con_tokens`
    con `model_usage` poblado además de `usage`: el resultado debe ser
    1452/23, no solo 523/6.
    """
    incomplete_result = make_result_message(
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={
            "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
            "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
        },
        terminal_reason="max_turns",
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([incomplete_result]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 1452
    assert excinfo.value.tokens_out == 23


@pytest.mark.anyio
async def test_sin_texto_de_resultado_con_model_usage_no_se_pierde_el_gasto_de_otros_modelos(
    monkeypatch,
):
    """Ancla en `model_usage` la rama `result is None` de `_extract_tokens`
    -- mutación superviviente de la revisión de T40. Gemelo de
    `test_result_message_de_exito_sin_texto_de_resultado_lanza_llm_error_con_tokens`
    con `model_usage` poblado además de `usage`: el resultado debe ser
    1452/23, no solo 523/6.
    """
    result_message = make_result_message(
        usage={"input_tokens": 523, "output_tokens": 6},
        model_usage={
            "claude-haiku-4-5-20251001": {"inputTokens": 929, "outputTokens": 17},
            "claude-sonnet-5": {"inputTokens": 523, "outputTokens": 6},
        },
        result=None,
    )
    monkeypatch.setattr(
        agent_sdk_provider, "query", build_fake_query([result_message]), raising=True
    )
    provider = AgentSDKProvider()

    with pytest.raises(LLMError) as excinfo:
        await provider.run_agent(_request())

    assert excinfo.value.tokens_in == 1452
    assert excinfo.value.tokens_out == 23
