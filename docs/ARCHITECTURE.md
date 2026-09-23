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
| Agente Reader | `application/agents/reader`, `application/use_cases/read_item.py` | done (T41) |
| Agente Popularizer | `application/agents/popularizer`, `application/use_cases/popularize_reading.py` | done (T42) |
| Agente Editor | `application/agents/editor`, `application/use_cases/edit_night.py` | done (T43) |
| Orquestador nocturno | `application/use_cases/run_night.py` | done (T44) |
| API de lectura | FastAPI | done (T50) |
| Web | Next.js | done (T51) |

## Flujo de una noche

```
00:00  máquina despierta (pmset)
00:05  launchd → run-night-scheduled.sh (envoltorio)
         ├─ fija PATH, valida configuración
         ├─ comprueba Docker, PostgreSQL salud
         ├─ centinela ~/.launched-YYYYMMDD (previene dobles)
         └─ invoca: caffeinate -is uv run --frozen nocturna run-night
              ├─ cierra `Run` huérfano si existe
              ├─ crea `Run` nuevo
              ├─ fetch_new(arXiv) → Items(new)                   [sin LLM, Fase A]
              ├─ por cada Item (≤ max_items): authorize? → Reader → Reading   [Sonnet, Fase A]
              ├─ por cada Reading ≥ umbral: authorize? → Popularizer → Finding  [Sonnet, Fase B]
              ├─ authorize(editor, reserva)? → Editor (1 llamada, todos candidatos)  [Opus, Fase C]
              └─ cierra Run (COMPLETED | PARTIAL | KILLED | FAILED)
04:45  hard_stop incondicional (vigía asyncio + comprobación en cada authorize)
```

**Qué aporta el envoltorio**: configuración del PATH (para que `uv` y `claude` sean resolubles), validación de precondiciones (Docker, PostgreSQL), centinela (única invocación por ventana), rotación de logs en ficheros separados. **Sin** lógica de ventana ni presupuesto, que siguen siendo exclusivas de `application/budget.py`.

**Degradación de estado**: `RunNight` propone estados en orden de severidad decreciente (COMPLETED < PARTIAL < KILLED). Un desenlace de fase se transfiere al siguiente: si fase A devuelve PARTIAL (Reader no completó), fase B hereda PARTIAL y solo puede bajar a KILLED por timeout. Si fase C devuelve PARTIAL (no hay presupuesto para Editor), el Run cierra PARTIAL, pero no se pierde lo publicado de las fases anteriores (eso es decisión del Editor).

**Mapa de desenlaces**: cada fase genera un desenlace que `_run_night_for_real` traduce a `RunStatus`. COMPLETED (noche íntegra) sale solo de fase C. Las fases A y B generan PARTIAL o KILLED; el Editor cierra con COMPLETED o PARTIAL. FAILED sale de una excepción inesperada.

**Códigos de salida**: 0 = COMPLETED, 1 = PARTIAL/FAILED/KILLED, 7 = timeout de ejecución, 8 = error crítico.

**Doble defensa del `hard_stop`**: vigía asyncio cancela en vuelo, comprobación de segundos restantes en cada autorización rechaza nuevo gasto.

## Capas

Dependencias: `api → application → domain ← infrastructure`. La capa `domain/` es pura (sin IO ni frameworks); `application/` orquesta casos de uso; `infrastructure/` implementa interfaces de dominio (repositorios, LLM); `api/` expone solo lectura. Ver skill `ddd-conventions` para detalle.

## Configuración

`config/pipeline.toml`, cargada en un objeto tipado por `infrastructure/config.py`. Secciones: `budget`, `limits`, `window`, `models`, `llm`, `sources.arxiv`.

## Ingesta de arXiv

Implementada en T20. La lógica vive en `application/use_cases/ingest_arxiv.py` (`IngestArxiv`) y `infrastructure/arxiv/` (cliente HTTP). El servidor MCP (`infrastructure/mcp/arxiv_server.py`) expone dos herramientas sin persistir: adaptador fino para que los agentes (T41+) puedan invocar `fetch_new` y `get_abstract`. Detalle arquitectónico en [ADR 0004](adr/0004-ingesta-de-arxiv-y-mcp-como-adaptador.md).

