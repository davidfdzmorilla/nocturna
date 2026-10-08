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
         ├─ invoca: caffeinate -is uv run --frozen nocturna run-night
         │    ├─ cierra `Run` huérfano si existe
         │    ├─ crea `Run` nuevo
         │    ├─ fetch_new(arXiv) → Items(new)                   [sin LLM, Fase A]
         │    ├─ por cada Item (≤ max_items): authorize? → Reader → Reading   [Sonnet, Fase A]
         │    ├─ por cada Reading ≥ umbral: authorize? → Popularizer → Finding  [Sonnet, Fase B]
         │    ├─ hallazgos de medida desde tension_evaluation (≤ K)  [sin LLM, T89]
         │    ├─ por cada tensión elegible (≤ tope): authorize(writer)? → redactor → Finding  [Sonnet, T76]
         │    ├─ authorize(editor, reserva)? → Editor (1 llamada, todos candidatos)  [Opus, Fase C]
         │    └─ cierra Run (COMPLETED | PARTIAL | KILLED | FAILED)
         └─ tras la noche: nocturna evaluate-tensions (sin LLM, T89) → evaluaciones que la noche siguiente usan los hallazgos de medida y el redactor
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

**Dos vías tras un mismo puerto (T60.c)**: `domain/sources.py::ArxivSource` tiene dos implementaciones, y `sources.arxiv.ingest_via` elige cuál corre:

- **`oai` (por defecto)**: `infrastructure/arxiv/oai_client.py` cosecha por OAI-PMH contra `https://oaipmh.arxiv.org/oai` con `metadataPrefix=arXivRaw`, un `set` por categoría (`astro-ph.EP` → `physics:astro-ph:EP`, con mapa explícito y fallo ruidoso ante lo desconocido). Es el endpoint que arXiv documenta para cosecha programada.
- **`api`**: `infrastructure/arxiv/client.py`, la vía original del feed Atom. Degradada desde los cuatro episodios de 406 entre el 21 y el 25 de septiembre de 2026, pero **no borrada**: es el punto de comparación si OAI fallara.

Las dos producen `ArxivEntry` (el mismo DTO) y convergen en `mappers.py::entry_to_item`, único sitio donde nace un `Item`, de modo que **el dominio no cambia con la vía** y no puede haber duplicados entre ellas: `source` es `"arxiv"` y `external_id` va sin versión ni prefijo en ambas, congelado en `tests/test_oai_atom_equivalence.py`. `infrastructure/arxiv/transport.py` concentra la petición con reintentos para que la clasificación de códigos y la cortesía de 3 s no se dupliquen. Motivación y alternativas rechazadas en [ADR 0010](adr/0010-ingesta-por-oai-pmh.md).

**Reintento de fallos transitorios (T60.b)**: `infrastructure/arxiv/retry.py` implementa `Retrier` con backoff exponencial y jitter ante fallos transitorios de arXiv (406, 429, 5xx, errores de transporte). Política configurable desde `pipeline.toml` bajo `sources.arxiv`: `retry_max_attempts` (intentos), `retry_base_delay_s` (base de espera, se dobla cada intento), `retry_max_elapsed_s` (acota **cuándo puede iniciarse** un nuevo intento, no la duración total: tras la comprobación queda por delante la petición en curso, hasta 30 s de timeout HTTP, así que el techo real por petición es ≈ 93 s). Eventos de log estructurados: `arxiv.retry` (warning al reintentar), `arxiv.retry_recovered` (info al recuperarse), `arxiv.retry_exhausted` (error al agotarse). La ingesta es **cancelable** (usa `anyio.sleep`, no `time.sleep`) y respeta `hard_stop` porque `Retrier` se ejecuta dentro de la tarea que el vigía de T44 cancela.

`cli.py` es el composition root: único sitio que abre `unit_of_work`, elige la vía con `arxiv_source_from_config` e invoca `IngestArxiv` dentro de la transacción. T20 introduce el subcomando `nocturna run-night --dry-run` que ingesta sin llamar a agentes. **La ventana y el presupuesto de gasto de tokens siguen siendo exclusivos de `application/budget.py`**: la ingesta no pasa por `BudgetGuard` porque no gasta tokens de suscripción a Claude; el único límite es la política de cortesía de arXiv (3 s entre peticiones, constante de módulo no configurable) y los timeouts configurables de `Retrier`.

## Agente Reader (fase 1)

Implementado en T41. Lee un `Item` con `status = new` y produce una `Reading` persistida. Definición programática en `application/agents/` con prompt de rol en `prompts/reader.md` (versionado).

**Contrato de entrada y salida**: `AgentRequest` con abstract del paper envuelto en tags `<abstract>` (mitigación de inyección de prompt), rol `reader`, modelo de configuración (Sonnet). Salida esperada: JSON validado por Pydantic que mapea a `ReadingOutput` con campos `summary`, `objects` (lista de nombres de objetos astronómicos), `claims` (lista de afirmaciones), `interest_score` (1–5 entero). Salida en dominio: entidad `Reading`.

**Parseo tolerante y reintento**: si el JSON no valida, se reintenta una sola vez dentro del `AgentRunner`. Si falla nuevamente, el ítem se marca `Item.FAILED` de forma terminal; se **no** reintenta al noche siguiente (el cliente MCP siempre da `FAILED`, no `NEW`). La validación de `Reading.__post_init__` rechaza cadenas en blanco, lo que quedó capturado solo por revisión manual en T41: `ReaderOutput` las aceptaba sin regla defensiva.

