# PLAN_TAREAS — Fase 1

Estado de cada tarea: `pending` · `in_progress` · `done` · `blocked`.
El orquestador toma la primera `pending` cuyas dependencias estén `done`, propone plan, espera aprobación.
Cada tarea termina con: tests en verde, `PLAN_TAREAS.md` actualizado, commit preparado (no ejecutado).

Objetivo de la fase: una noche completa corre en local contra la suscripción, publica hallazgos en PostgreSQL y la web los muestra. Sin despliegue.

---

## Bloque 0 — Base

### T00 · Esqueleto del repositorio
- **Estado**: done
- **Depende de**: —
- **Alcance**: estructura de carpetas según `CLAUDE.md`, `docs/` con `ARCHITECTURE.md`, `OPEN_DECISIONS.md`, `TECHNICAL_DEBT.md` y `adr/0001-suscripcion-como-proveedor.md` (por qué Agent SDK y no API key, con las restricciones que impone). `.gitignore`, `README.md` mínimo.
- **Hecho cuando**: `git log` muestra un commit inicial y `docs/` se puede leer de arriba abajo sin huecos.

### T01 · Agentes de desarrollo en `.claude/agents/`
- **Estado**: done
- **Depende de**: T00
- **Alcance**: Architect, Backend, Frontend, Database, Tester, Reviewer, Docs-keeper, Committer. Cada uno con su rol, herramientas permitidas y las reglas de `CLAUDE.md` que le aplican (Docs-keeper: cierre de tarea en documentación; Committer: sin atribución a IA, preparar ≠ ejecutar; Reviewer: vigilancia específica sobre `budget.py`).
- **Hecho cuando**: el orquestador puede delegar T02 sin instrucciones adicionales.

### T02 · Entorno local
- **Estado**: done
- **Depende de**: T01
- **Alcance**: `docker-compose.yml` solo con PostgreSQL 16 y volumen. `backend/pyproject.toml` con `uv`, FastAPI, SQLAlchemy 2, Alembic, Pydantic, `claude-agent-sdk`, pytest. `config/pipeline.toml` con todos los valores iniciales de `CLAUDE.md`. Carga de configuración tipada (Pydantic Settings).
- **Hecho cuando**: `docker compose up -d`, `uv sync`, `uv run pytest` (con un test trivial) pasan en limpio.

---

## Bloque 1 — Dominio y persistencia

### T10 · Modelo de dominio
- **Estado**: done
- **Depende de**: T02
- **Alcance**: entidades `Item`, `Reading`, `Finding`, `Run`, `AgentCall` en `domain/`, con sus reglas (transiciones de `status`, `interest_score` en 1–5, `confidence` en 0–1). Interfaces de repositorio. Interfaz `LLMProvider`.
- **Hecho cuando**: tests de dominio cubren transiciones e invariantes. Cero imports de SQLAlchemy o del SDK en `domain/`.

### T11 · Persistencia
- **Estado**: done
- **Depende de**: T10
- **Alcance**: modelos SQLAlchemy, migración inicial Alembic, repositorios en `infrastructure/`. Índices en `items(source, external_id)` (único) y `findings(published_at)`.
- **Hecho cuando**: `alembic upgrade head` limpio; tests de repositorio contra PostgreSQL de compose.

---

## Bloque 2 — Ingesta

### T20 · Servidor MCP `arxiv-astro`
- **Estado**: done
- **Depende de**: T11
- **Alcance**: servidor in-process con dos herramientas: `fetch_new(since: date, categories: list[str])` y `get_abstract(arxiv_id: str)`. Cliente HTTP a la API de arXiv con respeto a su rate limit (3 s entre peticiones). Los resultados se persisten como `Item` con `status = new`; deduplicación por `external_id`.
- **Hecho cuando**: `nocturna run-night --dry-run` ingesta los abstracts del día para las categorías configuradas y muestra una vista previa de 200 caracteres de cada abstract sin llamar a ningún agente. Tests con respuestas de arXiv grabadas. Un test en subproceso comprueba que `claude_agent_sdk` no entra en `sys.modules` durante `--dry-run`.

---

## Bloque 3 — Control de gasto (antes que cualquier agente)

### T30 · `budget.py` y límites duros
- **Estado**: done
- **Depende de**: T11
- **Alcance**: `BudgetGuard` que lee `pipeline.toml`, acumula tokens desde `AgentCall` en base de datos. Dos métodos: `check(role, estimated_tokens) -> BudgetDecision` (sin lanzar); `authorize(role, estimated_tokens)` (lanza si deniega). Comprobación de ventana horaria con `hard_stop`. Reserva de presupuesto para el Editor. Contadores de ítems y turnos. Marcado de `Run` como `partial` / `killed`. Reloj inyectable en `domain/clock.py` con implementación `SystemClock` en `infrastructure/clock.py`.
- **Hecho cuando**: tests que demuestran: corte al alcanzar `nightly_tokens`; rechazo pasada `hard_stop`; reserva del Editor intacta aunque el Reader agote lo suyo; persistencia del acumulado tras reinicio simulado. 390 tests en verde.
- **Nota**: dos revisiones completadas · primera rechazada por bug en `seconds_until_hard_stop` (resta en hora de pared ante cambios de hora) · segunda aprobada con verificación por mutantes · decisiones abiertas nuevas en `OPEN_DECISIONS.md`. Cerrada: 2026-09-16

---

## Bloque 4 — Agentes

### T40 · `AgentSDKProvider` y `FakeLLMProvider`
- **Estado**: done
- **Depende de**: T30
- **Alcance**: implementación de `LLMProvider` sobre `claude-agent-sdk` con `query()` y `ClaudeAgentOptions`: modelo por rol, `max_turns` desde configuración, servidores MCP inyectados, sin `setting_sources` (todo programático), sin `ANTHROPIC_API_KEY`. Extracción de tokens y modelo de los mensajes `result`. `FakeLLMProvider` para tests que devuelve JSON fijo por agente.
- **Hecho cuando**: un test de humo **manual y marcado como tal** (`pytest -m manual`) hace una llamada real mínima y registra tokens en `AgentCall`. El resto de la suite no toca Claude.
- **Ejecución real completada**: 2026-09-17 · `env -u ANTHROPIC_API_KEY uv run pytest -m manual -s` desde `backend/` ejercitó autenticación real, registró tokens en `AgentCall` y validó que `BudgetGuard` + `AgentSDKProvider` + persistencia funcionan juntos. Suite: 472 passed.
- **Cuatro revisiones completadas · rondas 1–2 rechazadas, ronda 3 aprobada, ronda 4 aprobada.** Ronda 1: guarda anti-Claude no cubría `ClaudeSDKClient` y `pytest -m db` ejecutaba el test de humo por `argparse` (sustituir ≠ componer). Ronda 2: `sys.exc_info()` es estado global del hilo, falla si `run_agent` se invoca desde dentro de un `except` (reintento de T41); parcheo faltaba tests. Ronda 3: cierre de frontera + `test_llm_call_sites.py` congela que gasto se persista antes de devolver resultado. Ronda 4: humo destapó subregistro de 2,8× en la contabilidad (`ResultMessage.usage` no incluye gastos de herramientas internas, solo del modelo pedido). El CLI factura tokens de Haiku (946 tokens/sesión en fase 1) no visibles en `usage`, solo en `model_usage` y `total_cost_usd`. Arreglo: máximo entre `usage` y suma de `model_usage`, componente a componente, en nueve caminos de extracción (tests: 472 passed). Incidente registrado en ronda 1: durante mutación de guarda, suite llamó CLI real (8,78 s, `LLMTimeout`), gastando suscripción. El humo manual pagó por sí mismo al encontrar el agujero de 2,8×.
- **Decisiones abiertas afectadas**: línea 33 de `OPEN_DECISIONS.md` (modelo efectivo sí disponible), `RateLimitEvent` (observado en llamada normal con éxito), gasto lateral del CLI (no configurable, debe calibrarse en T60).
- **Cerrada: 2026-09-17**

### T41 · Agente Reader
- **Estado**: done
- **Depende de**: T40, T20
- **Alcance**: prompt en `prompts/reader.md`, esquema Pydantic de salida (`Reading`), caso de uso `ReadItem` que pasa por `BudgetGuard`, valida JSON, reintenta una vez, marca `failed` si vuelve a fallar. Una conversación por ítem.
- **Hecho cuando**: `nocturna run-item <id>` produce una `Reading` persistida. Tests con `FakeLLMProvider` para el camino feliz, JSON inválido y presupuesto agotado.
- **Ejecución completada**: 2026-09-17 · 567 passed · Dos revisiones completadas · Ronda 1 rechazada: reintento interno de validación JSON se saltaba `authorize`, permitiendo gasto no contabilizado; se arregló con reintento manual en `ReadItem`. Ronda 2 aprobada. Bloqueante detectado por revisión: `ReaderOutput` aceptaba cadenas en blanco que `Reading.__post_init__` rechazaba, causando `InvariantViolation` sin `AgentCall` ni tokens contabilizados, fuga permanente cada noche. El ítem volvía a `next_unread` sin saber el motivo real. Arreglado con regla defensiva explícita.
- **Humo manual ejecutado**: 2026-09-17 · Suite: 571 passed, 2 deselected. Volcado real del Reader: `input_tokens: 2`, `cache_creation_input_tokens: 1269`, `cache_read_input_tokens: 0`, `output_tokens: 442`. Contabilizado: 2.796 tokens. Sin caché, solo `input_tokens: 2 + output: 442 = 444`, subregistro de 6,3×. **Valida ADR 0007: máximo componente a componente es correcto con datos reales.** Prompt: entrada unitaria (abstract 1.269 tokens de caché). Reintento: ninguno. `interest_score: 4` (binario de estrella de neutrones / enana blanca, período de 83 minutos). `RateLimitEvent` presente en stream, sin incidencia. Test reproducible: ambas ejecuciones del humo devuelven idénticas cifras.
- **Cerrada: 2026-09-17** · Reader con humo real validó contabilidad de tokens; máximo componente a componente es necesario incluso con datos distintos a T40.

### T42 · Agente Popularizer
- **Estado**: done
- **Depende de**: T41
- **Alcance**: prompt en `prompts/popularizer.md`, salida con `level_curious`, `level_amateur`, `level_technical`. Caso de uso `PopularizeReading`, solo para `interest_score >= 4`. Crea `Finding` en estado no publicado. **Refactor previo: extracción de `AgentRunner` en `application/agents/runner.py`** centraliza el patrón `authorize → run_agent → build → record_call` para los tres agentes. Suite de T41 pasa sin tocar ni un aserto (24 asertos de `test_read_item.py` intactos). El refactor en commit propio `ecd93fc` anterior a la implementación del Popularizer.
- **Implementación completada**: 2026-09-17 · 647 passed, 3 deselected · Revisión de gasto aprobada. Bug detectado por tester: `run-item` cerraba `Run` como `COMPLETED` cuando el Popularizer fallaba tras una lectura correcta, porque `had_reading` era heredado de cuando solo existía Reader. Arreglado en `application/use_cases/`.
- **Humo manual completado**: 2026-09-17 · 665 passed, 3 deselected · Popularizer: `outcome=popularized`, **2 intentos**, **9.120 tokens**, `duration_ms=32886`. Intento 1 metió saltos de línea literales dentro de JSON en `level_technical`; `json.loads` rechazó con `Invalid control character`. **Reparador JSON implementado** en `extract_json_object`: repara caracteres de control crudos (`\n`, `\r`, `\t`) dentro de literales de cadena, costo cero. **Prompt versionado a `popularizer-v2`.**  Desglose por intento (máximo componente entre `usage` y suma de `model_usage`): 1º ~4.599 tokens, 2º ~4.521 tokens. **Por intento: ~4.560 reales frente a 7.000 estimados** (margen 1,5×). Reader mismo día: 1 intento, 3.056 tokens. 
- **Aritmética de la noche observada**: pool Reader+Popularizer = 240.000; Reader 40 × 3.056 = 122.240; quedan 117.760. A 4.560 por candidato sin reintento → **~26 candidatos**. Si todos reintentan → **~13 candidatos**. Plan estimaba ~38; **la cifra real es sensiblemente peor**. El problema no es la estimación, es la **tasa de reintento**. `extract_json_object` la debe acotar en `popularizer-v2` forward. **No ajustar `popularizer_estimated_tokens` aún**: una observación no basta; regístralo como dato de calibración para T60.
- **Datos para T60**: gasto lateral del CLI (Haiku para control de sesión) pasó de 929 a 1.163 tokens (+234), causando fallo del humo de T40. El SDK sigue en 0.2.153; el CLI está en 2.1.274 sin registro previo. Los humos ahora registran versión de CLI y SDK. **El gasto lateral del CLI se mueve entre versiones y también entre llamadas dentro de la misma versión** (T43 observó: CLI 2.1.274 gasta 929, 1.163, 1.240 Haiku en tres llamadas sucesivas con SDK 0.2.153; no correlaciona limpiamente con tamaño de prompt). Sin control desde configuración, causa desconocida. ~82 sesiones/noche × ~1.163 = ~95.366 tokens (casi 25% del presupuesto).
- **Nota**: cuatro decisiones abiertas nuevas en OPEN_DECISIONS.md: escape de marca de cierre, distinción de motivos de DISCARDED, desbordamiento de una llamada, y ValueError en terminal_status_for. Una nueva: **tasa de reintento del Popularizer** (una observación de dos intentos no basta). Dos deudas nuevas en TECHNICAL_DEBT.md: arreglo del Run incompleto para T43, timeout compartido Reader/Popularizer, y **gasto lateral del CLI que no ata calibración a versión usada**.
- **Cerrada: 2026-09-17** · Hallazgo real del Popularizer producido. Tres humos ejecutados. Suite: 665 passed.

