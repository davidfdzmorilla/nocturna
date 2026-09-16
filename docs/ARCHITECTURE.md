# Arquitectura — Nocturna

Documento vivo. Se edita por sección cuando cambia algo estructural; no acumula historial (para eso están `adr/` y git).

## Visión

Pipeline nocturno batch → PostgreSQL → web de solo lectura. El análisis corre contra la suscripción Claude Max del autor a través del Agent SDK; ver `adr/0001`.

## Componentes

| Componente | Tecnología | Estado |
|---|---|---|
| Configuración tipada | Pydantic Settings (`infrastructure/config.py`) | done (T02) |
| Modelo de dominio | dataclasses `domain/` | done (T10) |
| Persistencia | SQLAlchemy 2 + Alembic + PostgreSQL 16 (`infrastructure/db/`) | done (T11) |
| Ingesta arXiv | MCP in-process (`claude-agent-sdk`), `httpx` | done (T20) |
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

`config/pipeline.toml`, cargada en un objeto tipado por `infrastructure/config.py`. Secciones: `budget`, `limits`, `window`, `models`, `llm`, `sources.arxiv`.

## Ingesta de arXiv

Implementada en T20. La lógica vive en `application/use_cases/ingest_arxiv.py` (`IngestArxiv`) y `infrastructure/arxiv/` (cliente HTTP). El servidor MCP (`infrastructure/mcp/arxiv_server.py`) expone dos herramientas sin persistir: adaptador fino para que los agentes (T41+) puedan invocar `fetch_new` y `get_abstract`. Detalle arquitectónico en [ADR 0004](adr/0004-ingesta-de-arxiv-y-mcp-como-adaptador.md).

`cli.py` es el composition root: único sitio que abre `unit_of_work`, instancia `ArxivClient` e invoca `IngestArxiv` dentro de la transacción. T20 introduce el subcomando `nocturna run-night --dry-run` que ingesta sin llamar a agentes.

## Proveedor LLM

La interfaz `LLMProvider` en `domain/llm.py` define el contrato: `async run_agent(request: AgentRequest) -> AgentResult`. El `AgentRequest` transporta `role`, `model`, `prompt`, `max_turns`, `timeout_s` e `item_id`; de esta forma ningún proveedor lee configuración global, y la firma async permite a T44 cancelar una llamada en vuelo al llegar `hard_stop`.

Fase 1 usa `AgentSDKProvider` en `infrastructure/llm/agent_sdk_provider.py`, autenticado con el CLI de Claude Code sin `ANTHROPIC_API_KEY`. El campo `llm.provider` en `pipeline.toml` está tipado como `Literal["agent_sdk"]` a propósito: impide un camino silencioso hacia una API key. Ese `Literal` se ampliará en la misma tarea que implemente `ApiKeyProvider`, reflejando una decisión consciente. Ver [ADR 0001](adr/0001-suscripcion-como-proveedor.md).

## Persistencia

Implementada en T11 con SQLAlchemy 2 (ORM sync), Alembic (migraciones) y PostgreSQL 16. El módulo `infrastructure/db/` contiene:

- **Modelos ORM** (`models.py`): representación de filas, sin métodos de negocio. Sin `relationship()` para evitar que SQLAlchemy se filtre hacia `application/`.
- **Mappers** (`mappers.py`): traducción manual dataclass ↔ ORM fila. El dominio se conserva limpio de SQLAlchemy.
- **Repositorios** (`repositories.py`): implementan los `Protocol` de `domain/repositories.py` de forma estructural (no por herencia).
- **Sesión y transacciones** (`session.py`): `unit_of_work` como único lugar que llama a `commit()` o `rollback()`. Garantiza que `AgentCall` y `Run.tokens_used` viajen juntos.

Las decisiones arquitectónicas (fronteras transaccionales, caché de gasto, índice único parcial, enums, tipos de columna) están documentadas en [ADR 0003](adr/0003-persistencia-tipos-y-fronteras-transaccionales.md). Punto crítico: **T30 lee `AgentCallRepository.tokens_used_for_run()` desde la base de datos para autorizar llamadas, nunca fiándose del campo `Run.tokens_used` en memoria** (es una caché desnormalizada).

## Modelo de dominio

Cinco entidades inmutables o mutables con guardas:

- **Item** (mutable): unidad de ingesta (abstract de arXiv). Estados terminales: `new → read → {discarded, published}`. Ningún estado permite volver atrás; repetir la misma transición lanza `InvalidTransition`. Solo se asigna `status` a través de métodos `mark_read()`, `discard()`, `publish()`.
- **Reading** (inmutable): salida del Reader para un Item. Contiene `summary`, `objects`, `claims`, `interest_score` (1–5), tokens y modelo. No tiene `created_at`: es un hecho, no una entidad con ciclo de vida.
- **Finding** (mutable): candidato de hallazgo con `title` y tres niveles de lectura. Solo `confidence` y `published_at` se asignan en el método `publish()`, juntos o no en absoluto. Antes de `publish()`, ambos son `None`.
- **Run** (mutable): una ejecución nocturna. Estados: `running` → {`completed`, `partial`, `failed`, `killed`}. El campo `budget_tokens` es el presupuesto efectivo (ya con el multiplicador del día). Los contadores `items_fetched`, `items_read`, `findings_published` son monótonos no decrecientes (se permite asignar un valor ≥ al actual, nunca menor). Solo se asignan `status`, `finished_at`, `tokens_used` a través del método `finish()` y `record_agent_call()`.
- **AgentCall** (inmutable): registro de una llamada ya ocurrida, con `tokens_in`, `tokens_out`, `duration_ms` y `status` (ok / invalid_output / error / timeout).

**Implementación en dataclasses (estándar de Python)**, no Pydantic: las reglas de negocio fallan con excepciones de dominio (`InvalidTransition`, `InterestScoreOutOfRange`), no con errores de validación. Los `__post_init__` y `__setattr__` protegen invariantes; quien necesita cambiar un campo guarded usa `object.__setattr__` internamente. Todos los `datetime` son *aware* (con `tzinfo`); el dominio no convierte zonas horarias, solo rechaza naive.

Pydantic se usa solo en las fronteras: `infrastructure/config.py` para el TOML tipado, y `application/agents/` para validar y estructurar el JSON que sale de los agentes antes de construir la entidad de dominio.

**Nota sobre hermeticidad de las guardas**: `object.__setattr__`, el descriptor de slot en clase, y `dataclasses.replace()` pueden rodear `__setattr__`. La defensa real contra una asignación manipulada de `tokens_used` en memoria es que T30 lea el acumulado con `AgentCallRepository.tokens_used_for_run()` desde la base de datos, nunca fiándose del valor del objeto en memoria.

## Lo que no existe en fase 1 (a propósito)

Contrastador, Analista, `ApiKeyProvider`, autenticación, panel de administración, despliegue.