**Patrón de transacciones**: la secuencia de gasto vive en un punto único, `AgentRunner.run()` en `application/agents/runner.py`. Por intento son **tres unidades de trabajo**, y la llamada al modelo no está en ninguna: (1) `BudgetGuard.authorize` + `timeout_for_call()` + lectura del `run_id`, que cierra antes de llamar; (2) `record_call`, **sola**, sin ninguna otra escritura; (3) la persistencia de la entidad de dominio, que abre el **caso de uso** después de que `run()` devuelva — el runner no persiste entidades de negocio, solo `AgentCall`. Entre (1) y (2) ocurren `LLMProvider.run_agent` **sin ninguna transacción abierta** (cero conexiones retenidas mientras se espera al modelo, hasta `item_timeout_s`) y `build(result, run_id)`, que es una función pura: parsea la salida y construye la entidad, sin tocar la base de datos. Que `record_call` no comparta transacción con la persistencia es la lección de T41: si la compartieran, un `IntegrityError` tiraría por rollback la fila de una llamada ya cobrada, y el gasto quedaría hecho y olvidado. Que `build` se ejecute **dentro** del runner es la lección del bloqueante de T41: si lanza `InvalidAgentOutput` o `InvariantViolation`, el intento se contabiliza con `status = invalid_output` y se reintenta, de modo que ninguna llamada pagada puede terminar sin `AgentCall`. El reintento va en el bucle con `continue`, **nunca dentro de un `except`**, y cada intento pasa por su propio `authorize`: el segundo se pesa contra lo que ya gastó el primero, porque su `record_call` cerró antes.

**Prompt de rol en `system_prompt`**: el abstract del paper va en el `prompt` estándar envuelto en `<abstract>`/`</abstract>` (escaped por Pydantic al serializar). El rol (instrucciones del Reader) viaja en un campo nuevo `AgentRequest.system_prompt` separado, que el `AgentSDKProvider` pasa como `system_prompt` a `ClaudeAgentOptions`, no mezclado con el abstract. Así se evita confundir instrucciones con datos de terceros.

**Reader v3 con medidas (T71.c, [ADR 0014](adr/0014-reader-v3-medidas-por-planeta.md))**: los ítems con alguna categoría en `[reader] measurement_categories` (hoy `astro-ph.EP`, cross-list incluidos) **y, desde T79, con `Item.exoplanet_match` cierto** (filtro de exoplanetas en la ingesta, [ADR 0018](adr/0018-filtro-de-exoplanetas-en-la-ingesta.md)) se leen con `prompts/reader-v3.md` (`prompt_version = reader-v3`), que además devuelve `measurements`; el resto sigue con `reader.md` (`reader-v2`). `ReadItem` construye dos `AgentRunner` del mismo rol, elige la variante antes de `authorize` y cada una autoriza con su propia estimación (`reader_estimated_tokens` / `reader_v3_estimated_tokens`). `application/agents/reader_measurements.py` filtra medida a medida (forma, invariantes, cita literal, valor presente en la cita, anfitriona en abstract o título con límite de palabra) y descarta sin reintentar; los descartes se registran como `reader.measurement_discarded`. Lista de categorías vacía = v3 apagado.

**Relectura con reader-v3 (T82, [ADR 0022](adr/0022-historia-de-reading-y-relectura-con-reader-v3.md))**: `run-item <id> --reader v3 --force` relee con v3 un ítem `read`, `discarded` o `published` que cumple ADR 0018 §2 y cuya lectura vigente no tiene medidas (`reread_refusal`, comprobado antes de `authorize`). `ReadItem.reread_with_measurements` usa el mismo `AgentRunner` de v3 y, si sale bien, `ReadingRepository.supersede`: la anterior queda con `superseded_at` y la nueva es la vigente (índice único parcial `uq_readings_item_id_current`, migración `f7c2d8e4a951`). No cambia `Item.status` ni los `Finding`; si falla, la anterior sigue vigente. `Reading.prompt_version` se guarda desde T82. Solo Reader, en un `Run` propio con `notes = "reread"`; con un `Run` en curso sale con 9 sin gastar. `night_report.sql` y `count_runs_with_prompt_version` excluyen esos `Run`.

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

Implementado en T43. Orquestador de publicación. Recibe todos los `Finding` candidatos de la noche en **una sola conversación** (`N:1`); devuelve lista de `candidate_id` a publicar (desde T89 el id del `Finding`; antes `item_id`), cada uno con `confidence` (0–1) y motivo (log, no persistido). Caso de uso `EditNight` que publica los aprobados y cierra el `Run` como `COMPLETED`.

**Contrato de entrada y salida**: `AgentRequest` con lista de candidatos (`candidate_id`, `type`, `title`, `level_curious` y, en los tipos de medida, una línea `data` con planeta, parámetro, valores, referencia y σ; nunca `evidence`, que es texto del abstract), rol `editor`, modelo Opus, prompt `editor-v2` desde T89 y `editor-v3` desde T76 (añade `catalog_tension`, ver § Redactor de tensiones). Salida esperada: JSON validado por `EditorOutput` con lista de decisiones `candidate_id / confidence / reason`; un `candidate_id` desconocido se ignora sin reintento. La guarda `Item` en `read` y `publish()`/`discard()` del ítem solo se aplican a `paper_explained`. Editor no recibe `level_amateur` ni `level_technical` (reducir entrada ~3.5×) pero su salida publica solo los aprobados, que ya llevan sus tres niveles (del Popularizer, de las plantillas de T89 o del redactor de T76).