### T43 · Agente Editor
- **Estado**: done
- **Depende de**: T42
- **Alcance**: prompt en `prompts/editor.md` (versionado `editor-v1`). Recibe todos los `Finding` candidatos de la noche en **una sola llamada** con Opus; devuelve lista de `item_id` a publicar, `confidence` y motivo. Caso de uso `EditNight` que publica los aprobados y cierra el `Run` como `COMPLETED` (único cierre de COMPLETED del proyecto). Timeout rol-dependiente `editor_timeout_s = 300` s. Estimación lineal `base + N×per_candidato`. Validador de reserva presupuestaria cierra al cargar configuración.
- **Implementación completada**: 2026-09-17 · **720 passed, 4 deselected** · Tests: publica solo los aprobados; si `BudgetGuard` no permite la llamada, ningún `Finding` se publica y el `Run` queda `partial`. Flujo `_edit_one_night` es punto único que devuelve `COMPLETED`, corrigiendo deuda de T42.
- **Dos revisiones completadas · ambas aprobadas.** Ronda 1 general: estructura de `EditNight`, transiciones de estado, contabilidad de tokens. Ronda 2 control de gasto: validador de reserva, estimación lineal, invariante de `hard_stop` preservada, timeout rol-dependiente no quiebra ADR 0005 (segundo término del `min()` es siempre `seconds_until_hard_stop`).
- **Humo manual ejecutado**: 2026-09-17 · rama `fix/t43-smoke-assert` · **Primer dato real de Opus del proyecto.** Resultado: `outcome=edited`, 3 candidatos, **1 intento**, **3.381 tokens**, `duration_ms=5361`, CLI 2.1.274, SDK 0.2.153. Estimado: 6.100 (base 4.000 + 3×700 = 6.100). **Margen conservador 1,8×**, coherente con Reader y Popularizer. **Datos de calibración para T60**: (1) Desglose de tokens por ADR 0007: Opus 2 (input) + 1889 (cache_creation_input) + 232 (output) = 2.123; Haiku (CLI) 1.240 + 18 = 1.258; total 3.381. Una sola observación: insuficiente para separar componente fijo de variable con N=3 candidatos, requiere segundo punto de calibración con N distinto. (2) Variabilidad de gasto lateral de Haiku: misma sesión, misma versión CLI/SDK, tres llamadas (`test_sdk_smoke`, `test_popularize_smoke`, `test_edit_night_smoke`) → Haiku 929, 1.163, 1.240 tokens respectivamente. **No correlaciona limpiamente con tamaño del prompt** (Popularizer 2.130 tokens de cache_creation gastó menos Haiku que Editor 1.889). (3) Calidad editorial: publicó 2/3 con confidence 0.85 y 0.80; descartó 1 (señuelo "Medición de metalicidad en cúmulo"), motivos pertinentes. **Evidencia a favor de entrada acotada a `title+level_curious`**: decisión correcta con contexto limitado. Suite T43 pasa íntegra. Bug descubierto y arreglado en test: comparación de `Finding` por igualdad de dataclass en lugar de por `id`.
- **Decisiones resueltas**: `terminal_status_for(EDITOR_ALREADY_CALLED)` se maneja en `cli.py`, no en `budget.py`. El `ValueError` es correcto como mecanismo defensivo.
- **Decisiones abiertas actualizadas**: línea 77 en OPEN_DECISIONS.md ahora tiene primer dato de calibración de Opus (registrar que requiere segundo punto con N distinto para separar fijo/variable); línea 75 ahora con evidencia a favor de entrada acotada (decisión correcta sobre 2/3 con señuelo); línea 80 sobre variabilidad de CLI también refinada.
- **Deuda técnica**: saldada la de T42 sobre cierre del Run; parcialmente la de timeout compartido (Editor ya tiene el suyo). Nuevas: `unpublished_for_run` sin `ORDER BY`, regresión de gasto ante `IntegrityError`, `EditNight.run_id` desacoplada de guard, `timeout_for_call(role)` parámetro opcional. **Lección técnica nueva**: comparar entidades de dominio por identidad (`id`), nunca por igualdad de dataclass cuando han pasado por repositorio o mutaron — bug descubierto en humo de T43.
- **Cerrada: 2026-09-17**

### T44 · Orquestador `run-night`
- **Estado**: done
- **Depende de**: T43
- **Alcance**: caso de uso `RunNight`: crea `Run`, ingesta (T20), Reader sobre todos los `new` en orden de llegada hasta `max_items_per_night`, Popularizer sobre candidatos, Editor al final, cierre del `Run` con estado y métricas. Manejo de `hard_stop` cancelando lo que esté en vuelo. Logging estructurado (JSON) por ítem y por agente.
- **Implementación completada**: 2026-09-17 · 773 passed / 4 deselected · **Dos revisiones completadas, ambas APROBADAS sin bloqueantes.** Ronda 1: estructura de `RunNight`, degradación de estado, cierre de `Run` huérfano, vigía de `hard_stop`, contabilidad de tokens con máximo componente a componente, línea de métrica de cierre con contadores. Ronda 2: validador de presupuesto inicial, códigos de salida 0/1/7/8, reconocimiento de timeout de ejecución frente a `hard_stop`, cortacircuitos `max_consecutive_failures = 5`, reconciliación de tokens (suma `AgentCall`, nunca lee `Run.tokens_used`).
- **Hechos saldados**: Política de `Run` huérfano: `run-night` cierra como `KILLED` e invoca siguiente; `run-item` sin cambios. Deuda de T41 sobre `KeyboardInterrupt` bloqueador. Mitigación (a) de T40 sobre fugas sin `ResultMessage`, acotada a ~50.000 tokens por noche con fallos monótonos.
- **Decisiones abiertas nuevas**: 10 registradas en `OPEN_DECISIONS.md`, líneas 88–100.
- **Decisiones resueltas en esta sesión**: nº 20 (huérfano, precisada para `run-night`), nº 41 (código 2 ya falso), nº 48 (reconciliación por suma), nº 57 (validador de presupuesto).
- **Deuda técnica marcada**: saldada la del Run incompleto por `KeyboardInterrupt`, precisado el alcance real del cortacircuitos (fallos monótonos, no intermitentes), añadidas 3 deudas nuevas (docstring de `Run`, camino FAILED sin contadores, cobertura de tests).
- **Primera ejecución real**: 2026-09-18 11:44 · fuera de ventana (06h 44m después del cierre a 04:45) · `deadline_s = 0`, `stop_reason = outside_window`, Run cerrado como `KILLED`, 0 tokens gastados, 0 ítems procesados · validación del `hard_stop`: funcionó exactamente como se diseñó, ninguna llamada a Claude llegó a autorizarse.
- **Segunda ejecución real (noche completa)**: 2026-09-18 12:34 · **fuera de ventana nominal** (00:00–04:45) con `hard_stop` ampliado a 23:59 para validación del circuito (ya revertido) · Run.status = `completed`, 246.608 de 300.000 tokens (82,2%), 39 ítems leídos, 1 fallo (2,5%), 14 candidatos, **10 hallazgos publicados** · ciclo completo validado: ingesta → Reader → Popularizer → Editor → persistencia. Dos hallazgos de calibración documentados en `docs/CALIBRACION.md`.
- **Cerrada: 2026-09-18** · Hecho cuando completo: noche completa en local, contra suscripción, Run.status = completed, hallazgos publicados. Salvedad: no en ventana real (ampliada para validación sin esperar medianoche). Suite: 773 passed. Registro del consumo semanal en Settings > Usage sigue pendiente del autor (T44 parte 2).

---

## Bloque 5 — Web

### T50 · API de lectura
- **Estado**: done
- **Depende de**: T11
- **Alcance**: FastAPI con `GET /health`, `GET /findings?page=&size=`, `GET /findings/{id}`. Solo `Finding` publicados. Sin escritura. CORS para `localhost:3000`.
- **Hecho cuando**: tests de API contra base de datos de compose. Puede empezar en paralelo con Bloque 4 si el autor lo aprueba.
- **Implementación completada**: 2026-09-17 · **815 passed, 4 deselected** · `-m db` (solo tests contra PostgreSQL): **134 passed**. **Dos revisiones completadas.** Ronda 1 rechazada: falta de red de tests en el manejo del error 500 (traza no se valida, solo respuesta estructurada), y comentario falso en `cors_origins`. Ronda 2 aprobada: suite ampliada con contrastación de mutantes (borrar el manejador → tests fallan; activar `debug=True` → traza completa aparece), validando que ambas garantías se preservan. **Qué se implementó**: tres métodos de `FindingRepository` (`published_page`, `count_published`, `get_published`) con filtro `published_at IS NOT NULL` dentro de sentencia, orden `DESC` con desempate por `id`, sin migración nueva (índice parcial de T11 cubre la consulta). Validación en `EXPLAIN` sobre 60.000 filas con ejecución real. `ListPublishedFindings` y `GetPublishedFinding` casos de uso con **segunda barrera** de validación (revalidan `is_published` en memoria) y fallo cerrado si falta `Item`. FastAPI app con `deps.py` (sesión nunca hace `commit`, cierra con `rollback`; no usa `unit_of_work` a propósito), `schemas.py`, `routes/health.py`, `routes/findings.py`. Contrato: `page` base 1 (default 1), `size` 1–50 (default 20), respuesta `{"items": [...], "page", "size", "total"}`. Página fuera de rango → `200` con lista vacía. `404` indistinguible entre «no existe» y «no publicado» (mismo cuerpo, mismas cabeceras, para que nadie pueda enumerar candidatos que Editor rechazó). `500` cuerpo fijo `{"detail": "internal error"}`, traza solo en stderr. `/health` comprueba BD: `503` si no responde. No expone `confidence`, `run_id`, `item_id`. CORS: `Settings.cors_origins` (por defecto `["http://localhost:3000"]`), solo `GET`. Script `seed_demo.py` fuera del paquete con guarda `NOCTURNA_ALLOW_SEED=1`. Arranque: `uv run uvicorn nocturna.api.app:create_app --factory --reload --port 8000`. **Probado contra PostgreSQL de compose con BD en marcha y con BD caída**, y con tests que siembran datos propios sin depender de la noche real (las fixtures crean `Finding` por el mismo camino de dominio que Editor).
- **Decisiones abiertas nuevas**: 11 registradas en `OPEN_DECISIONS.md`, líneas 88–99.
- **Deuda técnica nueva**: 4 registradas en `TECHNICAL_DEBT.md`: validación de log en test de error, resolución del `Settings` contra entorno en tests, mezcla de mutaciones en docstring, no-idempotencia de seed.
- **Cerrada: 2026-09-17** · API de lectura funcional, probada contra el PostgreSQL de compose y con la base de datos caída. Ningún test depende de la noche real: las fixtures siembran por el mismo camino de dominio que el Editor. La noche real sigue siendo el «Hecho cuando» pendiente de T44, no de esta tarea.

