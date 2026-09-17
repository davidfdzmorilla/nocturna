"""Interfaz del proveedor LLM, desacoplada del Claude Agent SDK.

El dominio no sabe nada de `claude-agent-sdk`, de `ClaudeAgentOptions` ni de
servidores MCP: eso es de `infrastructure/llm/`. Aquí solo se define el
vocabulario de roles de agente y el contrato mínimo (`AgentRequest` entra,
`AgentResult` sale) que cualquier implementación de `LLMProvider` debe
cumplir, incluida una futura `ApiKeyProvider` si cambia la política de
suscripción.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable
from uuid import UUID


class AgentRole(StrEnum):
    """Rol de un agente del pipeline. Vocabulario único, reutilizado por `AgentCall`."""

    READER = "reader"
    POPULARIZER = "popularizer"
    EDITOR = "editor"


@dataclass(frozen=True, slots=True)
class AgentRequest:
    """Petición a un agente: todo lo que necesita el proveedor para ejecutarla.

    `system_prompt` es opcional y va al final para no romper construcciones
    existentes (el dataclass es `frozen=True, slots=True`, así que un campo
    nuevo con default al final es la única forma aditiva de extenderlo).

    Existe porque mezclar el prompt de rol (instrucciones fijas del Reader/
    Popularizer/Editor) con el prompt de usuario (que lleva el abstract, texto
    de un tercero no confiable) en un único bloque `prompt` es la superficie
    de inyección más ancha disponible: cualquier abstract que incluya algo
    con forma de instrucción compite en el mismo canal que las instrucciones
    reales del agente. Separar rol/instrucciones en `system_prompt` y datos
    del ítem en `prompt` es la mitigación más barata que hay. El paquete
    instalado (`claude-agent-sdk` 0.2.153) confirma que un `str` plano en
    `ClaudeAgentOptions.system_prompt` sustituye el system prompt entero del
    CLI (no lo añade a uno por defecto), que es justo el comportamiento que
    se quiere aquí.
    """

    role: AgentRole
    model: str
    prompt: str
    max_turns: int
    timeout_s: int
    item_id: UUID | None = None
    system_prompt: str | None = None


@dataclass(frozen=True, slots=True)
class AgentResult:
    """Resultado crudo de una llamada a un agente.

    `output_text` no se parsea aquí: la validación contra el esquema JSON
    esperado (`Reading`, los tres niveles del Popularizer, la lista del
    Editor) es responsabilidad de `application/`.
    """

    role: AgentRole
    model: str
    output_text: str
    tokens_in: int
    tokens_out: int
    duration_ms: int

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out


@runtime_checkable
class LLMProvider(Protocol):
    """Contrato que cumple cualquier proveedor capaz de ejecutar un agente."""

    async def run_agent(self, request: AgentRequest) -> AgentResult: ...