**Reintento de fallos transitorios (T60.b)**: `infrastructure/arxiv/retry.py` implementa `Retrier` con backoff exponencial y jitter ante fallos transitorios de arXiv (406, 429, 5xx, errores de transporte). Política configurable desde `pipeline.toml` bajo `sources.arxiv`: `retry_max_attempts` (intentos), `retry_base_delay_s` (base de espera, se dobla cada intento), `retry_max_elapsed_s` (tope duro total). Eventos de log estructurados: `arxiv.retry` (warning al reintentar), `arxiv.retry_recovered` (info al recuperarse), `arxiv.retry_exhausted` (error al agotarse). La ingesta es **cancelable** (usa `anyio.sleep`, no `time.sleep`) y respeta `hard_stop` porque `Retrier` se ejecuta dentro de la tarea que el vigía de T44 cancela.

`cli.py` es el composition root: único sitio que abre `unit_of_work`, instancia `ArxivClient` e invoca `IngestArxiv` dentro de la transacción. T20 introduce el subcomando `nocturna run-night --dry-run` que ingesta sin llamar a agentes. **La ventana y el presupuesto de gasto de tokens siguen siendo exclusivos de `application/budget.py`**: la ingesta no pasa por `BudgetGuard` porque no gasta tokens de suscripción a Claude; el único límite es la política de cortesía de arXiv (3 s entre peticiones, constante de módulo no configurable) y los timeouts configurables de `Retrier`.

## Agente Reader (fase 1)

Implementado en T41. Lee un `Item` con `status = new` y produce una `Reading` persistida. Definición programática en `application/agents/` con prompt de rol en `prompts/reader.md` (versionado).

**Contrato de entrada y salida**: `AgentRequest` con abstract del paper envuelto en tags `<abstract>` (mitigación de inyección de prompt), rol `reader`, modelo de configuración (Sonnet). Salida esperada: JSON validado por Pydantic que mapea a `ReadingOutput` con campos `summary`, `objects` (lista de nombres de objetos astronómicos), `claims` (lista de afirmaciones), `interest_score` (1–5 entero). Salida en dominio: entidad `Reading`.

**Parseo tolerante y reintento**: si el JSON no valida, se reintenta una sola vez dentro del `AgentRunner`. Si falla nuevamente, el ítem se marca `Item.FAILED` de forma terminal; se **no** reintenta al noche siguiente (el cliente MCP siempre da `FAILED`, no `NEW`). La validación de `Reading.__post_init__` rechaza cadenas en blanco, lo que quedó capturado solo por revisión manual en T41: `ReaderOutput` las aceptaba sin regla defensiva.

**Patrón de transacciones**: la secuencia de gasto vive en un punto único, `AgentRunner.run()` en `application/agents/runner.py`. Por intento son **tres unidades de trabajo**, y la llamada al modelo no está en ninguna: (1) `BudgetGuard.authorize` + `timeout_for_call()` + lectura del `run_id`, que cierra antes de llamar; (2) `record_call`, **sola**, sin ninguna otra escritura; (3) la persistencia de la entidad de dominio, que abre el **caso de uso** después de que `run()` devuelva — el runner no persiste entidades de negocio, solo `AgentCall`. Entre (1) y (2) ocurren `LLMProvider.run_agent` **sin ninguna transacción abierta** (cero conexiones retenidas mientras se espera al modelo, hasta `item_timeout_s`) y `build(result, run_id)`, que es una función pura: parsea la salida y construye la entidad, sin tocar la base de datos. Que `record_call` no comparta transacción con la persistencia es la lección de T41: si la compartieran, un `IntegrityError` tiraría por rollback la fila de una llamada ya cobrada, y el gasto quedaría hecho y olvidado. Que `build` se ejecute **dentro** del runner es la lección del bloqueante de T41: si lanza `InvalidAgentOutput` o `InvariantViolation`, el intento se contabiliza con `status = invalid_output` y se reintenta, de modo que ninguna llamada pagada puede terminar sin `AgentCall`. El reintento va en el bucle con `continue`, **nunca dentro de un `except`**, y cada intento pasa por su propio `authorize`: el segundo se pesa contra lo que ya gastó el primero, porque su `record_call` cerró antes.

**Prompt de rol en `system_prompt`**: el abstract del paper va en el `prompt` estándar envuelto en `<abstract>`/`</abstract>` (escaped por Pydantic al serializar). El rol (instrucciones del Reader) viaja en un campo nuevo `AgentRequest.system_prompt` separado, que el `AgentSDKProvider` pasa como `system_prompt` a `ClaudeAgentOptions`, no mezclado con el abstract. Así se evita confundir instrucciones con datos de terceros.

## Agente Popularizer (fase 1)

