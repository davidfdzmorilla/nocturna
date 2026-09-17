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
| Control de gasto | `application/budget.py` + `domain/clock.py` | done (T30) |
| Proveedor LLM | `infrastructure/llm/agent_sdk_provider.py` | done (T40) |
| Ejecutor de agentes | `application/agents/runner.py` | done (T42 refactor) |
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

## Agente Reader (fase 1)

Implementado en T41. Lee un `Item` con `status = new` y produce una `Reading` persistida. Definición programática en `application/agents/` con prompt de rol en `prompts/reader.md` (versionado).

**Contrato de entrada y salida**: `AgentRequest` con abstract del paper envuelto en tags `<abstract>` (mitigación de inyección de prompt), rol `reader`, modelo de configuración (Sonnet). Salida esperada: JSON validado por Pydantic que mapea a `ReadingOutput` con campos `summary`, `objects` (lista de nombres de objetos astronómicos), `claims` (lista de afirmaciones), `interest_score` (1–5 entero). Salida en dominio: entidad `Reading`.

**Parseo tolerante y reintento**: si el JSON no valida, se reintenta una sola vez dentro del `AgentRunner`. Si falla nuevamente, el ítem se marca `Item.FAILED` de forma terminal; se **no** reintenta al noche siguiente (el cliente MCP siempre da `FAILED`, no `NEW`). La validación de `Reading.__post_init__` rechaza cadenas en blanco, lo que quedó capturado solo por revisión manual en T41: `ReaderOutput` las aceptaba sin regla defensiva.

**Patrón de transacciones**: la secuencia de gasto vive en un punto único, `AgentRunner.run()` en `application/agents/runner.py`. Por intento son **tres unidades de trabajo**, y la llamada al modelo no está en ninguna: (1) `BudgetGuard.authorize` + `timeout_for_call()` + lectura del `run_id`, que cierra antes de llamar; (2) `record_call`, **sola**, sin ninguna otra escritura; (3) la persistencia de la entidad de dominio, que abre el **caso de uso** después de que `run()` devuelva — el runner no persiste entidades de negocio, solo `AgentCall`. Entre (1) y (2) ocurren `LLMProvider.run_agent` **sin ninguna transacción abierta** (cero conexiones retenidas mientras se espera al modelo, hasta `item_timeout_s`) y `build(result, run_id)`, que es una función pura: parsea la salida y construye la entidad, sin tocar la base de datos. Que `record_call` no comparta transacción con la persistencia es la lección de T41: si la compartieran, un `IntegrityError` tiraría por rollback la fila de una llamada ya cobrada, y el gasto quedaría hecho y olvidado. Que `build` se ejecute **dentro** del runner es la lección del bloqueante de T41: si lanza `InvalidAgentOutput` o `InvariantViolation`, el intento se contabiliza con `status = invalid_output` y se reintenta, de modo que ninguna llamada pagada puede terminar sin `AgentCall`. El reintento va en el bucle con `continue`, **nunca dentro de un `except`**, y cada intento pasa por su propio `authorize`: el segundo se pesa contra lo que ya gastó el primero, porque su `record_call` cerró antes.

**Prompt de rol en `system_prompt`**: el abstract del paper va en el `prompt` estándar envuelto en `<abstract>`/`</abstract>` (escaped por Pydantic al serializar). El rol (instrucciones del Reader) viaja en un campo nuevo `AgentRequest.system_prompt` separado, que el `AgentSDKProvider` pasa como `system_prompt` a `ClaudeAgentOptions`, no mezclado con el abstract. Así se evita confundir instrucciones con datos de terceros.

## Control de gasto

Implementado en T30 con `BudgetGuard` en `application/budget.py` y el reloj inyectable `domain/clock.py` (`infrastructure/clock.py`). **Toda llamada a un agente pasa por `BudgetGuard.authorize` antes de llegar a `LLMProvider`.**

