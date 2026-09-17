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
    setting_sources=[],                    # **Explícito**: no cargar CLAUDE.md ni .claude/
    tools=[],                              # **Explícito**: no heredar herramientas del CLI
    skills=[],                             # **Explícito**: no heredar skills de .claude/
)

async for message in query(prompt=build_prompt(item), options=options):
    ...  # recoger el texto final y, del ResultMessage, tokens y modelo
```

Reglas del proyecto sobre esa llamada:

- **Una `query()` por ítem.** No se reutiliza sesión entre ítems. No `ClaudeSDKClient` con conversación larga.
- `max_turns` siempre desde configuración. Un agente que lee un abstract y devuelve JSON no necesita más de 2–3 turnos.
- `allowed_tools` mínimo. El Reader de fase 1 no necesita herramientas de ficheros (`Read`, `Write`, `Bash`): no se le dan.
- **`setting_sources=[]`, `tools=[]`, `skills=[]` son explícitos y no negociables** (ADR 0006). Dejarlos en `None` carga contexto de proyecto y herramientas del CLI, quemando 180.000–800.000 tokens **cada noche** de presupuesto.
- Salida estructurada: pedir JSON en el prompt y validar con Pydantic. Si el SDK ofrece salida estructurada nativa en la versión instalada, usarla; si no, parsear con tolerancia a fences ```` ```json ````.
- Tokens: leer del mensaje de resultado (`usage`, que es un `dict`), sumar `input_tokens + output_tokens` (incluyen tokens de caché: `cache_creation_input_tokens` y `cache_read_input_tokens` cuentan en `input_tokens`), y persistir en `AgentCall` con patrón `authorize → run_agent → record_call`.

## Subagentes del SDK vs. subagentes de desarrollo

- Los agentes del pipeline (Reader, Popularizer, Editor) se definen con `AgentDefinition` de forma programática o, más simple en fase 1, como tres `query()` independientes con distinto `system_prompt` y `model`. **Preferir lo segundo** salvo que el plan aprobado diga otra cosa: menos superficie, mismo control de gasto.
- `.claude/agents/` del repositorio son los subagentes de **desarrollo** con Claude Code. No se cargan en el pipeline.

## Bloqueo de presupuesto

- El control principal está en `BudgetGuard.check(role, estimated_tokens) → BudgetDecision` (consulta) y `BudgetGuard.authorize(role, estimated_tokens)` (barrera con excepción) antes de cada `query()`. `hard_stop` se verifica en `authorize`; si se ha cruzado, lanza excepción inmediatamente sin llamar al LLM.
- `BudgetGuard.timeout_for_call()` devuelve `min(item_timeout_s, segundos_hasta_hard_stop)`: ninguna llamada sobrevive a las 04:45.
- Ver ADR 0005 (control de gasto) y ADR 0006 (frontera del proveedor).

## Errores y timeouts

- Timeout por ítem (`limits.item_timeout_s`) con `asyncio.timeout()` alrededor de la iteración de `query()`.
- Fallo del SDK (proceso, red, límite alcanzado): las excepciones `LLMError` y `LLMTimeout` transportan `tokens_in` y `tokens_out` ya conocidos. Registrar ambos en `AgentCall` con el status correspondiente (`error`, `timeout`), y **no reintentar en bucle**. Un `429` o mensaje de límite de uso alcanzado termina la noche con `Run.status = partial`. Ver ADR 0006 § 4–5 para ruta de error completa.
