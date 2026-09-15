# Arquitectura — Nocturna

Documento vivo. Se edita por sección cuando cambia algo estructural; no acumula historial (para eso están `adr/` y git).

## Visión

Pipeline nocturno batch → PostgreSQL → web de solo lectura. El análisis corre contra la suscripción Claude Max del autor a través del Agent SDK; ver `adr/0001`.

## Componentes

| Componente | Tecnología | Estado |
|---|---|---|
| Ingesta arXiv | MCP in-process (`claude-agent-sdk`), `httpx` | pendiente (T20) |
| Control de gasto | `application/budget.py` | pendiente (T30) |
| Proveedor LLM | `infrastructure/llm/agent_sdk_provider.py` | pendiente (T40) |
| Agentes | Reader, Popularizer, Editor | pendiente (T41–T43) |
| Orquestador nocturno | `application/use_cases/run_night.py` | pendiente (T44) |
| API de lectura | FastAPI | pendiente (T50) |
| Web | Next.js | pendiente (T51) |

## Flujo de una noche

```
00:00  cron → nocturna run-night
       ├─ crea Run
       ├─ fetch_new(arXiv) → Items(new)               [sin LLM]
       ├─ por cada Item (≤ max_items): can_call? → Reader → Reading   [Sonnet]
       ├─ por cada Reading ≥ 4: can_call? → Popularizer → Finding    [Sonnet]
       ├─ can_call(editor, reserva)? → Editor (1 llamada) → publica  [Opus]
       └─ cierra Run (completed | partial | killed | failed)
04:45  hard_stop incondicional
```

## Capas

Dependencias: `api → application → domain ← infrastructure`. La capa `domain/` es pura (sin IO ni frameworks); `application/` orquesta casos de uso; `infrastructure/` implementa interfaces de dominio (repositorios, LLM); `api/` expone solo lectura. Ver skill `ddd-conventions` para detalle.

## Configuración

`config/pipeline.toml`, cargada en un objeto tipado. Secciones: `budget`, `limits`, `window`, `models`, `sources.arxiv`.

## Proveedor LLM

La interfaz `LLMProvider` en `domain/llm.py` define el contrato mínimo (`run_agent(agent, input) -> AgentResult`). Fase 1 usa `AgentSDKProvider` en `infrastructure/llm/agent_sdk_provider.py`, autenticado con el CLI de Claude Code sin `ANTHROPIC_API_KEY`. El hueco de `ApiKeyProvider` (alternativa seleccionable por configuración si la política de suscripción cambia) queda documentado e implementable en una tarea futura. Ver [ADR 0001](adr/0001-suscripcion-como-proveedor.md).

## Lo que no existe en fase 1 (a propósito)

Contrastador, Analista, `ApiKeyProvider`, autenticación, panel de administración, despliegue.