Implementado en T42. Lee una `Reading` con `interest_score >= min_interest_score` (umbral configurable, por defecto 4) y produce una `Finding` persistida sin publicar. Definición programática en `application/agents/` con prompt de rol en `prompts/popularizer.md`.

**Contrato de entrada y salida**: `AgentRequest` con `Reading` envuelto en tags `<reading>`, rol `popularizer`, modelo Sonnet. Salida esperada: JSON validado por `PopularizerOutput` con tres campos de texto libre — `level_curious` (~100 palabras), `level_amateur` (~200 palabras), `level_technical` (~300 palabras) — correspondientes a tres audiencias y direccionado a lectores sin contexto de astronomía. Salida en dominio: entidad `Finding` con `title` (resumido del `Reading.summary`) y los tres niveles.

**Umbral como palanca de gasto**: `interest_score < min_interest_score` retorna **antes de cualquier `authorize`**, sin gastar nada, y es la decisión que acota cuántas llamadas al Popularizer hay por noche. `min_interest_score` se lee de `limits.popularizer_min_interest_score` en `pipeline.toml`, no es literal, de modo que T60 puede calibrar el tope de gasto por noche sin tocar código.

**Transiciones asimétricas de estado**: una lectura exitosa (`Reading` creada) establece `Item.status = READ` y es irreversible; desde aquí divergen los caminos. Si el Popularizer produce JSON válido (`Finding` creado), la lectura pasa a `POPULARIZED` (cambio de estado en `Item`). Si el Popularizer falla por `TIMEOUT`, `ERROR` o `RATE_LIMITED`, la lectura vuelve a `READ` (reintentable otra noche). Si el Popularizer retorna JSON inválido o si el `interest_score` estaba por debajo del umbral, el `Item` es `DISCARDED` (terminal, nunca reintentable). Esta asimetría es el equivalente del «ítem envenenado» de T41: sin `DISCARDED`, T44 reintentaría cada noche un ítem que el `interest_score` rechazó, quemando presupuesto indefinidamente. Nota: `SKIPPED_LOW_SCORE` en el prompt del Popularizer desencadena `DISCARDED` en base de datos.

**Aritmética de la noche medida (2026-09-17)**:
- Presupuesto lector + popularizer = 240.000 tokens
- Reader: 40 ítems × 3.056 tokens/ítem (datos reales T41) = 122.240 tokens
- Quedan: 240.000 − 122.240 = 117.760 tokens
- Popularizer medido: ~4.560 tokens/candidato sin reintento (T42, intento 1 falló por JSON inválido; intento 2 ~4.521)
- Candidatos esperados: 117.760 ÷ 4.560 ≈ **25,8 → ~26** (presupuesto puro)
- **Tasa de reintento**: una observación de dos intentos (1º rechazado por saltos de línea en literal JSON, 2º válido) no es suficiente para saber si es sistemática. Si todos reintentan: 117.760 ÷ (4.560 × 2) ≈ **~13 candidatos**. Diferencia crítica: 26 vs 13. Calibración: T60 mide varios días y documenta si `popularizer-v2` resuelve la tasa.
- **Reparador de JSON**: `extract_json_object` repara caracteres de control crudos (`\n`, `\r`, `\t`) dentro de literales de cadena, solo después de que `json.loads` falle. Costo: cero tokens (no se llama a modelo). Garantía: **no-op sobre cualquier JSON que `json.loads` ya acepte**, porque el JSON válido exige comillas balanceadas y prohíbe esos caracteres crudos dentro de cadenas — exactamente lo único que toca. Filosofía: el reparador es una red de seguridad, no una excusa; prompts versión 2+ buscan salida limpia al primer intento.
- **Degradación elegante**: guard deniega en límite de presupuesto. La noche se degrada en cantidad, no en corrección (los candidatos se leen bien, solo hay menos).

## Agente Editor (fase 1)

Implementado en T43. Orquestador de publicación. Recibe todos los `Finding` candidatos de la noche en **una sola conversación** (`N:1`); devuelve lista de `item_id` a publicar, cada uno con `confidence` (0–1) y motivo (log, no persistido). Caso de uso `EditNight` que publica los aprobados y cierra el `Run` como `COMPLETED`.

**Contrato de entrada y salida**: `AgentRequest` con lista de candidatos (`item_id`, `title`, `level_curious` solamente, no los tres niveles), rol `editor`, modelo Opus. Salida esperada: JSON validado por `EditorOutput` con lista de decisiones `item_id / publish / confidence / reason`. Editor no recibe `level_amateur` ni `level_technical` (reducir entrada ~3.5×) pero su salida publica solo los aprobados, que ya llevan los tres niveles generados por Popularizer.