### T51 · Web Next.js
- **Estado**: done
- **Depende de**: T50
- **Alcance**: proyecto con App Router, TypeScript, Tailwind. `/` feed paginado, `/hallazgo/[id]` con selector de nivel (curioso / aficionado / técnico) y enlace a arXiv. Banner permanente de análisis automatizado. Fetch server-side a la API de lectura. Sin llamadas a Claude, sin auth, sin admin.
- **Implementación completada**: 2026-09-18 · **37 tests en verde** · `pnpm lint` y `pnpm build` verdes **con la API parada** · **Lighthouse accesibilidad 100/100** en `/` y 100/100 en `/hallazgo/[id]` (build de producción, preset desktop, sin auditorías fallidas; umbral exigido ≥ 90).
- **Stack**: Next 15.5.25, React 19.1.0, Tailwind 4.3.3 (v4, tokens en `@theme`), creada con `create-next-app@15` y limpiada de morralla de plantilla.
- **Qué se implementó**: SSR dinámico (`force-dynamic` + `cache: "no-store"`), no ISR. Frontera de IO única en `src/lib/api/client.ts` (mapea `404` → `not_found`, todo lo demás → `unavailable`). Selector de nivel por URL (`?nivel=`), sin estado de cliente, legible sin JavaScript. Todos Server Components salvo `app/error.tsx`. Banner permanente en layout raíz, aparece en todas las rutas incluidas `not-found` y `error`. Selección de nivel reflejada en URL compartible. Paleta y tipografía placeholder con contraste verificado (peor par 8,28:1). Zona horaria y locale fijos (`Europe/Madrid`, `es-ES`) para no desajustar SSR ≠ cliente.
- **Una pasada de revisión**: APROBADO sin bloqueantes, con **cinco correcciones ya aplicadas**: (1) Bug de Request Memoization en Next: `generateMetadata` duplicaba peticiones. Arreglado envolviendo `fetchFinding` en `React.cache()`, verificado contando peticiones en log de uvicorn (una sola por visita). (2) Backend vulnerable: `?page=100000000000000000000` tumbaba API con SQL error. Arreglado en web con regex `/^\d{1,6}$/` y tope `MAX_PAGE = 999_999`, congelado con tests; backend sigue sin protección (es de T50/T60). (3) Dos huecos en guard `no-claude-in-web.test.ts`: allowlist demasiado amplia en `client.ts`, escaneo no cubre `next.config.ts` ni invariantes de "no rutas de API en cliente", documentados en TECHNICAL_DEBT.md. (4) Tests de paginación verifican que `?page=` fuera de rango devuelve 200 con aviso (no 404), congelado. (5) `aria-current` borrado del contador (solo para enlaces).
- **Datos para la web verificados con datos sembrados por `seed_demo.py`**: No con la noche real (eso es «Hecho cuando» pendiente de T44). Las fixtures crean `Finding` por el mismo camino de dominio que Editor; el hallazgo de T51 es estructural: web se renderiza sin toque a la API de Claude, Lighthouse 100/100 accesibilidad.
- **Decisiones abiertas nuevas**: 10 registradas en `OPEN_DECISIONS.md` (SSR vs ISR, divergencia de `.claude/agents/frontend.md`, ramas de error HTTP 200, paleta, zona horaria, tema claro/oscuro, textos de UX, E2E, versión de Next, página fuera de rango).
- **Deuda técnica nueva**: 3 registradas en `TECHNICAL_DEBT.md` (backend no acota `page`, huecos en guard, `aria-current`).
- **Cerrada: 2026-09-18**

---

## Bloque 6 — Cierre de fase

### T60.b · Robustez de ingesta frente a 406
- **Estado**: done
- **Depende de**: T20, T44
- **Alcance**: reintento con backoff exponencial ante fallos transitorios de arXiv (406, 429, 5xx, errores de transporte). Motivación: 406 observado 2026-09-21 con cuerpo vacío, sin Retry-After, cabeceras Fastly/Varnish (CDN rechaza, no la aplicación), transitorio (misma consulta devolvió 200 minutos después). Política de reintento configurable: `retry_max_attempts`, `retry_base_delay_s`, `retry_max_elapsed_s` en `pipeline.toml` bajo `sources.arxiv`. Eventos de log estructurados: `arxiv.retry`, `arxiv.retry_recovered`, `arxiv.retry_exhausted`. Tests: 848 passed, 4 deselected (baseline 815). Revisión completada: ronda 1 (presupuesto de tiempo) rechazó con correcciones; ronda 2 (general) aprobó. Sin bloqueantes en ninguna pasada.
- **Hecho cuando**: suite en verde con tests nuevos de revisión, ambas pasadas de revisión resueltas (correcciones aplicadas), autor repite verificaciones V1 (ingesta trae ítems) y V3 (comprueba configuración), documentación completa (ADR 0009, PLAN_TAREAS, OPEN_DECISIONS, ARCHITECTURE, CALIBRACION), y commit preparado (no ejecutado aún).

### T60.c · Vía de ingesta que no dependa del CDN que nos rechaza
- **Estado**: done
- **Depende de**: T60.b
- **Alcance**: la ingesta pasa a OAI-PMH (`oaipmh.arxiv.org`, `metadataPrefix=arXivRaw`) como vía por defecto, con la API de `/api/query` conservada y seleccionable en `pipeline.toml` (`ingest_via`). Prerrequisito de las catorce noches de T60: sin ingesta, el pipeline consume la cola de `Item` en estado `NEW` y en dos noches corre en vacío.
- **Motivo**: cuatro episodios de 406 entre el 2026-09-21 y el 2026-09-25 (cuerpo vacío, sin `Retry-After`, cabeceras de Fastly/Varnish), **todos más largos que la ventana de reintento de T60.b y ninguno recuperado**. La cola bajó de 49 a 9 ítems en una noche. Descartados como causa: `User-Agent`, `Accept`, `Accept-Encoding`, versión HTTP, codificación de `%3A`, `max_results` y cliente síncrono frente a asíncrono.
- **Qué se implementó**: `infrastructure/arxiv/transport.py` (petición con reintentos, compartida por las dos vías, extraída de `client.py` sin cambio de comportamiento), `oai.py` (parser que produce el MISMO `ArxivEntry`), `oai_client.py` (sets explícitos por categoría, ventana `from`/`until` con solape, dedup de cross-list, techo `max_requests_per_fetch`), cinco claves nuevas en `ArxivConfig` sin defaults, y `arxiv_source_from_config` en `cli.py` como única elección de vía.
- **El dominio no cambia**: `external_id`, `published_at`, `categories` y `source` son idénticos por las dos vías, verificado contra un paper ya presente en la base y congelado en `tests/test_oai_atom_equivalence.py`. Sin migración. El relleno del 2026-09-25 lo confirmó en producción: **70 ítems nuevos, 0 duplicados** sobre 92 filas existentes.
- **Diferencia conocida**: `arXivRaw` devuelve LaTeX crudo (`\AA`) donde el Atom da Unicode (`Å`). Aceptada y congelada con test; el abstract lo lee un LLM y la web publica los niveles del Popularizer, no el abstract crudo.
- **Tests**: suite **954 passed, 4 deselected** (partía de 887). Nuevos: `test_oai_parser.py` (20), `test_oai_client.py` (19), `test_oai_atom_equivalence.py` (7), dos de dedup entre vías en `tests/db/test_ingest_dedup.py`, más los de configuración y fábrica. Fixturas OAI reales capturadas y documentadas en `tests/fixtures/arxiv/README.md`. Cero red y cero esperas reales.
- **Lo que NO se hizo**, por escrito en ADR 0010: no se imita la huella TLS de `curl`, no se falsean cabeceras, no se rota el `User-Agent`, no se usan proxies, y no se cambia de biblioteca HTTP «porque `urllib` pasa» — eso sería evadir la protección del CDN aunque la biblioteca sea de la estándar.
- **Hecho cuando**: dos pasadas de revisión aprobadas, y **una noche automática completa con ingesta por OAI** (`items_fetched > 0` y `run.status = completed`). Hasta entonces la vía está probada en `--dry-run`, no en una noche real.
- **Dos pasadas de revisión, ambas atendidas**: la de presupuesto de tiempo aprobó con reservas; la general **rechazó** por un bloqueante real, ya corregido: el suelo del filtro fino era `since`, y como un lote con `datestamp = D` trae envíos desde `D-2 18:00 UTC`, se descartaba **el 44 % de las novedades (8 de 18 medidas sobre una cosecha real), para siempre**, mientras el ADR, el TOML y el docstring afirmaban que «una noche perdida se recupera sola». Corregido bajando el suelo dos días respecto a la ventana y añadiendo el contador `filtered_out` al evento `arxiv.oai_harvest`, que es lo que habría hecho visible el defecto la primera noche.
- **Otras correcciones de las revisiones**: cota de duración **total** por petición con `anyio.fail_after` (`httpx.Timeout` no la da: acota cada operación por separado, así que una respuesta que gotea mantenía un `get` vivo indefinidamente y solo la paraba el vigía, perdiendo la noche entera sin aviso); una fecha RFC-2822 sin zona horaria ya no tumba la cosecha completa (`parsedate_to_datetime` devuelve naive con `-0000` y reventaba al comparar); el techo de peticiones cuenta **intentadas**, no servidas; validador que cruza el peor caso de la ingesta con `limits.run_timeout_s` y cota `le=7` a `oai_lookback_days` (subirlo no trae novedades, solo gasta peticiones en lotes que el suelo descarta); validador que rechaza al cargar una categoría sin set de OAI conocido; el campo del log ya no se llama `start` cuando lleva un set; y el aviso de truncado nombra las dos palancas posibles.
- **Tests tras las revisiones**: **970 passed, 4 deselected**. Incluye el test que congela el bloqueante contra la cosecha real, el de cancelación durante el backoff en la vía OAI (validado con mutante: sin `cancel()` falla) y el del mutante superviviente que la revisión encontró (`<resumptionToken></resumptionToken>` tiene `.text is None`, así que no ejercitaba la rama que decía probar).
- **Pendiente**: el commit. La noche del 2026-09-26 (`items_fetched = 31`, `completed`) cumplió el criterio; el código sigue sin commit en la rama `feat/t60c-ingesta-oai`.

### T60 · Calibración de estabilidad nocturna
- **Estado**: done
- **Cierre (2026-09-28)**: redefinida por decisión del autor como verificación de estabilidad; el "Hecho cuando" de abajo queda sustituido. La calibración por % semanal es imposible porque la suscripción es compartida con otros proyectos (ADR 0011). El control es el tope absoluto `budget.nightly_tokens = 300000`, medido en `agent_calls`. Evidencia: cuatro noches automáticas por `launchd` (2026-09-25 a 2026-09-28), tres `completed` y una `partial` (la del 25, por fallo de ingesta en la vía `api`, antes de T60.c); gasto del 75,6 % al 84,1 % del tope; ningún `hard_stop`; sin avisos ni errores en los logs del 26 al 28. Cola `new = 0` tras la noche del 28: desde ahí el pipeline depende de la ingesta diaria. Datos por noche en `docs/CALIBRACION.md`.
- **Depende de**: T44, T51
- **Alcance**: el autor ejecuta `run-night` **automáticamente con `launchd`** durante catorce noches. El agente dispara a las 00:05, invocando un envoltorio shell (`backend/scripts/run-night-scheduled.sh`) que valida precondiciones, impide dobles lanzamientos en la misma ventana (centinela), y captura logs en ficheros separados. Cada mañana el autor sigue el procedimiento de cinco pasos documentado en `docs/CALIBRACION.md` y anota una fila en la tabla. Al final se ajusta `pipeline.toml` según las siete reglas de calibración, se decide el umbral de `interest_score`, y se cierra esta tarea con un ADR que documenta los criterios aplicados.
- **Instalación del agente**: `launchd` se configura una sola vez al inicio, materializando el plist desde plantilla con `sed`. Descarga automática anterior si hubiera, `launchctl bootstrap` del nuevo. Requiere `mkdir -p ~/nocturna-logs` previo y `sudo pmset repeat wakeorpoweron` para despertar a las 00:00. El portátil debe estar **enchufado** todas las catorce noches.
- **Modelo de ejecución**: automático con `launchd` (decidido por el autor). Alternativas vistas durante el plan: manual cada noche (catorce despertares a medianoche), `cron` (descartado: macOS 15 exige Full Disk Access para `cron`, en silencio sin ejecutar; `crontab` no es versionable), híbrido (manuales + fines de semana). La decisión es reversible: con `cron` la línea sería `5 0 * * * /ruta/al/envoltorio`, nada más cambia. Prescripción para T60: **único modo de lanzar una noche es a través del envoltorio**.
- **Tabla**: mismas columnas que antes, ahora con "lanzamiento" columna que pasa de `manual` a `planif.` desde la primera noche. Columnas nuevas de telemetría: `CLI` y `SDK`, versión de cliente y SDK en esa noche (ruptura de baseline si cambian).
- **Fase de preparación cerrada 2026-09-18**: `docs/CALIBRACION.md` reescrito como runbook operacional (instalación del agente, interpretación de códigos de salida, archivos de log, desinstalación). `backend/scripts/night_report.sql` y `night-report.sh` probados contra la primera noche real. `config/pipeline.toml` reparametrizado con Editor tras dos observaciones reales (N=3, N=14). Primera noche real (2026-09-18 12:34) ejecutada fuera de ventana nominal (validación con `hard_stop` ampliado): 246.608 tokens (82,2%), 39 ítems leídos, 14 candidatos, 10 hallazgos publicados, pool al 97,8%.
- **Nota sobre prompt versions**: (1) `reader-v2` con abstract en `<abstract>`/`</abstract>`; (2) `popularizer-v2` con reparador JSON; (3) `editor-v1` entrada acotada a `title+level_curious`. Cada uno rompe comparabilidad con datos previos — exactamente para lo que existe `prompt_version` en `AgentCall`. T60 debe anotar este cambio de baselines al comparar.
- **Hecho cuando**: `pipeline.toml` tiene valores calibrados con mínimo 7 noches limpias, se cierra la tabla con catorce observaciones, y un ADR documenta el criterio de decisión final.