La puerta verifica en orden: (1) ¿el `Run` está `RUNNING`?, (2) ¿estamos dentro de `[window.start, window.hard_stop)`?, (3) ¿el rol alcanzó su tope de llamadas?, (4) ¿hay presupuesto? El acumulado de gasto se lee desde `AgentCallRepository.tokens_used_for_run()` (base de datos), nunca de `Run.tokens_used` (caché desnormalizada, ADR 0003). Ningún contador vive en memoria: reinicio a media noche no desincroniza el acumulado.

**Reserva del Editor**: Reader y Popularizer ven `nightly_tokens - editor_reserve_tokens` desde la primera llamada de la noche. El Editor ve el presupuesto completo. La reserva no es "bajo demanda"; es incondicional.

**Ventana y `hard_stop`**: huso horario explícito en `config/pipeline.toml` (`window.timezone`, clave IANA). La resta de segundos hasta `hard_stop` usa UTC en ambos operandos, inmune a cambios de hora. `timeout_for_call()` devuelve `min(item_timeout_s, segundos_hasta_hard_stop)`: ninguna llamada sobrevive a `hard_stop`.

**Multiplicador de reset semanal**: la noche del reinicio de suscripción (día y hora en configuración), si `now.hour >= weekly_reset_hour`, el presupuesto se multiplica por `reset_day_multiplier`. El `Run` lee el valor efectivo al crearse (T44) y lo almacena en `run.budget_tokens`. La reserva del Editor no escala. Detalle completo en [ADR 0005](adr/0005-control-de-gasto.md).

## Proveedor LLM

Implementado en T40. La interfaz `LLMProvider` en `domain/llm.py` define el contrato: `async run_agent(request: AgentRequest) -> AgentResult`. El `AgentRequest` transporta `role`, `model`, `prompt`, `max_turns`, `timeout_s` e `item_id`; de esta forma ningún proveedor lee configuración global, y la firma async permite a T44 cancelar una llamada en vuelo al llegar `hard_stop`.

**Frontera arquitectónica** (congelada por `test_llm_call_sites.py`): el proveedor no conoce `BudgetGuard`, `config`, ni `db`. Todo llega resuelto en `AgentRequest`. El proveedor es estructuralmente incapaz de persistir; la secuencia `authorize → run_agent → record_call` es responsabilidad de un punto único centralizado. Esta separación permite reemplazar el proveedor (pasar a `ApiKeyProvider`) sin tocar la orquestación.

## Ejecutor de agentes

Implementado en T42 como refactor sobre T41. `application/agents/runner.py` contiene `AgentRunner`, **el único sitio del proyecto que llama a `LLMProvider.run_agent`** y, por tanto, el único camino por el que sale gasto. Lo comparten los tres agentes: Reader (migrado en el mismo commit), Popularizer y Editor. Su responsabilidad es la secuencia `authorize → run_agent → build → record_call`, con el gasto contabilizado en los seis desenlaces —éxito, salida inválida, error del proveedor, timeout, límite de tasa y cancelación— antes de devolver o de relanzar. La persistencia de la entidad queda fuera, en el caso de uso. Lo que se gana: el bloqueante de T41 —una llamada cobrada sin `AgentCall`— pasa a ser estructuralmente imposible para los tres agentes a la vez; desaparece la duplicación del ciclo de reintento; y la lista blanca de `test_llm_call_sites.py` vuelve a tener **una sola entrada** en lugar de crecer a tres. Esa guarda, conviene recordarlo, se evade con un alias: es un detector de descuidos, no una garantía.

**Contabilidad de tokens** ([ADR 0007](adr/0007-contabilidad-de-tokens-con-modelos-internos.md), supersede ADR 0006 § 3): la fuente de verdad es `ResultMessage.usage` (tokens del modelo pedido) y `model_usage` (costos de todos los modelos internos que el CLI usó). El gasto contabilizado es el **máximo componente a componente** entre:
- `usage.input_tokens + usage.output_tokens` (incluye caché: `cache_creation_input_tokens` + `cache_read_input_tokens`)
- Suma de `(inputTokens + outputTokens)` para cada entrada en `model_usage` dict