**Estimación lineal de coste**: `editor_base_tokens + (num_candidates × editor_tokens_per_candidate)`. Configuración de `pipeline.toml` (cfg-2026-09-18): `editor_base_tokens = 2500`, `editor_tokens_per_candidate = 850`. Validación cerrada al cargar: `base + max_items_per_night × per_candidate ≤ editor_reserve_tokens` (2.500 + 40×850 = 36.500 ≤ 60.000). Si el invariante se rompe, la configuración falla de día, no en plena noche. **Nota:** el § 2 (estimación lineal) de [ADR 0008](adr/0008-el-editor-llamada-n1-y-estimacion-de-coste.md) quedó superado por `cfg-2026-09-18` tras observación real (T60); los valores vigentes son 2500 + 850×N, no 4000 + 700×N. Ver `docs/CALIBRACION.md` para detalles de la calibración.

**Timeout rol-dependiente**: el Editor tiene 300 segundos (5 min), frente a 180 de Reader/Popularizer. La invariante de `hard_stop` se preserva: ambos están limitados por `min(..., seconds_until_hard_stop())`. Ver ADR 0005 § 10 extensión (T43).

**Candidatos huérfanos**: si el Editor falla (JSON inválido, timeout, error), sus `Finding` quedan sin `published_at` ni `confidence`. Los `Item` correspondientes quedan en `READ`. La noche se cierra `PARTIAL`. Ninguna noche futura los reintenta: `unpublished_for_run` filtra por `run_id`. Es característica correcta de fase 1.

**`_edit_one_night` es el único cierre de `COMPLETED`**: Reader y Popularizer devuelven su desenlace parcial; solo `_edit_one_night` traduce un desenlace exitoso del Editor a `RunStatus.COMPLETED`. Corrige la deuda de T42. El Editor es la última etapa de la noche y tiene poder unilateral de cerrar. Si falla (JSON inválido, timeout, gasto), ningún `Finding` se publica y la noche cierra PARTIAL; si no hay gasto ni fallo, aún puede no publicar nada si todos los candidatos son rechazados por criterio editorial.

## Orquestador nocturno (T44)

Implementado en T44. `RunNight` en `application/use_cases/run_night.py` corre las tres fases en orden (Ingesta, Reader, Popularizer, Editor). Cada fase se autoriza antes de ejecutar y reporta un desenlace (`COMPLETED`, `PARTIAL`, `KILLED`). `_run_night_for_real` en `cli.py` instancia `RunNight`, invoca `run()`, recibe el desenlace final y cierra el `Run` con su `status`, contadores y métricas.

**Política de `Run` huérfano**: al iniciar, `_current_or_new_run_night` comprueba si existe un `Run` en `RUNNING` del inicio de hoy (entre `window.start` y ahora). Si existe, lo cierra como `KILLED` (interpretación: interrupción de noche anterior) y abre uno nuevo. Si no existe, abre uno nuevo. Este comportamiento es específico de `run-night`; `run-item` mantiene el camino anterior (adoptar un `RUNNING` vivo para depuración).

**Validación inicial**: `_build_run_night` comprueba que `run.budget_tokens == effective_nightly_tokens(policy, now)` (ley congelada por test), validador de reserva presupuestaria confirma que la estimación del Editor cabe, y `_deadline_for_run_night` calcula segundos hasta `hard_stop`.

**Logging estructurado**: cada línea de log es JSON (line 1 = inicio, línea 2 = por ítem, línea N = cierre con métricas). El flujo usa la utilidad `configure_json_logging()` de `infrastructure/logging.py` que redirige a `stderr` con `default=str` y fallback a línea mínima si la serialización revienta.

**Vigía de `hard_stop`**: `_deadline_for_run_night` retorna un `Deadline` con segundos restantes. Dentro de `RunNight.run()` hay un `asyncio.timeout()` que cancela en vuelo si expira. Además, cada llamada a `authorize()` comprueba `seconds_until_hard_stop() > 0` y rechaza si la ventana se cerró. La doble defensa garantiza que ninguna llamada sobrevive a las 04:45.