**Estimación lineal de coste**: `editor_base_tokens + (num_candidates × editor_tokens_per_candidate)`. Configuración de `pipeline.toml` (cfg-2026-09-18): `editor_base_tokens = 2500`, `editor_tokens_per_candidate = 850`. Validación cerrada al cargar: `base + (max_items_per_night + max_candidates_per_night + max_writer_calls_per_night) × per_candidate ≤ editor_reserve_tokens` (desde T75: 2.500 + (30 + 5 + 2)×850 = 33.950 ≤ 60.000). Si el invariante se rompe, la configuración falla de día, no en plena noche. **Nota:** el § 2 (estimación lineal) de [ADR 0008](adr/0008-el-editor-llamada-n1-y-estimacion-de-coste.md) quedó superado por `cfg-2026-09-18` tras observación real (T60); los valores vigentes son 2500 + 850×N, no 4000 + 700×N. Ver `docs/CALIBRACION.md` para detalles de la calibración.

**Timeout rol-dependiente**: el Editor tiene 300 segundos (5 min), frente a 180 de Reader/Popularizer. La invariante de `hard_stop` se preserva: ambos están limitados por `min(..., seconds_until_hard_stop())`. Ver ADR 0005 § 10 extensión (T43).

**Candidatos huérfanos**: si el Editor falla (JSON inválido, timeout, error), sus `Finding` quedan sin `published_at` ni `confidence`. Los `Item` correspondientes quedan en `READ`. La noche se cierra `PARTIAL`. Ninguna noche futura los reintenta: `unpublished_for_run` filtra por `run_id`. Es característica correcta de fase 1.

**`_edit_one_night` es el único cierre de `COMPLETED`**: Reader y Popularizer devuelven su desenlace parcial; solo `_edit_one_night` traduce un desenlace exitoso del Editor a `RunStatus.COMPLETED`. Corrige la deuda de T42. El Editor es la última etapa de la noche y tiene poder unilateral de cerrar. Si falla (JSON inválido, timeout, gasto), ningún `Finding` se publica y la noche cierra PARTIAL; si no hay gasto ni fallo, aún puede no publicar nada si todos los candidatos son rechazados por criterio editorial.

## Orquestador nocturno (T44)

Implementado en T44. `RunNight` en `application/use_cases/run_night.py` corre las tres fases en orden (Ingesta, Reader, Popularizer, Editor). Cada fase se autoriza antes de ejecutar y reporta un desenlace (`COMPLETED`, `PARTIAL`, `KILLED`). `_run_night_for_real` en `cli.py` instancia `RunNight`, invoca `run()`, recibe el desenlace final y cierra el `Run` con su `status`, contadores y métricas.

**Política de `Run` huérfano**: al iniciar, `_current_or_new_run_night` comprueba si existe un `Run` en `RUNNING` del inicio de hoy (entre `window.start` y ahora). Si existe, lo cierra como `KILLED` (interpretación: interrupción de noche anterior) y abre uno nuevo. Si no existe, abre uno nuevo. Este comportamiento es específico de `run-night`; `run-item` mantiene el camino anterior (adoptar un `RUNNING` vivo para depuración); `run-item --reader v3 --force` (T82) nunca lo adopta.

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

**Reserva del Editor**: el Editor ve el presupuesto completo; los demás roles ven, como mucho, `nightly_tokens - editor_reserve_tokens` desde la primera llamada de la noche (Reader y Popularizer, además, sin la reserva del redactor; ver el párrafo siguiente). La reserva no es "bajo demanda"; es incondicional.

**Reserva anidada del redactor** (T75, [ADR 0025](adr/0025-reserva-anidada-del-redactor.md)): rol `writer`. El Editor ve B, el redactor B − E y Reader y Popularizer B − E − W (con los valores actuales, 300.000, 240.000 y 216.000), desde la primera llamada. `available_tokens_for` en `budget.py` es la única implementación del reparto y la usan el guard y `run-night --dry-run`, que muestra las tres porciones. Tope propio del redactor (`max_writer_calls_per_night = 2`, estimación 12.000 por llamada); `_call_limit_reason` exige un tope explícito por rol. Validadores al cargar: E + W < B, tope × estimación ≤ W, y el peor caso del Editor incluye los candidatos del redactor. Desde T76 lo usa el redactor de tensiones (§ Redactor de tensiones).

**Ventana y `hard_stop`**: huso horario explícito en `config/pipeline.toml` (`window.timezone`, clave IANA). La resta de segundos hasta `hard_stop` usa UTC en ambos operandos, inmune a cambios de hora. `timeout_for_call()` devuelve `min(item_timeout_s, segundos_hasta_hard_stop)`: ninguna llamada sobrevive a `hard_stop`.

**Multiplicador de reset semanal**: la noche del reinicio de suscripción (día y hora en configuración), si `now.hour >= weekly_reset_hour`, el presupuesto se multiplica por `reset_day_multiplier`. El `Run` lee el valor efectivo al crearse (T44) y lo almacena en `run.budget_tokens`. Las reservas del Editor y del redactor no escalan. Detalle completo en [ADR 0005](adr/0005-control-de-gasto.md). **Nota (2026-09-28, ADR 0011)**: la calibración automática de `nightly_tokens` por % semanal (Settings > Usage) quedó retirada; la suscripción es compartida entre proyectos y el % es inmedible. Si el autor observa presión en su semanal, baja el tope a mano.

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
- **Sesión y transacciones** (`session.py`): `unit_of_work` como único lugar que llama a `commit()` o `rollback()`. Desde T87 admite `commit=False`: siempre deshace al salir y lanza `RuntimeError` si alguien intenta confirmar (lo usa `run-night --dry-run`). Garantiza que `AgentCall` y `Run.tokens_used` viajen juntos.

Las decisiones arquitectónicas (fronteras transaccionales, caché de gasto, índice único parcial, enums, tipos de columna) están documentadas en [ADR 0003](adr/0003-persistencia-tipos-y-fronteras-transaccionales.md). Punto crítico: **T30 lee `AgentCallRepository.tokens_used_for_run()` desde la base de datos para autorizar llamadas, nunca fiándose del campo `Run.tokens_used` en memoria** (es una caché desnormalizada).

