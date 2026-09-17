"""`FakeLLMProvider` (`tests/fakes/llm.py`): el doble que usa el resto de la
suite para no llamar nunca a Claude de verdad. Estos tests prueban al propio
doble, no código de producción.
"""

import pytest
from fakes.llm import FakeLLMProvider, ResponseQueueExhausted

from nocturna.domain.errors import LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRequest, AgentRole, LLMProvider


def _request(role: AgentRole = AgentRole.READER, **overrides: object) -> AgentRequest:
    defaults: dict[str, object] = dict(
        role=role,
        model="claude-sonnet-4-5",
        prompt="lee este abstract",
        max_turns=3,
        timeout_s=5,
    )
    defaults.update(overrides)
    return AgentRequest(**defaults)  # type: ignore[arg-type]


def test_fake_llm_provider_cumple_el_protocolo_llm_provider():
    fake = FakeLLMProvider()

    assert isinstance(fake, LLMProvider)


@pytest.mark.anyio
async def test_las_colas_son_fifo_e_independientes_por_rol():
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"n": 1}, tokens_in=10, tokens_out=1)
    fake.respond(AgentRole.READER, json={"n": 2}, tokens_in=20, tokens_out=2)
    fake.respond(AgentRole.EDITOR, json={"n": "editor"}, tokens_in=100, tokens_out=10)

    first_reader = await fake.run_agent(_request(AgentRole.READER))
    editor = await fake.run_agent(_request(AgentRole.EDITOR))
    second_reader = await fake.run_agent(_request(AgentRole.READER))

    assert first_reader.output_text == '{"n": 1}'
    assert second_reader.output_text == '{"n": 2}'
    assert editor.output_text == '{"n": "editor"}'


@pytest.mark.anyio
async def test_calls_registra_el_agent_request_completo_incluidas_las_llamadas_fallidas():
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"n": 1}, tokens_in=10, tokens_out=1)
    fake.fail(AgentRole.READER, error=LLMTimeout("no respondió"))

    ok_request = _request(AgentRole.READER, item_id=None)
    await fake.run_agent(ok_request)
    failing_request = _request(AgentRole.READER, prompt="otro prompt")
    with pytest.raises(LLMTimeout):
        await fake.run_agent(failing_request)

    assert fake.calls == [ok_request, failing_request]


@pytest.mark.anyio
async def test_fail_lanza_llm_timeout_programado():
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=LLMTimeout("no respondió a tiempo"))

    with pytest.raises(LLMTimeout, match="no respondió a tiempo"):
        await fake.run_agent(_request(AgentRole.READER))


@pytest.mark.anyio
async def test_fail_lanza_llm_rate_limited_programado():
    fake = FakeLLMProvider()
    fake.fail(AgentRole.POPULARIZER, error=LLMRateLimited("límite alcanzado"))

    with pytest.raises(LLMRateLimited, match="límite alcanzado"):
        await fake.run_agent(_request(AgentRole.POPULARIZER))


@pytest.mark.anyio
async def test_fail_lanza_llm_error_con_tokens_programados():
    fake = FakeLLMProvider()
    fake.fail(AgentRole.EDITOR, error=LLMError("falló", tokens_in=500, tokens_out=20))

    with pytest.raises(LLMError) as excinfo:
        await fake.run_agent(_request(AgentRole.EDITOR))

    assert excinfo.value.tokens_in == 500
    assert excinfo.value.tokens_out == 20


@pytest.mark.anyio
async def test_fail_lanza_llm_error_sin_tokens_por_defecto_a_cero():
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=LLMError("falló sin usage"))

    with pytest.raises(LLMError) as excinfo:
        await fake.run_agent(_request(AgentRole.READER))

    assert excinfo.value.tokens_in == 0
    assert excinfo.value.tokens_out == 0


@pytest.mark.anyio
async def test_fail_lanza_excepcion_generica_sin_asumir_que_todo_es_llm_error():
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=RuntimeError("fallo inesperado ajeno al dominio"))

    with pytest.raises(RuntimeError, match="fallo inesperado ajeno al dominio"):
        await fake.run_agent(_request(AgentRole.READER))


@pytest.mark.anyio
async def test_cola_agotada_lanza_response_queue_exhausted():
    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json={"n": 1}, tokens_in=10, tokens_out=1)
    await fake.run_agent(_request(AgentRole.READER))

    with pytest.raises(ResponseQueueExhausted):
        await fake.run_agent(_request(AgentRole.READER))


@pytest.mark.anyio
async def test_cola_agotada_de_un_rol_nunca_pedido_tambien_lanza_response_queue_exhausted():
    fake = FakeLLMProvider()

    with pytest.raises(ResponseQueueExhausted):
        await fake.run_agent(_request(AgentRole.EDITOR))


def test_respond_sin_tokens_in_lanza_type_error():
    fake = FakeLLMProvider()

    with pytest.raises(TypeError):
        fake.respond(AgentRole.READER, json={"n": 1}, tokens_out=1)  # type: ignore[call-arg]


def test_respond_sin_tokens_out_lanza_type_error():
    fake = FakeLLMProvider()

    with pytest.raises(TypeError):
        fake.respond(AgentRole.READER, json={"n": 1}, tokens_in=1)  # type: ignore[call-arg]


def test_respond_con_json_y_raw_a_la_vez_lanza_value_error():
    fake = FakeLLMProvider()

    with pytest.raises(ValueError):
        fake.respond(AgentRole.READER, json={"n": 1}, raw="texto crudo", tokens_in=1, tokens_out=1)


def test_respond_sin_json_ni_raw_lanza_value_error():
    fake = FakeLLMProvider()

    with pytest.raises(ValueError):
        fake.respond(AgentRole.READER, tokens_in=1, tokens_out=1)