**Desenlaces de fase y degradación**: Fase A (Ingesta + Reader) devuelve `COMPLETED` si ingesta OK y `items_read >= 1`, `PARTIAL` si Reader agota presupuesto, `KILLED` si `hard_stop` interrumpe. Fase B (Popularizer) hereda el desenlace y puede bajar a `PARTIAL` si agota presupuesto, `KILLED` por timeout. Fase C (Editor) recibe candidatos de fase B; si los contadores muestran `hard_stop`, rechaza sin llamar; si hay gasto, devuelve `COMPLETED` (Editor publicó algo) o `PARTIAL` (Editor rechazó todo o no hay gasto); si falla (JSON, error), devuelve `PARTIAL`. El desenlace de la noche es el máximo (monotonía descendente: `COMPLETED > PARTIAL > KILLED`).

**Cortacircuitos**: `max_consecutive_failures = 5` (Reader o Popularizer per phase): tras 5 fallos monótonos en la misma fase, se cierra esa fase con `PARTIAL` y se sigue (fuga ~50.000 tokens, acotada). El contador se reinicia al cambiar de fase, de modo que una noche con fallos alternos sigue limitada por `CALL_LIMIT_REACHED` (80 llamadas/rol).

**Reconciliación de tokens**: `run.budget_tokens` fija el presupuesto efectivo al crear el `Run`. Después del cierre, no se toca `run.tokens_used` (campo desnormalizado); el informe de T60 suma `AgentCall` de ese `Run` usando `AgentCallRepository.tokens_used_for_run()`.

**Nota sobre presupuesto agotado en fase B**: `CLAUDE.md` afirma «si el presupuesto se agota antes del Editor… no se publica nada esa noche». Esto es cierto: Reader y Popularizer comparten un pool de presupuesto. Si ese pool se agota en fase B (Popularizer), ningún candidato se crea, fase C no se invoca, no se publica nada. **Pero** si el presupuesto se agota durante fase C (Editor), los candidatos de fase B que ya se crearon se descartan (no se publican). La reserva del Editor es un suelo separado: agotar la reserva de Reader/Popularizer no impide que el Editor intente, pero si no hay presupuesto en su reserva tampoco se publica. No es contradicción: el mecanismo es correcto, pero un lector futuro podría malinterpretar la frase de `CLAUDE.md`. Ver ADR 0005 § 8 para detalle.

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

## API de lectura (fase 1)

Implementada en T50. FastAPI con tres endpoints expuestos:

- `GET /health`: comprobación de vivacidad. Devuelve `200` si el servicio y PostgreSQL responden. `503` si la base de datos no está disponible. Contrato: `{"status": "ok"}`.
- `GET /findings?page=<int>&size=<int>`: feed paginado de hallazgos publicados. `page` base 1 (por defecto 1), `size` (1–50, por defecto 20). Respuesta: `{"items": [<finding>, ...], "page": <int>, "size": <int>, "total": <int>}`. Página fuera de rango devuelve `200` con `items` vacía. `total` es `COUNT` exacto, económico a este volumen.
- `GET /findings/{id}`: detalle de un hallazgo publicado. Devuelve el objeto completo. `404` si no existe o no está publicado (indistinguible por diseño). El mismo `404` para «no existe» y «no publicado» impide enumerar candidatos que el Editor rechazó.

**Contrato de hallazgo (`Finding`)**: `id`, `title`, `level_curious`, `level_amateur`, `level_technical`, `published_at`, `item_id` (enlace a arXiv). **Campos ocultos**: `confidence` (es una nota editorial interna, no una métrica científica; expuesta junto a texto generado por IA se malinterpretaría como "grado de certeza científica", contradictorio con el banner obligatorio), `run_id`, `type`. La privacidad de `confidence` protege la semántica del análisis automático.

**Filtro de publicación**: `findings.published_at IS NOT NULL`, única fuente de verdad. Sin cruce con `items.status`: un hallazgo es «publicado» si tiene timestamp, punto. Refaldado por constraint `CHECK (confidence IS NULL) = (published_at IS NULL)` — ambos campos se asignan juntos o no en absoluto, en el método `Finding.publish()`.

**Implementación técnica**: la sesión de base de datos **nunca hace `commit()`**, solo `rollback()` al cerrar. Es la barrera dura contra escritura: `unit_of_work` es la frontera transaccional del pipeline (Reader, Popularizer, Editor confirman); la API no debe poder confirmar nada, aunque sea por accidente. La guarda AST `test_api_read_only.py` detecta `commit()` y `__setattr__` de entidades, pero es un detector de descuidos evasible; la verdadera defensa es arquitectónica: no abre contexto que pueda cambiar datos.