### T60.a · Lanzamiento planificado nocturno de `run-night`
- **Estado**: done
- **Cierre (2026-09-28)**: Agente launchd instalado con `launchctl bootstrap`, ejecutado sin fallos cuatro noches seguidas (2026-09-25 a 2026-09-28), disparos a las 00:05 sin dobles lanzamientos, centinela funcional. Suite de tests verde (841 passed, 4 deselected). V1 y V3 no se registraron como tales: las cuatro noches reales bajo `launchd` cubren V1, y los dos lanzamientos manuales fuera de ventana (2026-09-21 08:46 y 2026-09-24 10:48) terminaron en `killed` con cero tokens, que es lo que V3 comprueba.
- **Depende de**: T44, T51
- **Alcance**: documentación de lanzamiento planificado con `launchd` (plan, instalación, mitigaciones, desinstalación). Envoltorio `backend/scripts/run-night-scheduled.sh` (fija PATH, valida precondiciones, centinela, logs separados). Plantilla del plist `backend/scripts/com.nocturna.run-night.plist.template`. Suite de tests: 22 tests nuevos (entorno, precondiciones, centinela, códigos de salida), suite total 841 passed, 4 deselected. Dos pasadas de revisión (código): ambas aprobadas con correcciones. Hallazgos documentados en `docs/CALIBRACION.md` (tres bloques nuevos: lanzamiento planificado reescrito, centinela alcance/límites, versión CLI baseline).
- **Contrato del envoltorio**: variables `NOCTURNA_LOG_DIR` (defecto `$HOME/nocturna-logs`), `NOCTURNA_WAIT_S` (300), `NOCTURNA_PATH` (PATH de `uv`/`claude`). Códigos de salida: 0–8 propagados, 75 precondiciones, 76 ya lanzado, 77 entorno inválido. Logs: `night-YYYYMMDD.out.log` y `night-YYYYMMDD.err.log` (dos ficheros, no `.jsonl`).
- **Desviación más grave**: runbook viejo (`CALIBRACION.md` líneas ~152-222) mandaba instalar plist crudo sin envoltorio ni centinela. Reescrito completamente. Dos soluciones cruzadas: (a) `mkdir -p ~/nocturna-logs` como **paso previo obligatorio**, coste de tener directorio sin estar en repo; (b) aviso de migración si hubiera agente anterior con etiqueta duplicada.
- **Segundo bloque**: centinela `~/.launched-YYYYMMDD` es guarda por convención. Documentado el alcance real (qué cubre, qué no), enlace con "Regla de noches fallidas".
- **Tercer bloque**: versión CLI/SDK varía entre versiones sin que cambie nada nuestro. Cada noche se anota en tabla como `CLI` y `SDK`. Cambio de versión entre noches = ruptura de baseline = se excluye del cálculo de constante `k`.
- **Hecho cuando**: suite de tests verde, dos pasadas de revisión aprobadas, documentación completa, **y el autor ha ejecutado las verificaciones V1 y V3 del runbook e instalado el agente con `launchctl bootstrap`**. Mientras la instalación no esté hecha y verificada en la máquina, esta tarea no está cerrada: el código puede estar perfecto y la noche seguir sin dispararse.
- **Pendiente**: nada. El agente está instalado y ha disparado solo del 2026-09-25 al 2026-09-28.

### T61 · Retrospectiva y plan de fase 2
- **Estado**: done
- **Depende de**: T60
- **Alcance**: lista de deuda técnica real (no anticipada), decisiones abiertas que la fase 1 ha resuelto o hecho irrelevantes, y borrador de `PLAN_TAREAS.md` para fase 2. **Objetivo central de fase 2 sin resolver**: ¿qué cuenta como descubrimiento? (entrada de OPEN_DECISIONS.md). Fase 1 (paper_explained sobre abstracts) demuestra viabilidad del pipeline, no responde a la pregunta de fondo del proyecto: ver cómo Claude puede hacer descubrimientos.
- **Hecho cuando**: el autor aprueba el plan de fase 2.
- **Cierre (2026-09-28)**: el autor aprobó el plan de fase 2 (T70–T78) y ADR 0012. Triaje aplicado: `OPEN_DECISIONS.md` (100 entradas abiertas clasificadas: 16 resueltas, 8 sin objeto, 51 a fase 2, 25 de operación; 17 decisiones nuevas de fase 2) y `TECHNICAL_DEBT.md` (14 entradas a cerradas, 3 devueltas a abiertas, 4 marcadas bloqueantes si un agente recibe herramientas). Revisión: aprobada con correcciones, aplicadas.

---

## Fase 2

Aprobada el 2026-09-28 (T61). Decisiones de fondo en [ADR 0012](adr/0012-fase-2-tension-frente-a-catalogo.md). Objetivo: publicar como `catalog_tension` las tensiones entre lo que dice un paper de astro-ph.EP sobre un objeto y las medidas previas del NASA Exoplanet Archive. La discrepancia la calcula Python; Claude solo redacta a partir de números ya calculados. Local, sin despliegue.

**Estado del bloque**: T71, T71.b, T71.c, T72, T73, T74, T79, T80, T81, T82, T87, T83, T88, T89, T90, T91 y T77 cerradas; siguiente: T84–T86 (vía del archivo, decisiones del autor del 2026-10-01). T75 espera 7 noches con `reader-v3`. Vía (c) adoptada en firme por el autor el 2026-09-29 (ADR 0013; cierre de T71.b): la medida del paper y su atribución a un planeta las produce el Reader, y Python calcula σ. El parser determinista de T71 queda como herramienta del experimento, no como base de T73. Plan revisado y aprobado por el autor el 2026-09-29. Orden: T71.c → T73 → T74 → T72 → T75 → T76 → T77 → T78. El criterio de cierre de fase 2 está abierto (decisión del autor, ver T78).

### T70 · `page` sin tope en `GET /findings`
- **Estado**: done
- **Depende de**: T61
- **Alcance**: acotar `page` en `backend/src/nocturna/api/routes/findings.py:35` (`Query(ge=1)` sin `le`). Hoy `?page=100000000000000000000` llega a PostgreSQL como `NumericValueOutOfRange` y devuelve un 500. Tope igual al `MAX_PAGE = 999_999` que ya aplica la web desde T51, para que las dos capas digan lo mismo. Salda la deuda "backend no acota `page`" de `TECHNICAL_DEBT.md`.
- **Pregunta abierta en la tarea**: por encima del tope, ¿`422` (validación de FastAPI) o `200` con lista vacía? Opción reversible propuesta: `422`, porque es una entrada inválida y no una página sin resultados.
- **Hecho cuando**: tests `-m db`: `page=999999` da 200 con lista vacía; `page=1000000` y `page=10**20` dan la respuesta acordada, sin 500 ni traza en el cuerpo. Quitar el `le=` hace fallar el test. Deuda marcada como saldada y § API de `ARCHITECTURE.md` actualizado.
- **Cierre (2026-09-28)**: `MAX_PAGE = 999_999` y `Query(ge=1, le=MAX_PAGE)` en `api/routes/findings.py`; por encima, `422` (decisión del autor). Tres tests nuevos en `tests/db/test_api_findings.py`; mutación manual (quitar `le=`): `page=1000000` pasa a 200 y `page=10**20` a 500, y los tests fallan. Web: solo comentarios en `pagination.ts`. Suites: 974 passed / 4 deselected; `-m db` 141 passed; web 37 tests, lint verde.

### T71 · Experimento de viabilidad del cruce con el NASA Exoplanet Archive (sin Claude, sin tokens)
- **Estado**: done
- **Depende de**: T61
- **Alcance**: script fuera del paquete, en `backend/scripts/` como `seed_demo.py`, solo Python y solo lectura sobre la BD. No importa `claude_agent_sdk` ni gasta tokens. Pasos: (1) toma los `Item` de astro-ph.EP que ya están en BD y su `Reading`; (2) empareja cada nombre de `Reading.objects` con el Exoplanet Archive vía TAP (sin autenticación), con espaciado de cortesía entre peticiones; (3) para los emparejados, recupera las soluciones publicadas de la tabla de soluciones múltiples; (4) obtiene el valor numérico del paper por las dos vías decididas en ADR 0012: **(a)** parser determinista sobre el abstract y sobre `Reading.claims`, y **(b)** la solución del propio paper si el archivo ya la tiene ingerida; (5) calcula la discrepancia en σ entre el valor del paper y las medidas previas. Informe con recuentos de: ítems EP, ítems con `objects`, nombres emparejados, planetas con ≥ 2 soluciones previas, casos con valor del paper disponible **por la vía (a), por la (b) y por ambas**, casos perdidos por falta de ese valor, y distribución de σ por tramos. El experimento no fija umbral: da los tramos para que decida el autor. Salida adicional: tensiones esperadas por noche, que fijan el valor inicial de la reserva de T75 (orden de 20–40k, provisional).
- **Preguntas abiertas en la tarea**: (a) con las cifras de (a) y (b), qué vía se adopta; la vía (c) —que el Reader extraiga cantidades estructuradas, con cambio de prompt y gasto— solo se valora si (a) y (b) recuperan pocos casos, y requiere decisión expresa del autor. (b) Qué parámetros se contrastan. (c) Si hay algún umbral para seguir, además de "cero".
- **Hecho cuando**: script versionado; un test en subproceso comprueba que `claude_agent_sdk` no entra en `sys.modules`; nota de cierre de T71 en este fichero con las cifras; el autor decide seguir o replantear y qué vía de valor del paper se adopta. Si las tensiones reales son cero, se replantea y T72–T78 no se desbloquean.
- **Cierre (2026-09-28)**: script `backend/scripts/exoplanet_viability.py` (tres pasadas de revisión; la tercera, aprobada). Ejecución real: 42 peticiones, 274,5 s, 0 tokens. 74 ítems EP con `Reading`, 65 con `objects`, 182 nombres (169 distintos). Emparejamiento: 20 planeta, 4 anfitriona de un planeta, 12 anfitriona multiplanetaria (ambiguo), 146 sin emparejar. Valor del paper: vía (a) 1, vía (b) 0. Perdidos: ambiguo 12, `no_match` 145, `sin_objects` 9, `sin_valor_de_papel` 23, `tope_alias` 1. Tramos σ (fórmula provisional): un único caso en 3–5 (σ = 4,88), **falso positivo** verificado a mano (masa de RX J0534.0-0221 b atribuida a TWA 7 b, citado como comparación). Revisión manual: TOI-2109 b sin tensión (σ = 0, mismos valores que Wong et al. 2021); V1298 Tau b y e ≈ 3σ frente a la solución por defecto (Livingston et al. 2026), perdidos por la regla de un solo planeta. **Decisión del autor: opción A** — vía (c), atribución por el Reader, con prueba manual previa (ADR 0013); criterio de cierre de fase 2 a revisar.

