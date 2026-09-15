---
name: agent-sdk-usage
description: Cómo usar claude-agent-sdk (Python) en este proyecto — autenticación por suscripción, query() con opciones programáticas, MCP in-process, extracción de tokens. Invocar antes de escribir cualquier código que importe claude_agent_sdk.
---

# Uso del Claude Agent SDK en este proyecto

## Primero: verifica la documentación vigente

El SDK cambia con frecuencia. Antes de escribir código, lee la referencia de Python en `https://platform.claude.com/docs/en/agent-sdk/python` y confirma los nombres exactos de: `ClaudeAgentOptions`, `AgentDefinition`, campos de `ResultMessage` (uso de tokens) y `create_sdk_mcp_server`. Lo que sigue es la forma que el proyecto espera, no un sustituto de la referencia.

## Autenticación

- El SDK usa las credenciales del CLI de Claude Code instalado en la máquina. El autor hace `claude` y se loguea con su cuenta Max. **No** se configura `ANTHROPIC_API_KEY` (los hooks lo bloquean).
- Comprobación al arrancar el pipeline: si `ANTHROPIC_API_KEY` está en el entorno, `AgentSDKProvider` lanza un error y no arranca. Esto evita facturar por error contra una API key.

## Forma esperada de una llamada

```python
from claude_agent_sdk import query, ClaudeAgentOptions, AgentDefinition, create_sdk_mcp_server, tool

options = ClaudeAgentOptions(
    model=settings.models.reader,          # desde config/pipeline.toml
    max_turns=settings.limits.max_turns_per_agent,
    system_prompt=load_prompt("reader"),   # fichero .md, nunca inline
    mcp_servers={"arxiv": arxiv_server},   # in-process, create_sdk_mcp_server
    allowed_tools=["mcp__arxiv__get_abstract"],
    # setting_sources se deja en None: no cargamos CLAUDE.md ni .claude/ del proyecto
    # en el pipeline; ese contexto es para desarrollo, no para el análisis.
)

async for message in query(prompt=build_prompt(item), options=options):
    ...  # recoger el texto final y, del ResultMessage, tokens y modelo
```

Reglas del proyecto sobre esa llamada:

- **Una `query()` por ítem.** No se reutiliza sesión entre ítems. No `ClaudeSDKClient` con conversación larga.
- `max_turns` siempre desde configuración. Un agente que lee un abstract y devuelve JSON no necesita más de 2–3 turnos.
- `allowed_tools` mínimo. El Reader de fase 1 no necesita herramientas de ficheros (`Read`, `Write`, `Bash`): no se le dan.
- Salida estructurada: pedir JSON en el prompt y validar con Pydantic. Si el SDK ofrece salida estructurada nativa en la versión instalada, usarla; si no, parsear con tolerancia a fences ```` ```json ````.
- Tokens: leer del mensaje de resultado (`usage`), sumar input + output (+ cache si se reporta), y persistir en `AgentCall` **antes** de devolver el resultado al caso de uso.

## Subagentes del SDK vs. subagentes de desarrollo

- Los agentes del pipeline (Reader, Popularizer, Editor) se definen con `AgentDefinition` de forma programática o, más simple en fase 1, como tres `query()` independientes con distinto `system_prompt` y `model`. **Preferir lo segundo** salvo que el plan aprobado diga otra cosa: menos superficie, mismo control de gasto.
- `.claude/agents/` del repositorio son los subagentes de **desarrollo** con Claude Code. No se cargan en el pipeline.

## Hooks del SDK

- Para el `hard_stop` se puede usar un hook `PreToolUse` del SDK, pero el control principal está en `BudgetGuard.can_call` antes de cada `query()`; el hook es defensa en profundidad, no sustituto.

## Errores

- Timeout por ítem (`limits.item_timeout_s`) con `asyncio.wait_for` alrededor de la iteración de `query()`.
- Fallo del SDK (proceso, red, límite alcanzado): marcar `AgentCall.status = failed`, registrar el error, y **no reintentar en bucle**. Un `429` o mensaje de límite de uso alcanzado termina la noche con `Run.status = partial`.