**CORS**: `Settings.cors_origins`, por defecto `["http://localhost:3000"]`. Solo `GET`, nunca `*`. Configurable desde `NOCTURNA_CORS_ORIGINS` (variable de entorno con formato JSON: `'["http://a","http://b"]'`).

**Errors**: `500` devuelve cuerpo fijo `{"detail": "internal error"}`, la traza va solo a `stderr`. `404` a nivel de endpoint no devuelto; solo los especificados arriba.

**Arranque local**: `cd backend && uv run uvicorn nocturna.api.app:create_app --factory --reload --port 8000`. El parámetro `--factory` invoca `create_app()` que devuelve `FastAPI()`. Settings cacheadas con `lru_cache` en `deps.py`.

**Script de siembra**: `backend/scripts/seed_demo.py` (fuera del paquete instalable) siembra tres `Finding` de prueba publicados por el mismo camino de dominio que el Editor (y con la misma guarda: `NOCTURNA_ALLOW_SEED=1`). No es idempotente: segundo lanzamiento falla con `IntegrityError` sin corromper nada (todo dentro de transacción de escritura).

## Web (fase 1)

Implementada en T51. Next.js 15.5.25 con App Router, React 19.1.0, Tailwind 4.3.3. Dos rutas públicas de solo lectura:

- `/` feed paginado (`?page=1&level=curious`) de hallazgos publicados, orden descendente por `published_at`, desempate por `id`. `page` base 1 (default 1), `size` 1–50 (default 20). Respuesta: objeto `{items: [...], page, size, total}`. Página fuera de rango → `200` con lista vacía, aviso visual al usuario.
- `/hallazgo/[id]` detalle de un hallazgo publicado. Selector de nivel por parámetro URL `?nivel=curious|amateur|technical` (default `curious`), **sin estado de cliente**: cada nivel es una URL compartible, legible sin JavaScript. Enlace a arXiv original del paper. `404` si no existe o no publicado (indistinguible por diseño).

**Decisiones estructurales**:
- **SSR dinámico con `force-dynamic` + `cache: "no-store"`**, no ISR. Razón: hallazgos se publican en bloque de madrugada y no cambian hasta la siguiente noche, ISR solo aportaría latencia a cambio de razonar sobre el *full route cache* de Next (si la API está caída al revalidar, se cachea la página degradada) y prerenderizar `/` en build ataría el build a tener API y PostgreSQL levantadas. Con SSR dinámico, `pnpm build` toca solo el código (verificado: `grep` de `localhost:8000` y `NOCTURNA_API_URL` no aparecen en `.next/static`). Reversible en una línea (`revalidate = 300`).
- **Frontera de IO única**: `src/lib/api/client.ts` es el único módulo que hace `fetch`. Mapea `404` → `not_found()` y todo lo demás → `unavailable`, nunca devuelve al llamante el cuerpo del servidor, el código de estado ni la URL. Espeja la política de T50.
- **Selector de nivel por URL (`?nivel=`)**, no por estado de cliente: cero JavaScript en la ruta de renderización (solo navegación mínima), legible con JS desactivado, cada nivel es una URL compartible.
- **La web no tiene `domain/`** y no debe tenerlo: ninguna regla de negocio del backend se duplica en TypeScript.
- **Banner permanente de análisis automatizado** en el layout raíz, así que aparece en todas las rutas por construcción, incluidas `not-found` y `error`. Sin estado ni mecanismo de cierre.
- **Server Components** salvo `app/error.tsx`, que Next exige como componente de cliente (pero no hace IO, solo renderiza un fallback).
- **Lighthouse accesibilidad 100/100** (build de producción, preset desktop, sin auditorías fallidas; umbral exigido ≥90).

**Problemas técnicos descubiertos y arreglados en T51**:
- `generateMetadata` duplicaba peticiones porque `AbortSignal.timeout()` por llamada rompe la Request Memoization de Next. Arreglado envolviendo `fetchFinding` en `React.cache()`, verificado contando peticiones en el log de uvicorn (una sola por visita).
- Backend no acota `page`, permitiendo `?page=100000000000000000000` → SQL error `NumericValueOutOfRange` → 500. Arreglado en la web con validación `parsePageParam` regex `/^\d{1,6}$/` y tope `MAX_PAGE = 999_999`, congelado con tests. El backend sigue siendo vulnerable (no es bug de T51, sino de T50).

## Lo que no existe en fase 1 (a propósito)

Contrastador, Analista, `ApiKeyProvider`, autenticación, panel de administración, despliegue.