## Modelo de dominio

Cinco entidades inmutables o mutables con guardas:

- **Item** (mutable): unidad de ingesta (abstract de arXiv). Estados: `new → {read, failed}` (ambos terminales). `read → {discarded, published}` (también terminal). `failed` se alcanza cuando el JSON de salida del Reader no valida dos intentos seguidos. Ningún estado permite volver atrás; repetir la misma transición lanza `InvalidTransition`. Solo se asigna `status` a través de métodos `mark_read()`, `fail()`, `discard()`, `publish()`.
- **Item: marca `exoplanet_match`** (T79, ADR 0018): booleana, calculada en la ingesta por `ExoplanetFilter` (`domain/exoplanet_filter.py`, regla pura con palabras clave y patrones de designación de `[exoplanet_filter]`); decide la variante del Reader junto con `measurement_categories` y la prioridad en `next_unread` (`exoplanet_match DESC, fetched_at, external_id`). Se recalcula con `scripts/backfill_exoplanet_match.py`.
- **Reading** (inmutable): salida del Reader para un Item. Contiene `summary`, `objects`, `claims`, `interest_score` (1–5), tokens y modelo. No tiene `created_at`: es un hecho, no una entidad con ciclo de vida. Desde T71.c lleva `measurements: tuple[Measurement, ...] | None` (`None` = no extraído, `()` = extraído sin medidas); `Measurement` es un value object sin identidad (ADR 0014), persistido en `readings.measurements` JSONB con `none_as_null=True`.
- **Finding** (mutable): candidato de hallazgo con `title` y tres niveles de lectura. Solo `confidence` y `published_at` se asignan en el método `publish()`, juntos o no en absoluto. Antes de `publish()`, ambos son `None`. Tipos: `paper_explained`, `primera_medida` y `confirmacion_independiente` (T89, ADR 0020) y, desde T72, `catalog_tension` (ADR 0017), que lleva además `catalog_tension: CatalogTension` (value object con planeta canónico del archivo, parámetro, `archive_url`, umbral, σ de referencia y cada comparación medida del paper ↔ solución previa con su σ), informado si y solo si `type == catalog_tension` y construido con `catalog_tension_from`. `Item.status` sigue solo el camino `paper_explained`: un `catalog_tension` no lo cambia. Persistido como JSONB con `schema_version` y un CHECK que replica la invariante. Los tipos de T89 llevan, con el mismo patrón, `first_measurement: FirstMeasurement` o `independent_confirmation: IndependentConfirmation` (sin `evidence`) y `tension_evaluation_id`, informado si y solo si el tipo es uno de los dos; único por (`tension_evaluation_id`, `type`) (migración `e5b3a9d1c746`). Desde T76 `catalog_tension` también lleva `tension_evaluation_id` obligatorio (migración `a3f6d9c1b852`, ADR 0026).
- **Run** (mutable): una ejecución nocturna. Estados: `running` → {`completed`, `partial`, `failed`, `killed`}. El campo `budget_tokens` es el presupuesto efectivo (ya con el multiplicador del día). Los contadores `items_fetched`, `items_read`, `findings_published` son monótonos no decrecientes (se permite asignar un valor ≥ al actual, nunca menor). Solo se asignan `status`, `finished_at`, `tokens_used` a través del método `finish()` y `record_agent_call()`.
- **AgentCall** (inmutable): registro de una llamada ya ocurrida, con `tokens_in`, `tokens_out`, `duration_ms`, `status` (ok / invalid_output / error / timeout) y `prompt_version` (cadena de versión del prompt usado, para trazabilidad futura).

**Implementación en dataclasses (estándar de Python)**, no Pydantic: las reglas de negocio fallan con excepciones de dominio (`InvalidTransition`, `InterestScoreOutOfRange`), no con errores de validación. Los `__post_init__` y `__setattr__` protegen invariantes; quien necesita cambiar un campo guarded usa `object.__setattr__` internamente. Todos los `datetime` son *aware* (con `tzinfo`); el dominio no convierte zonas horarias, solo rechaza naive.

Pydantic se usa solo en las fronteras: `infrastructure/config.py` para el TOML tipado, y `application/agents/` para validar y estructurar el JSON que sale de los agentes antes de construir la entidad de dominio.

**Nota sobre hermeticidad de las guardas**: `object.__setattr__`, el descriptor de slot en clase, y `dataclasses.replace()` pueden rodear `__setattr__`. La defensa real contra una asignación manipulada de `tokens_used` en memoria es que T30 lea el acumulado con `AgentCallRepository.tokens_used_for_run()` desde la base de datos, nunca fiándose del valor del objeto en memoria.

## API de lectura (fase 1)

Implementada en T50. FastAPI con tres endpoints expuestos:

- `GET /health`: comprobación de vivacidad. Devuelve `200` si el servicio y PostgreSQL responden. `503` si la base de datos no está disponible. Contrato: `{"status": "ok"}`.
- `GET /findings?page=<int>&size=<int>[&type=<tipo>]`: feed paginado de hallazgos publicados; desde T77, `type` filtra por un valor de `FindingType` (fuera del enum, `422`) y cada elemento lleva `type`. `page` base 1 (1–999999, por defecto 1; tope `MAX_PAGE` en `api/routes/findings.py`, igual al de la web, T70), `size` (1–50, por defecto 20). Respuesta: `{"items": [<finding>, ...], "page": <int>, "size": <int>, "total": <int>}`. Página sin resultados dentro de 1–999999 devuelve `200` con `items` vacía; `page` por encima del tope, `0` o `size` fuera de 1–50 devuelven `422`. `total` es `COUNT` exacto, económico a este volumen.
- `GET /findings/{id}`: detalle de un hallazgo publicado. Devuelve el objeto completo. `404` si no existe o no está publicado (indistinguible por diseño). El mismo `404` para «no existe» y «no publicado» impide enumerar candidatos que el Editor rechazó.