Fórmula: `tokens_in = max(usage.input_tokens, sum_model_usage_input)`, idem para output. **Por qué máximo**: `usage` reporta solo el modelo pedido; si el CLI usó modelos internos (Haiku para control de sesión, verificación), su gasto viaja en `model_usage` pero no en `usage`. Ambas fuentes reportan el mismo evento, así que máximo evita duplicación; suma sería contar dos veces.

**Datos observados en pruebas reales (2026-09-17)**: T40 registró una llamada con 523 tokens en `usage` pero 1.475 reales (946 Haiku + 529 Sonnet), subregistro de 35,9%. T41 registró `input_tokens: 2` + `cache_creation: 1269` + `output_tokens: 442` = 2.796 contabilizados; sin caché habrían sido 444, subregistro de 6,3×. Ambas ejecutan la política de máximo componente a componente (ADR 0007). Políticas de máximo y suma de `model_usage` viven en todos los sitios de extracción de tokens (T40 implementó, T41–T44 heredan).

`LLMError`/`LLMTimeout` transportan `tokens_in` y `tokens_out` ya conocidos (extraídos con este máximo); quien captura es responsable de contabilizar. `CancelledError` viene con atributos tras el primer `await`, pero un segundo `await` sobre cancelación externa puede dar un `CancelledError` nuevo **sin** atributos — T44 debe usar `getattr(exc, "tokens_in", 0)`. Sin `ResultMessage` (timeout de socket): no hay fuente de verdad, se contabiliza cero (fuga documentada en TECHNICAL_DEBT.md, acotada a ~600–800k tokens/noche en escenario de arXiv caído).

**Semántica de cancelación al contabilizar**: si `record_call` falla durante una cancelación por `hard_stop`, la misma instancia de `CancelledError` se propaga y su fallo se traga con `logging.warning`. Si la propia contabilización levantara una **nueva** `CancelledError` (distinta instancia del mismo tipo), se propaga la nueva y se pierden los tokens adjuntos a la original. El corte de las 04:45 sigue funcionando en ambos casos (T44 cancela), lo que varía es si se conserva en base de datos el gasto de esa última llamada. T41–T43 usan `try/except CancelledError` para capturar, contabilizar y relanzar la misma instancia; el tratamiento es defensivo.

**Cifras económicas** (por qué `setting_sources=[]`, `tools=[]`, `skills=[]` son no negociables):

- `setting_sources=None` (default del SDK) carga `CLAUDE.md`, `.claude/settings.json` y agentes de desarrollo en **cada** `query()`: ~3.500–5.000 tokens extras × ~51 llamadas/noche = **180.000–255.000 tokens/noche (60–85% del presupuesto), 5,5–7,7 millones/mes**. Una sola noche con esa carga quema el presupuesto completo. Solución: `setting_sources=[]` explícito en `ClaudeAgentOptions`.
- `tools=None` (default del SDK) hace que el CLI declare su toolset por defecto en cada llamada (herramientas del proyecto): **240.000–800.000 tokens/noche de preámbulo, equivalente a 1–3 noches de presupuesto**, cada una. Solución: `tools=[]` + `skills=[]` explícitos.

Fase 1 usa `AgentSDKProvider` en `infrastructure/llm/agent_sdk_provider.py`, autenticado con el CLI de Claude Code sin `ANTHROPIC_API_KEY`. El campo `llm.provider` en `pipeline.toml` está tipado como `Literal["agent_sdk"]` a propósito: impide un camino silencioso hacia una API key. Ese `Literal` se ampliará en la misma tarea que implemente `ApiKeyProvider`, reflejando una decisión consciente. Ver [ADR 0001](adr/0001-suscripcion-como-proveedor.md) y [ADR 0006](adr/0006-frontera-del-proveedor-y-contabilidad-de-tokens.md).

## Persistencia

Implementada en T11 con SQLAlchemy 2 (ORM sync), Alembic (migraciones) y PostgreSQL 16. El módulo `infrastructure/db/` contiene:

