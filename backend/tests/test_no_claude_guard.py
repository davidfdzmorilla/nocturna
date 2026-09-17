"""La guarda anti-Claude de `conftest.py` (`_no_claude`) es infraestructura
de la propia suite: si se rompe en silencio, un test de la suite normal
podría gastar suscripción de verdad. Hermano de `test_no_network_guard.py`.

Cubre los tres puntos de parcheo que documenta `_no_claude`:

- `SubprocessCLITransport.connect` (`conftest.py:166`): el cierre real del
  agujero -- el único punto por el que pasan tanto `query()` como
  `ClaudeSDKClient` antes de lanzar el CLI, con independencia de qué nombre
  se haya importado o cuándo. `test_transporte_subprocess_connect_esta_parcheado_por_la_guarda`
  lo comprueba por identidad (`SubprocessCLITransport.connect.__name__`),
  sin invocar `connect()`, así que se pone en rojo si alguien borra esa
  línea de `conftest.py` sin arrancar el CLI para averiguarlo.
- El nombre local `query` ya vinculado en `agent_sdk_provider` (el único
  call site real de producción hoy).
- El atributo `claude_agent_sdk.query` (defensa adicional para quien acceda
  por atributo).

Los dos últimos son "cinturón sobre tirantes" (ver `conftest.py:134-139`):
cubren el caso trivial, pero no son el cierre del agujero. Ninguno de los
tres tests está marcado `manual`, así que los tres corren con la guarda
activa.
"""

import pytest

from nocturna.domain.llm import AgentRequest, AgentRole
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider


def _request() -> AgentRequest:
    return AgentRequest(
        role=AgentRole.READER,
        model="claude-sonnet-4-5",
        prompt="lee este abstract",
        max_turns=3,
        timeout_s=5,
    )


@pytest.mark.anyio
async def test_run_agent_de_verdad_explota_con_el_mensaje_de_la_guarda():
    provider = AgentSDKProvider()

    with pytest.raises(RuntimeError, match="test intentando llamar a Claude"):
        await provider.run_agent(_request())


@pytest.mark.anyio
async def test_claude_agent_sdk_query_por_atributo_tambien_esta_bloqueado():
    import claude_agent_sdk

    with pytest.raises(RuntimeError, match="test intentando llamar a Claude"):
        async for _ in claude_agent_sdk.query(prompt="hola", options=None):
            pass


def test_transporte_subprocess_connect_esta_parcheado_por_la_guarda():
    """Cierre real del agujero (`conftest.py:166`), no sus síntomas.

    Los dos tests de arriba pasan por los parcheos *públicos* de `query`
    (`agent_sdk_provider.query` y `claude_agent_sdk.query`), que el propio
    `conftest.py` describe como "cinturón sobre tirantes" -- no cierran nada
    por sí solos, ver docstring de `_no_claude`. El cierre real es
    `SubprocessCLITransport.connect`: es el único método que de verdad lanza
    `anyio.open_process(...)` para arrancar el CLI, y tanto `query()` como
    `ClaudeSDKClient` pasan por él con independencia de qué nombre se haya
    importado o cuándo se vinculó.

    Este test comprueba, por identidad, que ese método sigue sustituido por
    el centinela de la guarda -- sin invocar `connect()` (no abre ningún
    transporte, no hay riesgo de gastar suscripción): si alguien borra la
    línea `monkeypatch.setattr(SubprocessCLITransport, "connect", ...)` de
    `conftest.py`, este test se pone en rojo. Verificado por mutación: con
    esa línea temporalmente eliminada, este test (y solo este test) falla;
    con la línea restaurada, pasa. Los otros dos tests de este fichero NO
    detectan esa mutación -- por eso hacía falta uno nuevo.
    """
    from claude_agent_sdk._internal.transport.subprocess_cli import (
        SubprocessCLITransport,
    )

    assert SubprocessCLITransport.connect.__name__ == "_blocked_transport_connect", (
        "SubprocessCLITransport.connect no está parcheado por la guarda anti-Claude "
        "(_no_claude en conftest.py): si esta línea desaparece de conftest.py, "
        "query()/ClaudeSDKClient podrían lanzar el CLI 'claude' real y gastar "
        "suscripción. Revisa conftest.py:166."
    )
