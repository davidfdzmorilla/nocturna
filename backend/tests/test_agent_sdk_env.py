"""`AgentSDKProvider` nunca debe correr con una API key en variable de
entorno del proceso: el proyecto corre contra la suscripción Claude Max
vía el CLI de Claude Code, nunca contra una API key (`CLAUDE.md`,
"Restricción que gobierna todo el diseño"). La comprobación vive en
`_check_no_blocked_env_vars` y se llama dos veces: en `__init__` y de nuevo
al principio de cada `run_agent`, porque la variable puede aparecer a mitad
de la noche.

Estos tests no llaman a `query()` de verdad en ningún caso: en el primero,
`ApiKeyInEnvironment` corta antes de construirse el provider; en el segundo,
corta al principio de `run_agent`, antes de tocar el SDK.
"""

import pytest

from nocturna.domain.llm import AgentRequest, AgentRole
from nocturna.infrastructure.llm.agent_sdk_provider import (
    AgentSDKProvider,
    ApiKeyInEnvironment,
)


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


def test_construir_el_provider_con_api_key_en_el_entorno_falla(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    with pytest.raises(ApiKeyInEnvironment):
        AgentSDKProvider()


@pytest.mark.anyio
async def test_api_key_aparecida_entre_construccion_y_run_agent_tambien_falla(monkeypatch):
    provider = AgentSDKProvider()

    monkeypatch.setenv("ANTHROPIC_API_KEY", "x")

    with pytest.raises(ApiKeyInEnvironment):
        await provider.run_agent(_request())