**Contrato de hallazgo (`Finding`)**: `id`, `title`, `level_curious`, `level_amateur`, `level_technical`, `published_at`, `item_id` (enlace a arXiv). **Campos ocultos**: `confidence` (es una nota editorial interna, no una métrica científica; expuesta junto a texto generado por IA se malinterpretaría como "grado de certeza científica", contradictorio con el banner obligatorio), `run_id`. Desde T77, `type` sale en el listado y en el detalle, y el detalle lleva `catalog_tension`, `first_measurement` e `independent_confirmation` (cada uno `null` salvo el de su tipo), construidos por lista blanca con esquemas propios ([ADR 0024](adr/0024-contrato-publico-de-los-datos-estructurados-de-finding.md)); no salen `tension_evaluation_id` ni los metadatos internos del archivo. La privacidad de `confidence` protege la semántica del análisis automático.

**Filtro de publicación**: `findings.published_at IS NOT NULL`, única fuente de verdad. Sin cruce con `items.status`: un hallazgo es «publicado» si tiene timestamp, punto. Refaldado por constraint `CHECK (confidence IS NULL) = (published_at IS NULL)` — ambos campos se asignan juntos o no en absoluto, en el método `Finding.publish()`.

**Implementación técnica**: la sesión de base de datos **nunca hace `commit()`**, solo `rollback()` al cerrar. Es la barrera dura contra escritura: `unit_of_work` es la frontera transaccional del pipeline (Reader, Popularizer, Editor confirman); la API no debe poder confirmar nada, aunque sea por accidente. La guarda AST `test_api_read_only.py` detecta `commit()` y `__setattr__` de entidades, pero es un detector de descuidos evasible; la verdadera defensa es arquitectónica: no abre contexto que pueda cambiar datos.

**CORS**: `Settings.cors_origins`, por defecto `["http://localhost:3000"]`. Solo `GET`, nunca `*`. Configurable desde `NOCTURNA_CORS_ORIGINS` (variable de entorno con formato JSON: `'["http://a","http://b"]'`).

**Errors**: `500` devuelve cuerpo fijo `{"detail": "internal error"}`, la traza va solo a `stderr`. `404` a nivel de endpoint no devuelto; solo los especificados arriba.

**Arranque local**: `cd backend && uv run uvicorn nocturna.api.app:create_app --factory --reload --port 8000`. El parámetro `--factory` invoca `create_app()` que devuelve `FastAPI()`. Settings cacheadas con `lru_cache` en `deps.py`.

**Script de siembra**: `backend/scripts/seed_demo.py` (fuera del paquete instalable) siembra tres `Finding` de prueba publicados por el mismo camino de dominio que el Editor (y con la misma guarda: `NOCTURNA_ALLOW_SEED=1`). No es idempotente: segundo lanzamiento falla con `IntegrityError` sin corromper nada (todo dentro de transacción de escritura).

## Web (fase 1)

Implementada en T51. Next.js 15.5.25 con App Router, React 19.1.0, Tailwind 4.3.3. Dos rutas públicas de solo lectura:

- `/` feed paginado (`?page=1`, y desde T77 `?tipo=articulo|tension|primera-medida|confirmacion`) de hallazgos publicados, orden descendente por `published_at`, desempate por `id`. `page` base 1 (default 1), `size` 1–50 (default 20). Respuesta: objeto `{items: [...], page, size, total}`. Página fuera de rango → `200` con lista vacía, aviso visual al usuario.
- `/hallazgo/[id]` detalle de un hallazgo publicado. Selector de nivel por parámetro URL `?nivel=curious|amateur|technical` (default `curious`), **sin estado de cliente**: cada nivel es una URL compartible, legible sin JavaScript. Enlace a arXiv original del paper. `404` si no existe o no publicado (indistinguible por diseño).

**Decisiones estructurales**:
- **SSR dinámico con `force-dynamic` + `cache: "no-store"`**, no ISR. Razón: hallazgos se publican en bloque de madrugada y no cambian hasta la siguiente noche, ISR solo aportaría latencia a cambio de razonar sobre el *full route cache* de Next (si la API está caída al revalidar, se cachea la página degradada) y prerenderizar `/` en build ataría el build a tener API y PostgreSQL levantadas. Con SSR dinámico, `pnpm build` toca solo el código (verificado: `grep` de `localhost:8000` y `NOCTURNA_API_URL` no aparecen en `.next/static`). Reversible en una línea (`revalidate = 300`).
- **Frontera de IO única**: `src/lib/api/client.ts` es el único módulo que hace `fetch`. Mapea `404` → `not_found()` y todo lo demás → `unavailable`, nunca devuelve al llamante el cuerpo del servidor, el código de estado ni la URL. Espeja la política de T50.
- **Selector de nivel por URL (`?nivel=`)**, no por estado de cliente: cero JavaScript en la ruta de renderización (solo navegación mínima), legible con JS desactivado, cada nivel es una URL compartible.
- **La web no tiene `domain/`** y no debe tenerlo: ninguna regla de negocio del backend se duplica en TypeScript.
- **Banner permanente de análisis automatizado** en el layout raíz, así que aparece en todas las rutas por construcción, incluidas `not-found` y `error`. Sin estado ni mecanismo de cierre.
- **Server Components** salvo `app/error.tsx`, que Next exige como componente de cliente (pero no hace IO, solo renderiza un fallback).
- **Lighthouse accesibilidad 100/100** (build de producción, preset desktop, sin auditorías fallidas; umbral exigido ≥90). Medido de nuevo en T77 (2026-10-06) en `/`, `/?tipo=primera-medida` y dos detalles.
- **Tipos de hallazgo (T77, ADR 0024)**: etiqueta de texto por tipo en el feed y el detalle ("Artículo explicado", "Tensión con el catálogo", "Primera medida", "Confirmación independiente"), etiqueta "Candidato" solo en `catalog_tension` con su leyenda, filtro por tipo como lista de enlaces sin estado de cliente, y bloque "Datos del contraste" en los tres niveles. Texto de terceros solo como texto escapado (guarda `no-raw-html.test.ts`); los enlaces externos salen de `lib/externalLinks.ts` con prefijos validados; `lib/measurementFormat.ts` replica el formato de números y unidades de las plantillas.