- **Modelos ORM** (`models.py`): representación de filas, sin métodos de negocio. Sin `relationship()` para evitar que SQLAlchemy se filtre hacia `application/`.
- **Mappers** (`mappers.py`): traducción manual dataclass ↔ ORM fila. El dominio se conserva limpio de SQLAlchemy.
- **Repositorios** (`repositories.py`): implementan los `Protocol` de `domain/repositories.py` de forma estructural (no por herencia).
- **Sesión y transacciones** (`session.py`): `unit_of_work` como único lugar que llama a `commit()` o `rollback()`. Garantiza que `AgentCall` y `Run.tokens_used` viajen juntos.

Las decisiones arquitectónicas (fronteras transaccionales, caché de gasto, índice único parcial, enums, tipos de columna) están documentadas en [ADR 0003](adr/0003-persistencia-tipos-y-fronteras-transaccionales.md). Punto crítico: **T30 lee `AgentCallRepository.tokens_used_for_run()` desde la base de datos para autorizar llamadas, nunca fiándose del campo `Run.tokens_used` en memoria** (es una caché desnormalizada).

## Modelo de dominio

Cinco entidades inmutables o mutables con guardas:

- **Item** (mutable): unidad de ingesta (abstract de arXiv). Estados: `new → {read, failed}` (ambos terminales). `read → {discarded, published}` (también terminal). `failed` se alcanza cuando el JSON de salida del Reader no valida dos intentos seguidos. Ningún estado permite volver atrás; repetir la misma transición lanza `InvalidTransition`. Solo se asigna `status` a través de métodos `mark_read()`, `fail()`, `discard()`, `publish()`.
- **Reading** (inmutable): salida del Reader para un Item. Contiene `summary`, `objects`, `claims`, `interest_score` (1–5), tokens y modelo. No tiene `created_at`: es un hecho, no una entidad con ciclo de vida.
- **Finding** (mutable): candidato de hallazgo con `title` y tres niveles de lectura. Solo `confidence` y `published_at` se asignan en el método `publish()`, juntos o no en absoluto. Antes de `publish()`, ambos son `None`.
- **Run** (mutable): una ejecución nocturna. Estados: `running` → {`completed`, `partial`, `failed`, `killed`}. El campo `budget_tokens` es el presupuesto efectivo (ya con el multiplicador del día). Los contadores `items_fetched`, `items_read`, `findings_published` son monótonos no decrecientes (se permite asignar un valor ≥ al actual, nunca menor). Solo se asignan `status`, `finished_at`, `tokens_used` a través del método `finish()` y `record_agent_call()`.
- **AgentCall** (inmutable): registro de una llamada ya ocurrida, con `tokens_in`, `tokens_out`, `duration_ms`, `status` (ok / invalid_output / error / timeout) y `prompt_version` (cadena de versión del prompt usado, para trazabilidad futura).

**Implementación en dataclasses (estándar de Python)**, no Pydantic: las reglas de negocio fallan con excepciones de dominio (`InvalidTransition`, `InterestScoreOutOfRange`), no con errores de validación. Los `__post_init__` y `__setattr__` protegen invariantes; quien necesita cambiar un campo guarded usa `object.__setattr__` internamente. Todos los `datetime` son *aware* (con `tzinfo`); el dominio no convierte zonas horarias, solo rechaza naive.

Pydantic se usa solo en las fronteras: `infrastructure/config.py` para el TOML tipado, y `application/agents/` para validar y estructurar el JSON que sale de los agentes antes de construir la entidad de dominio.

**Nota sobre hermeticidad de las guardas**: `object.__setattr__`, el descriptor de slot en clase, y `dataclasses.replace()` pueden rodear `__setattr__`. La defensa real contra una asignación manipulada de `tokens_used` en memoria es que T30 lea el acumulado con `AgentCallRepository.tokens_used_for_run()` desde la base de datos, nunca fiándose del valor del objeto en memoria.

## Lo que no existe en fase 1 (a propósito)

Contrastador, Analista, `ApiKeyProvider`, autenticación, panel de administración, despliegue.