### T71.b · Prueba manual de la vía (c): atribución de medidas por el Reader
- **Estado**: done
- **Depende de**: T71
- **Alcance**: comprobar, antes de construir nada, que el Reader atribuye bien cada medida (planeta, parámetro, valor, errores, unidad) en los abstracts de T71 con cifras (al menos 2609.20748, 2609.26894, 2609.30038 y 2609.17025), con un prompt y un esquema de salida experimentales. Humo `-m manual` con `NOCTURNA_ALLOW_REAL_CLAUDE=1`, gasto máximo fijado en el plan. Python calcula σ contra el archivo con las medidas atribuidas (ADR 0013).
- **Hecho cuando**: el autor ve la atribución de cada abstract frente a su lectura manual (TWA 7 b / RX J0534 b, TOI-2109 b, V1298 Tau) y decide si la vía (c) se adopta en firme; con esa decisión se revisan T72–T78 y el criterio de cierre de fase 2.
- **Cierre (2026-09-29)**: humo `tests/manual/test_reader_attribution_smoke.py` con prompt experimental `reader-measures-exp1` (neutro: sin nombres ni cifras del fixture, verificado). 4 llamadas, 0 reintentos, **25.524 tokens** (tope 40.000): 5.258 / 6.247 / 6.445 / 7.574 por abstract (≈6.400 de media, frente a 3.056 del Reader de producción). Atribución: 2609.17025 sin medidas (OK); 2609.20748 masa 2,8 (+0,5/−0,5) M_J a RX J0534.0-0221 b, nada a TWA 7 b (OK); 2609.26894 R, M y P de TOI-2109 b exactos con `origin=literature`, nada a WASP-12 b (OK); 2609.30038 las 4 masas de b y las 3 de e con sus errores exactos, `this_work`, sin cruce b/e, nada para c/d ni periodos — el informe lo marca FALLO porque `planet_name` salió como "b"/"e" (el prompt pedía copiar el nombre sin completarlo): atribución correcta, nombre incompleto. σ no calculado automáticamente por ese nombre; cálculo manual con la fórmula provisional frente a la solución por defecto (Livingston et al. 2026): b 3,4–3,9σ, e 2,7–3,4σ; frente a Suárez Mascareño et al. 2022: b ≈0,5σ, e 1,3–2,3σ. **Decisión del autor (2026-09-29): se adopta la vía (c) en firme**, con el ajuste de pedir el nombre completo del planeta (anfitriona + letra). Pendiente del autor: criterio de cierre de fase 2. Siguiente: revisar T72–T78 con esta decisión.

### T71.c · Reader v3: medidas estructuradas por planeta
- **Estado**: done
- **Depende de**: T71.b
- **Toca agentes y gasto**: sí. **Dos pasadas de revisión con `budget-guard-review`.**
- **Alcance**:
  - Dominio: value object `Measurement` (dataclass congelada, sin identidad) en `domain/entities.py` con `planet_name`, `parameter` (mass/radius/period), `value`, `err_plus`, `err_minus`, `unit` (M_jup/M_earth/R_jup/R_earth/day), `limit` (none/upper/lower), `origin` (this_work/literature), `evidence`; invariantes de `MeasurementOut` (valor finito, errores ≥ 0, unidad coherente con el parámetro, nombre y evidencia no vacíos); propiedad `usable_for_tension` (`origin == this_work`, `limit == none`, ambos errores presentes). Campo nuevo `Reading.measurements: tuple[Measurement, ...] | None`: `None` = no extraído (lecturas `reader-v2` o ítems fuera del alcance del prompt con medidas), `()` = extraído sin medidas (aprobado por el autor el 2026-09-29).
  - Persistencia: columna `readings.measurements` JSONB nullable, mapper y migración Alembic con downgrade.
  - Agente: prompt de producción `reader-v3`, a partir de `reader-measures-exp1`, con `planet_name` como nombre completo (anfitriona + letra) completado con la anfitriona tal como aparece en el abstract. `MeasurementOut` pasa a `application/agents/reader_output.py`; `ReadItem._build_reading` mapea las medidas. Filtros en Python: una medida mal formada o con `evidence` que no es subcadena literal del abstract (salvo espacios) se descarta sola y se registra en el log, sin reintentar el ítem; salvaguarda del nombre completado: la anfitriona debe aparecer en el abstract o en `objects`, si no la medida se descarta. `tests/experiments/` no se toca (registro de T71.b).
  - Alcance del prompt con medidas: solo ítems de astro-ph.EP; la lista de categorías en `pipeline.toml`, sin defaults en código. Parámetros: masa, radio y periodo.
  - Configuración: `max_items_per_night` baja de 40 a 30 (adelantado desde T75: sin esto el pool de Reader+Popularizer se agota). Estimación propia del Reader v3 (orden de 13.000, ≈ 2 × media medida de 6.400; máximo observado 7.574).
  - Informe: consulta nueva en `backend/scripts/night_report.sql` con las medidas de la noche (ítem, planeta, parámetro, valor, errores, origen, evidencia).