**Problemas técnicos descubiertos y arreglados en T51**:
- `generateMetadata` duplicaba peticiones porque `AbortSignal.timeout()` por llamada rompe la Request Memoization de Next. Arreglado envolviendo `fetchFinding` en `React.cache()`, verificado contando peticiones en el log de uvicorn (una sola por visita).
- Backend no acota `page`, permitiendo `?page=100000000000000000000` → SQL error `NumericValueOutOfRange` → 500. Arreglado en la web con validación `parsePageParam` regex `/^\d{1,6}$/` y tope `MAX_PAGE = 999_999`, congelado con tests (no es bug de T51, sino de T50). Saldado en el backend en T70: `page` acotado a `MAX_PAGE = 999_999` con `Query(le=...)`; por encima, `422`.

## Cálculo de la tensión (fase 2, T73–T74)

Implementado en T73 ([ADR 0015](adr/0015-calculo-de-la-tension.md)) y T74 ([ADR 0016](adr/0016-adaptador-nasa-exoplanet-archive.md)). Cálculo en dominio y aplicación; red solo en el adaptador de infraestructura; sin LLM:

- **Puerto `domain/catalog.py::ExoplanetCatalog`** (asíncrono, como `ArxivSource`): `resolve_planet(name)` → nombre canónico o `None`; `solutions(planet, parameter)` → todas las `CatalogSolution` publicadas, incluida la del propio paper. `CatalogSolution` es un value object con `is_default`, `reference` y `arxiv_id` (sin versión, mismo formato que `Item.external_id`). Implementado en T74.
- **`domain/tension.py`**: `compare(paper, prior)` calcula σ en unidad común terrestre con el error que mira al otro valor y devuelve `CatalogComparison` con los números usados; `TensionResult` agrupa por (ítem, planeta, parámetro) todas las comparaciones y expone `is_candidate(umbral)`: referencia = la previa por defecto (por igualdad de valor, exactamente una) y todas las medidas del paper deben superar el umbral frente a ella.
- **`application/use_cases/compute_tensions.py::ComputeTensions`**: filtra medidas no utilizables sin llamar al catálogo, resuelve el planeta, agrupa, excluye la solución propia (desde T83 con `classify_solution`, por `arxiv_id` o por valores; ver § T88), filtra previas utilizables y devuelve `TensionReport` con resultados y descartes (`NOT_USABLE`, `PERIOD_TTV`). No aplica el umbral ni captura excepciones del puerto.
- **Adaptador del NASA Exoplanet Archive (T74)**: `infrastructure/exoplanet_archive/`, sin servidor MCP. `client.py`: cliente TAP sobre `httpx` con techo de peticiones por proceso comprobado antes de enviar (cuenta también las fallidas), espaciado de cortesía (`RateLimiter` de `infrastructure/arxiv/`), límite de tiempo total por petición (`anyio.fail_after`), `User-Agent` neutro y tope de tamaño; sin reintentos; HTTP ≠ 200, VOTABLE de error, timeout o JSON de alias inválido → `ExoplanetArchiveUnavailable`. `names.py`: `clean_name` y `normalize_name`. `catalog.py`: `ExoplanetArchiveCatalog` resuelve por `pl_name` exacto contra un índice de `pscomppars` pedido una vez y, si no está, por el servicio de alias (solo rama de planeta), con cachés por instancia; `solutions` hace una consulta a `ps` por planeta para los tres parámetros. `mappers.py`: filas de `ps` a `CatalogSolution` (masa solo con `pl_bmassprov == "Mass"`; `arxiv_id` del bibcode ADS del enlace de `pl_refname`; filas sin valor, ≤ 0 o no finitas se omiten; valores no numéricos son error) y `planet_overview_url`. Configuración en `[sources.exoplanet_archive]` y umbral en `[tension]`, sin defaults; el peor caso del archivo se suma al validador de tiempo de ingesta aunque la noche aún no lo consulte. `TensionResult.reference_sigma()` en dominio.
- **`--dry-run` con cruce**: desde T87, la ingesta, el plan de gasto y las lecturas se hacen en una sola sesión con `unit_of_work(commit=False)` que se deshace al final, así que no escribe nada en la base; tras la ingesta y el plan de gasto, cruza los `Reading` con medidas ya guardados (`ReadingRepository.with_measurements()`) contra el archivo con `ComputeTensions`, sin tokens, e imprime medidas utilizables, emparejadas, tramos de σ, candidatos y peticiones. Si el archivo no está disponible, error por stderr y código 1. `run-night` real no consulta el archivo, tampoco desde T76: el redactor trabaja sobre las evaluaciones ya guardadas (ADR 0026).

