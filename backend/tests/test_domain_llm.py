"""Tests de la interfaz `LLMProvider` y sus tipos de datos.

No usan `FakeLLMProvider` (no existe hasta T40): para el test de
conformidad con el protocolo se define una clase mínima en este mismo
fichero. Ningún test llama a Claude, a la red ni a la base de datos.
"""

from dataclasses import FrozenInstanceError
from uuid import uuid4

import pytest

from nocturna.domain.errors import DomainError, LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRequest, AgentResult, AgentRole, LLMProvider


def _make_request(**overrides) -> AgentRequest:
    defaults = dict(
        role=AgentRole.READER,
        model="sonnet",
        prompt="lee este abstract",
        max_turns=1,
        timeout_s=60,
        item_id=uuid4(),
    )
    defaults.update(overrides)
    return AgentRequest(**defaults)


def _make_result(**overrides) -> AgentResult:
    defaults = dict(
        role=AgentRole.READER,
        model="sonnet",
        output_text="{}",
        tokens_in=100,
        tokens_out=40,
        duration_ms=1200,
    )
    defaults.update(overrides)
    return AgentResult(**defaults)


# --- AgentResult / AgentRequest -------------------------------------------


def test_agent_result_total_tokens_es_la_suma():
    result = _make_result(tokens_in=100, tokens_out=40)

    assert result.total_tokens == 140


def test_agent_request_es_frozen():
    request = _make_request()

    with pytest.raises(FrozenInstanceError):
        request.model = "opus"


def test_agent_result_es_frozen():
    result = _make_result()

    with pytest.raises(FrozenInstanceError):
        result.tokens_in = 0


# --- Conformidad con el protocolo LLMProvider -----------------------------


class _MinimalProvider:
    async def run_agent(self, request: AgentRequest) -> AgentResult:
        return _make_result()


class _NotAProvider:
    def alguna_otra_cosa(self) -> None:
        pass


def test_clase_minima_con_run_agent_satisface_llmprovider():
    provider = _MinimalProvider()

    assert isinstance(provider, LLMProvider)


def test_clase_sin_run_agent_no_satisface_llmprovider():
    not_provider = _NotAProvider()

    assert not isinstance(not_provider, LLMProvider)


# --- Blindaje del control de gasto: sin defaults en campos de coste -----


@pytest.mark.parametrize("missing_field", ["tokens_in", "tokens_out", "duration_ms"])
def test_agent_result_sin_campo_de_coste_falla(missing_field):
    kwargs = dict(
        role=AgentRole.READER,
        model="sonnet",
        output_text="{}",
        tokens_in=100,
        tokens_out=40,
        duration_ms=1200,
    )
    del kwargs[missing_field]

    with pytest.raises(TypeError):
        AgentResult(**kwargs)


# --- Jerarquía de excepciones del proveedor LLM ---------------------------


@pytest.mark.parametrize("exception_cls", [LLMRateLimited, LLMTimeout])
def test_excepciones_de_llm_heredan_de_llmerror_y_domainerror(exception_cls):
    assert issubclass(exception_cls, LLMError)
    assert issubclass(exception_cls, DomainError)