- **Pregunta abierta en la tarea**: ¿masa mínima (`m sin i`) frente a masa verdadera? Opciones: parámetro `msini` en el esquema; excluir masas de RV; aceptar el riesgo.
- **Hecho cuando**: tests de dominio de `Measurement` y `usable_for_tension`; parseo y `build` con `FakeLLMProvider` usando como fixtures las salidas reales de T71.b; mapper con test `-m db` que distingue `None` de `()`; `upgrade`/`downgrade -1` limpios; test de que el Reader autoriza con su estimación propia; `test_llm_call_sites.py` con una sola entrada; humo `-m manual` con `reader-v3` sobre los 4 abstracts de T71.b (tope fijado en el plan, orden de 40.000) donde 2609.30038 devuelve "V1298 Tau b"/"V1298 Tau e"; **una noche real completa con `reader-v3`** y revisión del autor de todas sus medidas frente a los abstracts (primera validación con abstracts no vistos); dos pasadas de `budget-guard-review`; § "Modelo de dominio" y § "Control de gasto" de `CLAUDE.md` actualizados; ADR 0014 escrito.
- **Cierre (2026-09-30)**: implementado según ADR 0014 (commit 46e913b, PR #27). Dos pasadas de `budget-guard-review` sin bloqueantes; correcciones aplicadas (salvaguarda de anfitriona contra abstract o título con límite de palabra, `value > 0`, valor presente en la cita, comentarios de presupuesto). Suite: 1328 passed; 155 `-m db`. Humo manual con el camino de producción: 4/4 abstracts de T71.b correctos, 28.402 tokens. **Primera noche real con `reader-v3` (2026-09-30)**: `completed`, 30 leídos (11 con v3), 9 publicados, 197.570 tokens; Reader v3 media 5.575 / máx. 7.338 tokens por llamada (v2: 3.545), un reintento recuperado; las 11 lecturas v3 guardaron `()` y la revisión manual no encontró omisiones ni atribuciones falsas (7 abstracts de ciencia planetaria del sistema solar; `m sin i` sin error en HD 126053 b/HD 168009 b; 2 medidas del candidato "61 Cygni AB" descartadas por el filtro de anfitriona). Veredicto del autor: aprobado. Ninguna medida contrastable esa noche: el ritmo lo medirá T74.

### T72 · Extensión del dominio: `FindingType.CATALOG_TENSION` y datos del contraste
- **Estado**: done
- **Depende de**: T73
- **Toca agentes y gasto**: no.
- **Alcance**: `FindingType.CATALOG_TENSION` (`"catalog_tension"`) ligado a `item_id`, sin entidad nueva (ADR 0012 §1). Campo estructurado nuevo en `Finding` (ADR 0012 §13) = resultado de T73 serializado: medida del paper (valor, errores, unidad, `evidence`), planeta canónico del archivo, soluciones previas comparadas (referencia, valor, errores, si es la solución por defecto, enlace estable al registro) y σ. Invariante: el campo está informado si y solo si `type == catalog_tension`. Migración Alembic del CHECK de `finding_type` y de la columna nueva (JSONB, nullable) con downgrade; el test que congela `{"paper_explained"}` pasa a congelar los dos valores. `AgentRole` no se toca aquí (pasa a T75).
- **Preguntas abiertas en la tarea**: (a) `Item.status` de un ítem con un `paper_explained` descartado y un `catalog_tension` publicado, o al revés; (b) ¿`catalog_tension` lleva tres niveles de texto o uno? Si es uno, cambian los invariantes de `level_*` de `Finding`; se resuelve antes de la migración.
- **Hecho cuando**: `alembic upgrade head` y `downgrade -1` limpios; tests de dominio, invariantes y mappers en verde; cero imports de IO en `domain/`; § "Modelo de dominio" de `CLAUDE.md` actualizado.
- **Cierre (2026-09-30)**: implementado según ADR 0017. Decisiones del autor al aprobar el plan: `Item.status` solo sigue el camino `paper_explained`; tres niveles de texto; `archive_url` dentro de `catalog_tension`. `FindingType.CATALOG_TENSION`, value objects `CatalogTension` y `CatalogTensionComparison` (solo describen candidatos: exactamente una previa por defecto y `reference_sigma` = σ mínimo frente a ella ≥ umbral), `Finding.catalog_tension` con invariante iff, `catalog_tension_from`; `CatalogSolution` movida a `entities.py` y reexportada. Columna JSONB con `schema_version` 1, CHECK de tipo ampliado y CHECK iff; migración `7c1e4a9b2d35` probada upgrade/downgrade/upgrade en base efímera, downgrade con filas `catalog_tension` → `RuntimeError`. Revisión aprobada; correcciones aplicadas (invariante de `reference_sigma`, `==` en el tipo, `schema_version` estricto, sin `assert` en dominio). Suite: 1609 passed; 169 `-m db`; ruff limpio. Sin gasto. Pendiente para T76: adaptar `EditNight` y el prompt del Editor antes de crear filas `catalog_tension`. **Tras el merge, migrar la base real** (`alembic upgrade head`, fuera de 00:00–04:45).

### T73 · Cálculo determinista de la tensión
- **Estado**: done
- **Depende de**: T71.c
- **Toca agentes y gasto**: no. Test de import: no importa `claude_agent_sdk`.
- **Alcance**: función pura en `domain/` que recibe una medida del paper y una solución previa y devuelve la discrepancia en σ con los números usados; value object del resultado. Puerto `ExoplanetCatalog` (`Protocol`) en `domain/`: resolución de nombre a planeta canónico y soluciones publicadas por planeta y parámetro (lo implementa T74). Caso de uso en `application/`, sin LLM: toma los `Reading` de la noche con `measurements`, se queda con las `usable_for_tension`, resuelve el planeta por el puerto, **excluye de las previas la solución del propio paper** y devuelve las tensiones. Medidas `literature`, límites y medidas sin error nunca cuentan. Sin discrepancia calculada no hay candidato (ADR 0012 §6).
- **Preguntas abiertas en la tarea**: (a) fórmula: errores asimétricos; comparar con cada previa, con la solución por defecto, con la media ponderada o con el rango (V1298 Tau: 3,4–3,9σ frente a Livingston 2026, ≈0,5σ frente a Suárez Mascareño 2022); (b) umbral de σ; (c) varias soluciones del mismo paper para el mismo planeta y parámetro: cada una por separado, la de menor error, o exigir que todas estén en tensión; (d) ¿todos los `Reading` con medidas o solo `interest_score >= 4`?
- **Hecho cuando**: tests con casos calculados a mano (sin tensión, tensión clara, errores asimétricos, catálogo sin previas → nunca publicable, objeto no emparejado, medida no utilizable descartada, solución propia excluida); caso de uso probado con catálogo falso; test de import sin `claude_agent_sdk`.
- **Cierre (2026-09-30)**: implementado según ADR 0015 (decisiones del autor del mismo día: error del lado que mira al otro valor; referencia = solución por defecto con σ frente a todas las previas guardados; umbral 3σ aplicado fuera; todas las soluciones del paper en tensión; se cruzan todos los `Reading` con medidas utilizables; sin default no hay candidato). `domain/catalog.py`, `domain/tension.py`, `application/use_cases/compute_tensions.py`; `UNITS_BY_PARAMETER` pública. Tests con valores calculados a mano (V1298 Tau b frente a Livingston 2026: 3,40σ y 3,91σ; frente a Suárez Mascareño 2022: 0,53σ; e mínimo 2,69σ → no candidato a 3σ; TOI-2109 b σ = 0), catálogo falso y test de import en subproceso. Cuatro mutantes manuales muertos (exclusión de la propia, lado del error, filtro de medidas no utilizables, mínimo/máximo). Revisión aprobada con correcciones, aplicadas: `is_candidate` identifica la previa por defecto por igualdad de valor (no `id()`), contrato de `arxiv_id` documentado en el puerto, test de la propia como default. Suite: 1407 passed; 155 `-m db`. Sin gasto: no toca agentes ni `budget.py`.

### T74 · Adaptador del NASA Exoplanet Archive
- **Estado**: done
- **Depende de**: T73
- **Toca agentes y gasto**: no. Cero tokens; sin servidor MCP (ADR 0012 §14).
- **Alcance**: `infrastructure/exoplanet_archive/`: cliente TAP sobre `httpx` con timeout total por petición, espaciado de cortesía y techo de peticiones por noche. Implementa `ExoplanetCatalog`: resolución de nombres (`normalize_name` y alias portados del script de T71 a `infrastructure/`; el nombre completo del Reader se normaliza aquí), soluciones por planeta, identificación de la solución del propio paper por `pl_refname`, enlace estable al registro. Sección `[sources.exoplanet_archive]` en `pipeline.toml` sin defaults en código; el peor caso de tiempo entra en el validador que contrasta la ingesta con `limits.run_timeout_s`. `--dry-run` calcula y muestra las tensiones de los `Reading` con medidas ya guardados, sin llamar a ningún agente.
- **Decisiones del autor al aprobar el plan (2026-09-30)**: cortesía 2,0 s entre peticiones, techo 40 peticiones por proceso, timeout total 30 s por petición; User-Agent neutro `nocturna/0.1.0`; masa solo con `pl_bmassprov == "Mass"`; `threshold_sigma = 3.0` a `pipeline.toml`; opción (b) del informe: T74 se cierra con informe real + caso de control V1298 Tau; T75 con dependencia de 7 noches reader-v3 + nuevo `--dry-run` (cero tokens) para fijar reserva; techo de 40 revisado en T76; sin regla de dígitos para alias (post-revisión 2026-09-30).
- **Hecho cuando**: tests con respuestas TAP grabadas, cero red y cero esperas reales; validadores de configuración con test; **`--dry-run` sobre las noches reales acumuladas con `reader-v3`** con informe de medidas utilizables, emparejadas y tensiones por tramo de σ, revisado por el autor; ese informe fija el valor inicial de la reserva de T75.
- **Cierre (2026-09-30)**: implementado según ADR 0016. Suite: 1548 passed, 6 deselected; `-m db` 160 passed; ruff limpios. Caso de control: V1298 Tau b σ mínimo 3,37 (corrige 3,40 en ADR 0015: se calculó con 0,041 M_J y dos medidas; aquí cuatro medidas y archive real); e no candidato 2,69. `--dry-run` real 2026-09-30 10:03 sobre base con `reader-v3`: exit 0, fetched=118 new=66, 11 lecturas v3, 0 con medidas utilizables, 0 candidatos, 0 peticiones al archivo (sin tensiones). Decisión del autor post-revisión (2026-09-30): se quita la regla que solo consultaba alias para nombres con dígitos; cualquier nombre fuera del índice pasa por alias una vez (con caché negativo). Motivo: nombres como "Proxima Centauri b" o "Beta Pictoris b" no coinciden con el índice.

### T75 · Reserva del redactor en `budget.py`
- **Estado**: pending
- **Depende de**: T71.c (coste real del Reader v3), T74 (tensiones por noche medidas)
- **Dependencias adicionales**: espera **7 noches completas con `reader-v3` activo** (criterio del autor del 2026-10-05: cuentan solo las noches con al menos una llamada real a `reader-v3`; 4 a 2026-10-05: 30/09, 01/10, 02/10 y 03/10) y **nuevo `--dry-run` (cero tokens) para fijar la reserva** (opción (b) del plan aprobada por el autor). Hasta entonces, T75 queda bloqueada.
- **Toca agentes y gasto**: sí. **Dos pasadas de revisión con `budget-guard-review`.**
- **Alcance**: valor nuevo en `AgentRole` para el redactor (y migración del CHECK de `agent_calls`); el nombre del rol se decide aquí. Reserva fija nueva, anidada: Reader y Popularizer ven `nightly_tokens − editor_reserve − reserva_redactor`; el redactor ve `nightly_tokens − editor_reserve`; el Editor ve `nightly_tokens`. Tope de llamadas del redactor por noche (reintento incluido) y estimación por llamada en `pipeline.toml`. Validadores al cargar que fallan cerrado: `tope × estimación ≤ reserva_redactor`; `editor_reserve + reserva_redactor < nightly_tokens`; la reserva del Editor cuenta los candidatos `catalog_tension` (`editor_base + (max_items_per_night + tope_tensiones) × per_candidate ≤ editor_reserve`). `nightly_tokens` se queda en 300.000. `--dry-run` muestra las tres porciones.
- **Preguntas abiertas en la tarea**: nombre del rol; valores de reserva, tope y estimación a partir del informe de T74 (el "20–40k" de ADR 0012 era provisional); ¿cabe el pool de Reader+Popularizer con Reader v3?
- **Hecho cuando**: tests que demuestran que la reserva del Editor sigue intacta aunque el redactor agote la suya, que la del redactor sigue intacta aunque Reader y Popularizer agoten su pool, que el redactor se deniega al alcanzar su tope y que se rechaza la configuración incoherente; verificación por mutantes; dos pasadas de `budget-guard-review`; § Control de gasto de `ARCHITECTURE.md` y `CLAUDE.md` actualizados.

### T76 · Rol redactor de tensiones e integración en `run-night`
- **Estado**: pending
- **Depende de**: T72, T74, T75
- **Toca agentes y gasto**: sí. **Dos pasadas de revisión con `budget-guard-review`.**
- **Alcance**: rol nuevo con prompt versionado en `prompts/`, esquema Pydantic y caso de uso que llama a través de `AgentRunner`, el único punto de gasto. Una conversación nueva por tensión; `tools=[]`, `setting_sources=[]`, `skills=[]` (ADR 0006). Entrada: solo lo ya calculado (objeto, parámetro, medida del paper con su `evidence`, soluciones previas, σ, enlaces, título del ítem). Salida inválida → un reintento → `failed`. Los `catalog_tension` pasan por el Editor (ADR 0012 §12) en su única llamada por noche junto con los demás tipos; `EditNight` ya decide por `candidate_id` y admite varios tipos desde T89 (`editor-v2`); aquí se añade la línea `data` de `catalog_tension` y un prompt `editor-v3`. Orden de la noche: Reader → cruce determinista (sin tokens) → Popularizer → redactor (solo si hay tensión) → Editor.
- **Preguntas abiertas en la tarea**: (a) modelo por defecto del rol; (b) cómo presenta el Editor los dos tipos de candidato en su única llamada.
- **Hecho cuando**: tests con `FakeLLMProvider` (sin tensión, cero `AgentCall` del rol; con tensión, una; JSON inválido → reintento → `failed`; presupuesto del rol agotado → ninguna llamada y el resto de la noche sigue; el Editor recibe los dos tipos en una llamada); `test_llm_call_sites.py` con una sola entrada; humo `-m manual` con una tensión real del informe de T74; dos pasadas de `budget-guard-review`; tabla "Agentes" de `CLAUDE.md` actualizada.

### T77 · API y web: tipo visible, filtro por tipo y etiqueta "candidato"
- **Estado**: done
- **Depende de**: T72, T70
- **Toca agentes y gasto**: no.
- **Alcance**: API: `type` en el resumen del listado; `GET /findings?type=` validado contra el enum; el campo estructurado de T72 en el detalle. Web: mismo feed, etiqueta visible del tipo en feed y detalle, filtro por tipo en la URL sin estado de cliente, etiqueta "candidato" en `catalog_tension`, valor del paper con su cita literal, soluciones previas con enlace al archivo y σ junto a cada afirmación, en castellano. Banner intacto; cero llamadas a Claude.
- **Pregunta abierta en la tarea**: textos exactos de las etiquetas.
- **Hecho cuando**: tests de API `-m db` (filtro, tipo inválido, `404` que sigue siendo indistinguible); tests, lint y build de la web en verde con la API parada; guarda `no-claude-in-web` en verde; Lighthouse de accesibilidad ≥ 90.
- **Cierre (2026-10-06, pendiente de merge; sin migración)**: plan aprobado por el autor con D1–D9: los cuatro tipos; etiquetas "Artículo explicado", "Tensión con el catálogo", "Primera medida", "Confirmación independiente"; "Candidato" solo en `catalog_tension`; slugs `?tipo=`; `evidence` como cita en inglés en texto plano; ADR 0024. API: filtro `?type=`, `type` en el listado, tres datos estructurados en el detalle por lista blanca, `404` intacto. Web: etiquetas, filtro, bloque "Datos del contraste", enlaces externos validados, sin `dangerouslySetInnerHTML`, test de contrato de claves con la API. La revisión detectó que la web esperaba un `planet_name` por medida que la API no envía (columna "Planeta" vacía con datos reales); corregido. Lighthouse de accesibilidad 100 en `/`, `/?tipo=primera-medida` y dos detalles, con la API local en solo lectura sobre la base real. Suites: backend 2612 passed (369 de `-m db`); web 107 tests.

### T78 · Cierre de fase 2
- **Estado**: pending
- **Depende de**: T76, T77
- **Toca agentes y gasto**: no hay código nuevo; es operación.
- **Alcance**: noches automáticas con el cruce activo. Registro por noche: ítems EP leídos, medidas extraídas, utilizables y emparejadas, tensiones calculadas, tokens de más del Reader v3, llamadas y tokens del redactor, candidatos y publicados. Revisión de la atribución por muestreo (validación continua con abstracts no vistos). Al final, el autor valora cuántos candidatos son genuinamente interesantes y decide si sigue esta vía o cambia de enfoque.
- **Pregunta abierta en la tarea (bloquea el arranque de T78, no las anteriores)**: criterio de cierre. ADR 0012 §9 pedía al menos 10 candidatos en dos semanas; ADR 0013 §3 lo dejó en revisión (una tensión real en cinco noches en T71). Propuesta hecha en conversación, **no aprobada**: ≥ 3 candidatos en 4 semanas. El informe de T74 dará el ritmo real. Decide el autor.
- **Hecho cuando**: el autor ha fijado el criterio, este se cumple, y la decisión final queda en un ADR.

### T79 · Filtro de exoplanetas en la ingesta y prioridad en la cola de lectura
- **Estado**: done
- **Depende de**: T75 en el enunciado del autor; se adelanta a T75 (decisión del autor al aprobar el plan, 2026-10-01): T75 calibra el coste de v3 y debe hacerlo con la población que v3 va a leer. Las noches con v3 anteriores a T79 cuentan para las 7 de T75.
- **Toca agentes y gasto**: sí, indirectamente: decide qué ítems pasan por `reader-v3` (más caro) y el orden de la cola de lectura. No toca `budget.py`. Dos pasadas de revisión con `budget-guard-review`.
- **Origen**: informe sobre `reader-v3` del 2026-10-01 (dos noches, 25 ítems astro-ph.EP, 0 medidas; 14 de 25 de sistema solar o física espacial); opción 1 aceptada por el autor.
- **Alcance**: marca `Item.exoplanet_match` calculada en la **ingesta** con palabras clave y patrones de designación en `[exoplanet_filter]` de `pipeline.toml` (lista B del autor con correcciones, ADR 0018); `reader-v3` solo si casa y la categoría está en `measurement_categories`; los EP que no casan van a `reader-v2`; `next_unread` prioriza los marcados y después `fetched_at`; columna y migración; script de relleno; marca `[exo]` y reparto v3/v2 en `--dry-run`.
- **Hecho cuando**: fixture real con cero falsos negativos en los planetas concretos (V1298 Tau b, TOI-6981 b, los de T71.b) y cero falsos positivos en sistema solar y negativos sintéticos; tests de dominio, configuración, ingesta, `ReadItem` (variante y estimación), orden de la cola y migración en verde; dos pasadas de revisión; tras el merge, base real migrada y rellenada con TOI-6981 b en cabeza de la cola, según la secuencia de `docs/DEVELOPMENT_WORKFLOW.md`.
- **Cierre (2026-10-01)**: implementada según ADR 0018 (commit fc38462, PR #32). Dos pasadas de revisión con `budget-guard-review`, la segunda aprobada. Arreglo posterior del mismo día (commit 0b67bf6, PR #33): letras de planeta múltiples y ascendentes ("HIP 67522 bc"), por un falso negativo real (2609.35979) detectado en el relleno. Secuencia de merge de `DEVELOPMENT_WORKFLOW.md` ejecutada el 2026-10-01 por la mañana: base real en `a79e3c5d8f12`; relleno de 385 ítems, 45 marcados; `run-night --dry-run` con 17 de los 30 a leer con `reader-v3`; 2609.35979 en el puesto 2 y TOI-6981 b (2609.37597) en el puesto 9, ambos con v3. Suite: 1791 passed.

### T80 · Guard de ADR: bloquear solo ficheros ya en git
- **Estado**: done
- **Depende de**: —
- **Toca agentes y gasto**: no.
- **Origen**: tres veces (ADR 0011, 0012, 0018) el hook bloqueó editar un borrador de ADR sin commit y hubo que borrarlo y recrearlo. La inmutabilidad protege lo publicado, no el borrador (Decisiones del autor del 2026-10-01).
- **Alcance**: `.claude/hooks/guard-write.sh` bloquea la edición de un ADR solo si el fichero ya está en git (`git ls-files --error-unmatch`); los ficheros nuevos sin commit se pueden editar. Commit propio `chore(hooks): ...`, sin mezclar con otra tarea.
- **Hecho cuando**: `backend/tests/hooks/test_guard_write.py` en verde con los casos del plan (borrador sin rastrear permitido; ADR commiteado, modificado, borrado del disco, en el índice o renombrado bloqueado; fuera de repo, sin git o con directorio inexistente bloqueado; consulta contra el repo o worktree del propio fichero, inmune a `GIT_DIR` y a nombres con glob; resto de reglas del hook sin cambios).
- **Cierre (2026-10-01)**: la rama `*/docs/adr/*` de `guard-write.sh` consulta `git ls-files --error-unmatch` en el repo del fichero: pathspec `:(literal,icase)` (sin globs e insensible a mayúsculas, por APFS); un symlink en `docs/adr/` se bloquea; rc 0 (índice o commit) bloquea, rc 1 (sin rastrear) permite, cualquier otro rc bloquea (falla cerrado). "Publicado" = en el índice de git. 30 tests nuevos (suite: 1821 passed); la revisión encontró una regresión frente a `main` (mayúsculas en el nombre y symlinks), corregida con tres tests más; mutaciones comprobadas (volver a `[ -f "$path" ]`, quitar `-C`, quitar `icase`, quitar el bloqueo de symlinks o el `-u GIT_INDEX_FILE` ponen tests en rojo). Surte efecto en la sesión tras el merge y `git pull` del árbol principal (el hook se carga desde `$CLAUDE_PROJECT_DIR`).

### T81 · Snapshot semanal del NASA Exoplanet Archive
- **Estado**: done
- **Depende de**: T74
- **Toca agentes y gasto**: no. Cero tokens.
- **Origen**: Decisiones del autor del 2026-10-01 tras el estudio de viabilidad de la opción 3(b): la opción 3(b) entra como segunda vía de la fase 2, unida a la de arXiv por `arxiv_id`.
- **Alcance**: opción B incremental del estudio de viabilidad, los viernes (filas nuevas por `releasedate`, soluciones por defecto y filas de los planetas afectados), más volcado completo mensual. Tablas `archive_snapshot`, `archive_solution` (clave natural `solution_key`) y `archive_default_change`, como propone el estudio. Reutiliza el cliente y los mapeos de `infrastructure/exoplanet_archive/`.
- **Hecho cuando**: tests sin red en verde; dos pasadas de cambios tras revisión; carga real completa y una segunda ejecución incremental sin cambios; agente de launchd instalado.
- **Cierre (2026-10-02)**: implementado según ADR 0019 (commit c2b8b42, PR #36). La revisión rechazó por el PATH del envoltorio (sin `/opt/homebrew/bin`, el job nunca habría encontrado `docker`) y porque la guarda no contaba las soluciones por defecto perdidas; ambos corregidos con tests. Suite: 2069 passed. El 2026-10-01 a las 13:27: base real migrada a `b4d7f1a26c93`; `--dry-run` y carga completa inicial (1 petición, 40.031 soluciones tras fusionar 157 duplicados exactos, 6.372 soluciones por defecto, 6,6 s); segunda ejecución incremental (3 peticiones, 0 altas, 0 bajas, 0 cambios). Agente `com.nocturna.archive-snapshot` instalado y cargado (viernes 10:00). Dato para T83: solo 100 de las 40.031 soluciones traen `arxiv_id` (el resto cita el bibcode de la revista); en las 8 semanas del estudio, 23 de 124 filas nuevas.

### T82 · `run-item --reader v3 --force`: releer un ítem ya leído
- **Estado**: done
- **Depende de**: T79
- **Toca agentes y gasto**: sí. Dos pasadas de revisión con `budget-guard-review`.
- **Origen**: autor, 2026-10-01 (caso de 2609.35979, HIP 67522 b y c, que estuvo a punto de leerse con v2). Prioridad alta tras T81.
- **Alcance**: releer con `reader-v3` un ítem en estado `read`; pasa por `BudgetGuard` como cualquier llamada; conserva la `Reading` anterior con su `prompt_version`.
- **Hecho cuando**: lo fija el plan.
- **Cierre (2026-10-05; mergeada y base migrada a `f7c2d8e4a951` el mismo día, con `prompt_version` relleno en las 392 lecturas; primeras 5 relecturas el 2026-10-06 con un agente launchd puntual: 33.551 tokens, 21 medidas, 13 evaluaciones nuevas)**: plan aprobado por el autor con las recomendaciones del architect: se releen ítems `read`, `discarded` y `published` (D1); `Item.status` y `Finding` intactos (D2); se rechaza si la lectura vigente ya tiene medidas (D3); se exigen los criterios de ADR 0018 (D4); solo Reader (D5); con un `Run` en curso sale con 9 (D6); a mano dentro de la ventana (D7), ≤ 5 por noche (D8); los `Run` de relectura (`notes = "reread"`) no cuentan como noches para T75 ni para el informe de la mañana (D9); relleno de `prompt_version` solo sin ambigüedad (D10); ADR 0022 (D11). Migración `f7c2d8e4a951` (`readings.prompt_version`, `superseded_at`, índice único parcial); `ReadItem.reread_with_measurements`; `run-item --reader v3 --force` con códigos 0/1/2/3/4/9. La revisión pidió además excluir las lecturas sustituidas y los `Run` de relectura en `night_report.sql` y `exoplanet_viability.py`, tests de cierre del `Run` y cierre también ante interrupciones. Dos pasadas de `budget-guard-review`. Suite: 2444 passed (331 de `-m db`). El caso origen, 2609.35979, ya se leyó con v3 el 2026-10-02 y no es candidato.

### T83 · Enlace entre vías: solución propia por `arxiv_id` y por valores
- **Estado**: done
- **Depende de**: T81
- **Toca agentes y gasto**: no.
- **Origen**: Decisiones del autor del 2026-10-01 tras el estudio de viabilidad de la opción 3(b): arXiv detecta antes, el archivo confirma o resuelve después.
- **Alcance**: unir `arxiv_id_from_refname` de las soluciones del archivo con el `Item` de arXiv correspondiente. Incluye la medición pendiente, sin tokens: de las 130 filas nuevas de las 8 semanas del estudio, cuántos `pl_refname` con `arxiv_id` corresponden a ítems ya presentes en la base.
- **Hecho cuando**: lo fija el plan.
- **Nota (2026-10-02)**: con la carga real de T81 solo el 0,25 % de las soluciones del archivo traen `arxiv_id`; unir las vías solo por `arxiv_id` dejaría fuera la mayoría de los casos. El plan de T83 debe plantear el enlace también por bibcode u otra clave. Primer caso real para fixture: Chakraborty et al. 2026 (HIP 67522 b y c, bibcode `2026arXiv260618045C`), alta del archivo del 2026-10-01.
- **Cierre (2026-10-06; mergeada el mismo día, sin migración; informe en la base real: (a) 130 altas desde 2026-08-01, 27 con `arxiv_id`, 0 cruces con ítems; (b) retraso preprint → archivo de 0 a 71 meses, mediana 2,5; (c) 103 filas de revista, 0 propias por valor, 0 ambiguas; (d) 21 evaluaciones, 0 cambios; 2 confirmaciones bloqueadas, HIP 67522 b y TOI-2158 b, ambas con referencia independiente)**: plan aprobado por el autor (D1–D8 con las recomendaciones; título nuevo). Al ver los tests, el autor decidió además que la coincidencia por valor solo vale para masa y radio y que la tolerancia se mide sobre el valor del archivo. `domain/own_solution.py` (`classify_solution`), exclusión en `ComputeTensions`, confirmación solo con referencia independiente, `[tension.own_solution]` (0,01 y 6 meses), `scripts/t83_link_report.py` de solo lectura; ADR 0023. `confirmation_enabled` sigue en `false`. Suite: 2573 passed (349 de `-m db`).

### T84 · Resumen semanal de cambios de solución por defecto
- **Estado**: pending
- **Depende de**: T81; el texto del redactor, de T75/T76
- **Toca agentes y gasto**: solo a través del redactor.
- **Origen**: Decisiones del autor del 2026-10-01 tras el estudio de viabilidad de la opción 3(b): producto semanal nuevo; es la sección fija del producto y las tensiones son la sección rara.
- **Alcance**: resumen determinista de los cambios de solución por defecto del archivo (planeta, paper, parámetros que cambian), sin LLM salvo el redactor.
- **Hecho cuando**: lo fija el plan.

### T85 · Tensiones solución contra solución
- **Estado**: pending
- **Depende de**: T81, T73, T72
- **Toca agentes y gasto**: solo a través del redactor y el Editor.
- **Origen**: Decisiones del autor del 2026-10-01 tras el estudio de viabilidad de la opción 3(b).
- **Alcance**: tensiones entre soluciones del archivo con las reglas deterministas 1–3 y 5 del estudio como filtro (solo `Published Confirmed`, distinta referencia, sin papers antiguos incorporados ahora, reglas de periodo); la 6 ("depende del modelo") y la 7 (`pl_controv_flag`) como etiquetas visibles para el Editor, no como descarte. Un `Finding` de esta vía cuelga de un `Item` con `source = 'exoplanet_archive'` y `external_id = solution_key`.
- **Hecho cuando**: lo fija el plan.

### T86 · ADR 0021: criterio de cierre de fase 2 (supersede a ADR 0012)
- **Estado**: pending
- **Depende de**: T83, T84, T85 y datos de las dos vías
- **Toca agentes y gasto**: no.
- **Origen**: Decisiones del autor del 2026-10-01 tras el estudio de viabilidad de la opción 3(b); ADR 0012 queda obsoleto en su criterio de cierre (no se edita).
- **Alcance**: ADR 0021 (renumerado desde 0019 al aprobar T81 y desde 0020 el 2026-10-02, que lo toman T81 y la regla de referencia de T88/T89) con tres criterios: (a) la cadena funciona de extremo a extremo con un caso real por vía (V1298 Tau b por arXiv, HD 202206 c por el archivo); (b) N = 2 candidatos publicables según el Editor en 4 semanas, sumando las dos vías; (c) snapshot semanal con al menos 4 semanas de histórico y filas en `archive_default_change`. Si (b) falla con (a) y (c) cumplidos, la fase cierra igualmente con el resumen semanal como producto principal. Se redacta al final, con datos de las dos vías.
- **Hecho cuando**: ADR 0021 escrito con los datos medidos.

### T87 · `--dry-run` no escribe en la base
- **Estado**: done
- **Depende de**: —
- **Prioridad**: inmediata, antes que cualquier otra tarea pendiente (decisión del autor, 2026-10-02).
- **Toca agentes y gasto**: no.
- **Origen**: cuarto informe seguido con ingesta persistida por `run-night --dry-run` (66 ítems el 2026-09-30, 39 el 2026-10-01, 26 el 2026-10-02). Deuda registrada en `TECHNICAL_DEBT.md`.
- **Alcance**: `run-night --dry-run` no escribe nada en la base: ingesta en memoria o en una transacción con rollback; el dry-run sigue mostrando qué habría ingerido, el plan de gasto, el reparto v3/v2 y el cruce de tensiones.
- **Hecho cuando**: `unit_of_work(commit=False)` con 4 tests (rollback, `commit()` prohibido, excepción propagada, `commit=True` intacto) y 7 tests del CLI contra la base: recuento de todas las tablas del esquema `public` (incluida `alembic_version`) idéntico antes y después, dos dry-run seguidos con el mismo `new=`, ítems nunca guardados que cuentan en el plan con su prioridad, arXiv o archivo caídos sin escrituras, sin conexiones `idle in transaction`, y la ingesta de la noche real que sigue persistiendo.
- **Cierre (2026-10-02)**: opción (a) del plan: una sola sesión con `unit_of_work(commit=False)` para la ingesta, el plan de gasto y las lecturas de tensiones; el archivo se consulta con la sesión ya cerrada (desde T88 las previas salen de la base dentro de la misma sesión y solo el alias puede ir a la red); marcador `[dry-run: no se escribió nada en la base]`. La noche real, `run-item` y `archive-snapshot` no cambian. Mutación comprobada (`commit=False` → `commit=True` pone en rojo 4 de los 7 tests del CLI). Revisión aprobada. Suite: 2080 passed (223 de `-m db`). Sin migración.

### T88 · Regla de referencia, límites superiores y medidas en espera
- **Estado**: done
- **Depende de**: T81, T87
- **Toca agentes y gasto**: no.
- **Origen**: Decisiones del autor del 2026-10-02 tras la revisión de la noche (caso real de HIP 67522 b y c, 2609.35979).
- **Alcance**: (1) **regla de referencia** por parámetro, que supersede ADR 0015 §6: la solución por defecto si tiene ese parámetro con error bilateral; si no, la solución `Published Confirmed` más reciente que lo tenga con error bilateral; excluyendo siempre la del propio paper (por `arxiv_id`) y aplicando las reglas deterministas 1–3 y 5 del estudio de viabilidad. (2) **Límites superiores**: no son referencia; una medida por debajo del límite se registra como "consistente con límite" y no se publica; una por encima es una tensión de tipo "incompatible con límite previo" y puede ser candidato. (3) **Medidas sin previa**: estado nuevo `awaiting_reference`, no se descartan; cada snapshot del archivo (T81) reevalúa las medidas en espera cuando entra una solución para ese planeta; si la solución que entra es la del mismo paper (mismo `arxiv_id`), no es tensión: es cierre del bucle y se registra como tal. Caso de control: HIP 67522 b, referencia Chakraborty et al. 2026, compatible a 1,4σ; se guarda.
- **Hecho cuando**: tests sin red con los casos de control (HIP 67522 b `evaluated` frente a Chakraborty 2026 con 1,42 y 0,75σ desde filas del archivo; c `consistent_with_limit` con la cota de 22 M⊕; V1298 Tau b candidato a 3,37σ); evaluaciones idempotentes tras pasar por la base; paso de `awaiting_reference` a `evaluated` entre dos snapshots conservando el `id`; dry-run sin escrituras; tras el merge, base migrada y `evaluate-tensions` sobre la base real.
- **Cierre (2026-10-02; verificado en la base real tras el merge el mismo día: migración a `d2a8c5e7f104`, `evaluate-tensions` creó 5 evaluaciones —HIP 67522 b `evaluated` frente a Chakraborty et al. 2026 con 1,42 y 0,75σ, c `consistent_with_limit`, TOI-210 b masa y radio y TOI-6981 b radio `awaiting_reference`— y una segunda pasada no cambió nada)**: decisiones del autor al aprobar el plan: criterio de límite (x − L)/e_minus ≥ 3 en todas las medidas; la regla 3 no se aplica; periodo ΔP/P ≥ 1e-4 o ΔP ≥ 1 h y alias n ≤ 5; recencia por `pl_pubdate` y `releasedate`; se borra la consulta en vivo de T74. Previas desde `archive_solution`; tabla `tension_evaluation` (migración `d2a8c5e7f104`); `RecordTensionEvaluations`; subcomando `evaluate-tensions`; `archive-snapshot` evalúa tras guardar (código 3 ante cualquier fallo posterior al snapshot). La revisión pidió además no recalcular lecturas con evaluaciones todas terminales (limita las consultas al alias) y que el dry-run muestre estados conciliados; ambos hechos, con tests añadidos. Suite: 2230 passed (269 de `-m db`). No crea `Finding` ni toca el Editor ni la noche real.

### T89 · Findings `primera_medida` y `confirmacion_independiente`
- **Estado**: done
- **Depende de**: T88
- **Toca agentes y gasto**: sí (prompt del Editor). Dos pasadas de revisión con `budget-guard-review`.
- **Origen**: Decisiones del autor del 2026-10-02 tras la revisión de la noche (caso real de HIP 67522 b y c, 2609.35979).
- **Alcance**: dos tipos nuevos de `Finding`: `primera_medida` (primera masa o radio con error bilateral de un planeta sin solución comparable en el archivo; caso de hoy: TOI-6981 b) y `confirmacion_independiente` (dos soluciones independientes —papers, métodos o equipos distintos— compatibles dentro de 2σ para el mismo parámetro, con la más reciente entrada en los últimos 30 días; caso de hoy: HIP 67522 b). El prompt del Editor se actualiza para tratar "compatible" como información, no como ausencia de resultado.
- **Hecho cuando**: lo fija el plan. Al cerrar T88 y T89, ADR 0020 con los puntos 1–4 de las decisiones del autor, que supersede ADR 0015 §6 (los ADR 0015 y 0019 no se editan).
- **Cierre (2026-10-05; mergeada y base migrada a `e5b3a9d1c746` el mismo día: `evaluate-tensions --dry-run` sin fallos de resolución y `run-night --dry-run` con TOI-210 b masa y radio y TOI-6981 b radio como `primera_medida` y HIP 67522 b bloqueada)**: plan aprobado por el autor con las recomendaciones del architect: texto por plantillas deterministas, sin tokens (D1); `primera_medida` para planeta ausente o presente sin solución comparable, subtipo en el dato (D2); solo masa y radio (D3); compatibilidad con todas las medidas a σ ≤ 2 frente a la referencia de T88 (D4); ventana de 30 días con `Item.published_at` y `releasedate` desde la fecha local de la noche (D5); `confirmation_enabled = false` hasta T83 (D6); independencia solo como artículos distintos (D7); un `Finding` por evaluación (D8); K = 5 por `Run` (D9); `evaluate-tensions` tras cada noche desde el envoltorio, no en `--dry-run` (D10); el Editor decide por `candidate_id` (D11); valores literales del enum (D12); se publica antes de T77 (D13); `planet_overview_url` inyectada desde `cli.py` (D14); D16, cambio sobre T88 aprobado: solo `System Not Found` es planeta ausente, cualquier otra respuesta del alias es fallo y se reintenta. Migración `e5b3a9d1c746`; `GenerateMeasurementFindings`; prompt `editor-v2`; fase nueva en `RunNight`; `[measurement_findings]` en `pipeline.toml`; ADR 0020. Dos pasadas de `budget-guard-review`. La revisión pidió además no lanzar `evaluate-tensions` con `--dry-run`, tope K por `Run`, no recalcular lecturas por sus grupos de periodo y comprobar en el merge que no quedan filas `awaiting_reference` anteriores a D16 con fallo de resolución (paso añadido a `DEVELOPMENT_WORKFLOW.md`). Suite: 2389 passed (298 de `-m db`). Absorbe de T76 la adaptación de `EditNight` a varios tipos.


### T90 · Clave reservada en el log de `GenerateMeasurementFindings`
- **Estado**: done
- **Depende de**: T89
- **Toca agentes y gasto**: no (fase de la noche sin LLM). Revisión normal.
- **Origen**: noche del 2026-10-06 (Run `e1cab929`): la fase `measurement_findings` lanzó `KeyError` al registrar el evento `night.measurement_findings` con `extra={"created": …}` (`created` es un atributo reservado de `logging.LogRecord`); los `Finding` ya estaban guardados y el Editor los publicó, pero la noche cerró `partial` (código 7, `end=measurement_findings_error`). Se repite cada noche hasta corregirlo.
- **Alcance**: renombrar la clave; test que ejecute el generador con el logging JSON real a nivel INFO; guarda que impida claves reservadas de `LogRecord` en cualquier `extra=` de `src/`.
- **Hecho cuando**: lo fija el plan; merge antes de las 00:00 del 2026-10-07.
- **Cierre (2026-10-06; mergeada el mismo día, sin migración)**: plan aprobado por el autor. La clave pasa a `findings_created` (`MeasurementFindingsReport.created` no cambia). Tests nuevos que fallaban antes del arreglo: el generador con el logging JSON real a INFO y la noche con el log a INFO sin `measurement_findings_error`. Guarda AST `tests/test_log_extra_reserved_keys.py` sobre todos los `extra=` de `src/` frente a `_RESERVED_LOG_RECORD_ATTRS`; lo no resoluble falla, con una sola excepción explícita (`context_key` de `infrastructure/arxiv/transport.py`, valores `start`, `token` y `set`, fijados por otro test). La noche del 2026-10-06 no necesita reparar datos: los tres `primera_medida` se guardaron y se publicaron. Comprobación pendiente: la noche del 2026-10-07 debe cerrar sin `measurement_findings_error`. Suite: 2462 passed (331 de `-m db`).

### T91 · Unidades de Júpiter en los textos de los hallazgos de medida
- **Estado**: done
- **Depende de**: T89
- **Toca agentes y gasto**: no (plantillas deterministas, sin LLM). Revisión normal.
- **Origen**: `run-night --dry-run` del 2026-10-06 tras el merge de T83: las plantillas de T89 solo traducen `M_earth` y `R_earth` (M⊕, R⊕); las medidas en unidades de Júpiter salen con una etiqueta provisional parecida al código ("2,8 ± 0,5 M_Jup", "0,969 ± 0,017 R_Jup"). Esa noche se publicarían así cinco `primera_medida`: la masa de RX J0534.0-0221 b y la masa y el radio de TOI-5120 b y de TOI-5699 b.
- **Alcance**: propuesta del orquestador aceptada por el autor: `M_Jup` → "M♃" y `R_Jup` → "R♃" en título y niveles; test que recorra todas las unidades que admite `Measurement` y falle si alguna llega al texto sin traducir.
- **Hecho cuando**: lo fija el plan; merge antes de las 00:00 del 2026-10-07.
- **Cierre (2026-10-06; mergeada el mismo día, sin migración; `run-night --dry-run` muestra M♃/R♃ en las cinco `primera_medida`)**: plan aprobado por el autor. En `_UNIT_LABEL` de `measurement_finding_texts.py`, `M_jup` → "M♃" y `R_jup` → "R♃" (las etiquetas provisionales "M_Jup"/"R_Jup" eran escritas a mano, no el código del enum). La línea `data` del Editor sigue con el código del enum (es entrada para el modelo, no texto publicado). Test de cobertura sobre todas las unidades. Consulta de solo lectura en la base real: 0 hallazgos guardados con la etiqueta antigua, nada que reparar. Suite: 2580 passed (349 de `-m db`).

---

## Decisiones abiertas al arrancar

Van también a `docs/OPEN_DECISIONS.md`; se listan aquí para que el orquestador las tenga presentes desde T00.

- ~~Nombre del proyecto y del paquete Python~~ · resuelto: Nocturna, paquete `nocturna`.
- Categorías arXiv iniciales (propuesta: `astro-ph.EP`, `astro-ph.GA`; confirmar).
- Idioma de los hallazgos publicados: solo español, o español + inglés desde el principio.
- Si los prompts de agentes se versionan con un campo `prompt_version` en `AgentCall` (recomendado, coste bajo).
- Cómo autenticar el CLI de Claude Code en el VPS cuando llegue el despliegue. **No se resuelve en fase 1.**