## Regla de referencia y evaluaciones de tensión (fase 2, T88)

Decisiones del autor del 2026-10-02 (caso real de HIP 67522); [ADR 0020](adr/0020-referencia-limites-espera-y-medidas-publicables.md) supersede ADR 0015 §6:

- **Previas desde el histórico**: el catálogo (`ExoplanetArchiveCatalog(archive, alias_client)`) lee las soluciones activas de `archive_solution` (T81) con `catalog_solution_from_archive`; el servicio de alias solo se usa para nombres fuera del índice local. La consulta en vivo a `ps`/`pscomppars` de T74 ya no existe.
- **Referencia por parámetro** (`select_reference`): la solución por defecto si es `Published Confirmed` con error bilateral; si no, la `Published Confirmed` más reciente (`pl_pubdate`, `releasedate`, `solution_key`); la propia del paper (`arxiv_id`) se excluye siempre. `TensionResult.reference_sigma()` se mide frente a esa referencia; `CatalogTension` (ADR 0017) no cambia y `catalog_tension_from` falla si la referencia no es la solución por defecto.
- **Límites superiores** (`compare_with_limit`): no son referencia; incompatible si (x − L)/e_minus ≥ umbral en todas las medidas, consistente en otro caso. **Periodo** (`check_period`, `[tension.period]`): diferencia mínima ΔP/P ≥ 1e-4 o ΔP ≥ 1 h; alias como etiqueta (n ≤ 5); con `ttv_flag` no se comparan periodos.
- **`TensionEvaluation`** (tabla `tension_evaluation`, migración `d2a8c5e7f104`): clave `(reading_id, planeta del Reader, parámetro)`; estados `awaiting_reference`, `evaluated`, `consistent_with_limit`, `incompatible_with_limit` y `closed_loop`; solo `awaiting_reference` se reevalúa. `RecordTensionEvaluations` crea las que faltan, reevalúa las que esperan, escribe solo si cambia y no recalcula lecturas cuyas evaluaciones son todas terminales.
- **Dónde corre**: al final de `archive-snapshot`, en otra transacción tras guardar el snapshot (código 3 si falla; el snapshot queda guardado); en `nocturna evaluate-tensions [--dry-run]` (código 1 si hay fallos de resolución, guardando el resto); desde T89, tras cada noche desde `run-night-scheduled.sh` (no con `--dry-run`; su fallo no cambia el código de salida de la noche); y, conciliado y sin escribir, en `run-night --dry-run`. `run-night` en sí no lo ejecuta; no hay llamadas a Claude.
- **Solución propia** (T83, [ADR 0023](adr/0023-solucion-propia-por-arxiv-id-y-por-valores.md)): `domain/own_solution.py::classify_solution` clasifica cada solución frente al paper como propia por `arxiv_id`, propia por valores (sin `arxiv_id`, masa o radio sin cota, `pl_pubdate` no anterior al mes del ítem menos `pubdate_margin_months` y valor a ≤ `value_rel_tolerance` relativa del valor del archivo, en unidad canónica), ambigua o independiente. Las propias se excluyen y deciden `closed_loop`; las ambiguas siguen siendo previas; `confirmacion_independiente` exige una referencia independiente. Parámetros en `[tension.own_solution]`. `scripts/t83_link_report.py` mide el enlace sin escribir.
- **Planeta ausente** (T89): solo si el servicio de alias responde `System Not Found` (sin distinguir mayúsculas). Cualquier otra respuesta, o `OK` sin el nombre en `planet_set`, lanza `PlanetResolutionFailed`: esa medida no se evalúa esa vez, no se cachea y se reintenta en la siguiente pasada. Los grupos de periodo no cuentan para decidir si una lectura con filas guardadas debe recalcularse.

## Hallazgos de medida (fase 2, T89)

Implementado en T89 ([ADR 0020](adr/0020-referencia-limites-espera-y-medidas-publicables.md)). Sin LLM salvo el Editor:

- **Reglas** en `domain/measurement_findings.py` (puras): `primera_medida` para una evaluación `awaiting_reference` de masa o radio sin cota ni solución propia (subtipo `absent` o `no_comparable_solution`); `confirmacion_independiente` para una `evaluated` de masa o radio con todas las medidas a σ ≤ `confirmation_max_sigma` frente a la referencia y la entrada más reciente (paper `published_at`, referencia `releasedate`) dentro de `confirmation_window_days` en la fecha local de la noche.
- **`GenerateMeasurementFindings`** (`application/use_cases/`): lee `tension_evaluation` ya guardada (sin red), salta las evaluaciones que ya tienen `Finding` de ese tipo, aplica el tope `max_candidates_per_night` por `Run` en orden determinista y persiste los `Finding` con texto de `application/measurement_finding_texts.py` (plantillas deterministas). No recibe proveedor, `AgentRunner` ni `BudgetGuard`. Con `confirmation_enabled = false` (hasta T83) las confirmaciones solo se cuentan como bloqueadas.
- **En la noche**: fase `_phase_measurement_findings` entre Popularizer y Editor (no corre si el Editor se va a saltar, p. ej. pasado `hard_stop`); el Editor se llama aunque solo haya candidatos de medida; si la generación falla, la noche queda `PARTIAL` y el Editor se llama igualmente. `run-night --dry-run` lista los candidatos que se generarían sin escribir.
- **Configuración**: `[measurement_findings]` (`max_candidates_per_night`, `confirmation_max_sigma`, `confirmation_window_days`, `confirmation_enabled`), sin valores por defecto en código.

## Redactor de tensiones (fase 2, T76)

Implementado en T76 ([ADR 0026](adr/0026-redactor-de-tensiones.md)). Único camino nuevo hacia Claude: fase del redactor → `AgentRunner` con rol `writer`; `budget.py` no cambia.

- **Elegibilidad** (`domain/measurement_findings.py::catalog_tension_skip_reason`, pura): evaluación `evaluated` con `reference_sigma` ≥ `[tension] threshold_sigma`, referencia igual a la solución por defecto e `INDEPENDENT` según `classify_solution`; en periodo, diferencia mínima y sin alias. `incompatible_with_limit` y las tensiones frente a otra referencia no se publican (OD 244).
- **Selección** (`SelectTensions`, solo lectura, sin proveedor ni guard): descarta además las evaluaciones ya redactadas, los ítems con `max_calls_per_item` llamadas `invalid_output` del redactor en cualquier noche (fallida) y las que no forman una `CatalogTension` válida (se construye antes de autorizar); orden por σ descendente, fecha del ítem e id. `run-night --dry-run` imprime elegibles y excluidas con su motivo.
- **Redacción** (`application/use_cases/write_tensions.py::WriteTensions`): un `AgentRunner` por tensión, estimación `writer_estimated_tokens`, hasta `max_calls_per_item` intentos, modelo `[models] writer`, prompt `writer-v1`. El dato va entre `<tension>` y `</tension>` (título del ítem, planeta, parámetro, medidas con `evidence`, referencia, σ frente a cada previa), cada valor saneado a una línea sin `<` ni `>` (`application/agents/prompt_text.py`). Salida `WriterOutput` (título y tres niveles). El `Finding` `catalog_tension` se guarda sin publicar con `tension_evaluation_id`; `Item.status` no cambia.
- **En la noche**: fase `_phase_writer` entre los hallazgos de medida y el Editor; no corre si el Editor se va a saltar. Tope de llamadas: termina la fase sin degradar. Presupuesto agotado: `partial` (`writer_budget_exhausted`) y sigue al Editor. `OUTSIDE_WINDOW`: `killed` sin Editor. `RATE_LIMITED`: `partial` sin Editor. Error inesperado: termina la fase, `partial` (`writer_error`) y sigue al Editor. Nunca captura la cancelación del `hard_stop`.
- **Editor**: `editor-v3` recibe `catalog_tension` en el mismo bloque `<candidates>` con una línea `data` acotada (planeta, parámetro, valores, referencia, σ, umbral, número de previas y su rango de σ), nunca `evidence`. Lo acota el tope del redactor, que el validador de la reserva del Editor ya cuenta.
- **Informe**: `night_report.sql` añade las tensiones de la noche y las llamadas del redactor por estado.

## Histórico del NASA Exoplanet Archive (fase 2, T81)

Implementado en T81 ([ADR 0019](adr/0019-historico-del-nasa-exoplanet-archive.md)). Segunda vía de la fase 2, sin LLM:

- **Lanzador propio**: `nocturna archive-snapshot [--full] [--dry-run]`, programado por un segundo agente de launchd (`com.nocturna.archive-snapshot`, viernes 10:00) con `backend/scripts/archive-snapshot-scheduled.sh`. Fuera de `run-night`, de la ventana y del presupuesto.
- **`domain/archive.py`**: `ArchiveSolution` con su identidad `solution_key` (v1, sha256 de nombre, `ref_key` y los 12 valores canónicos), `collapse_duplicates`, `diff_snapshot` (altas, bajas, reactivaciones, cambios y pérdidas de solución por defecto) y el puerto `ArchiveSolutionSource`; `ArchiveRepository` en `domain/repositories.py`.
- **`application/use_cases/take_archive_snapshot.py`**: elige completo (primer snapshot del mes natural o `--full`) o incremental, salta a completo si los lotes no caben, aplica la guarda de cambios masivos y persiste o, en dry-run, solo informa.
- **`infrastructure/exoplanet_archive/snapshot.py`**: `ArchiveSnapshotSource` sobre `ArchiveHttpClient` (cuatro consultas ADQL, lotes por planeta); el cliente admite un tope de respuesta configurable (64 MiB para el snapshot; el catálogo de T74 conserva 20 MB).
- **Tablas** `archive_snapshot`, `archive_solution` y `archive_default_change` (migración `b4d7f1a26c93`), escritas en una sola transacción por `SqlAlchemyArchiveRepository` con upsert por bloques.

## Lo que no existe en fase 1 (a propósito)

Contrastador, Analista, `ApiKeyProvider`, autenticación, panel de administración, despliegue.

**Fase 2 (aprobada en T61, [ADR 0012](adr/0012-fase-2-tension-frente-a-catalogo.md))**: lo ya construido está en § "Cálculo de la tensión" (T73–T74) y en § "Modelo de dominio" (T72); la reserva del redactor (T75) y la API y web (T77) ya existen; el redactor y su integración en `run-night` (T76) también; queda el cierre de la fase (T78, T84–T86). La fase 2 añade un `Finding.type` nuevo (`catalog_tension`): la tensión entre lo que dice un paper de astro-ph.EP sobre un objeto y las medidas previas del NASA Exoplanet Archive. La discrepancia en σ la calcula Python y se guarda en un campo estructurado de `Finding`; Claude solo redacta a partir de números ya calculados, sin herramientas (`tools=[]`), y los candidatos pasan por el Editor como los demás. La etapa tiene una reserva de presupuesto fija, análoga a la del Editor, sin subir `nightly_tokens`, y `max_items_per_night` baja a 30. El archivo se integra como adaptador de `infrastructure/`, sin servidor MCP, porque ningún agente lo consulta. Todo lo que sigue a T71, el experimento de viabilidad sin tokens, depende de su resultado. Esta sección se reescribe tarea a tarea conforme las piezas existan.
