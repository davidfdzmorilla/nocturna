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
- **Nota importante**: **No incluye ejecución real de noche completa contra la suscripción**. El autor ejecuta `nocturna run-night` una o más veces en local y registra métricas en `docs/CALIBRACION.md` (T60). Después de la primera noche ejecutada y anotada, la tarea está completa.
- **Cerrada: 2026-09-17** · Suite íntegra: 773 passed. Implementación y revisiones completadas; calibración del autor en T60.

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
- **Estado**: pending
- **Depende de**: T50
- **Alcance**: proyecto con App Router, TypeScript, Tailwind. `/` feed paginado, `/hallazgo/[id]` con selector de nivel (curioso / aficionado / técnico) y enlace a arXiv. Banner permanente de análisis automatizado. Fetch server-side a la API de lectura. Sin llamadas a Claude, sin auth, sin admin.
- **Hecho cuando**: `pnpm dev` muestra los hallazgos publicados por T44. Lighthouse accesibilidad ≥ 90.

---

## Bloque 6 — Cierre de fase

### T60 · Dos semanas de calibración
- **Estado**: pending
- **Depende de**: T44, T51
- **Alcance**: el autor ejecuta `run-night` a mano (o con `cron` local) durante dos semanas. Cada mañana anota en `docs/CALIBRACION.md`: tokens del `Run`, porcentaje semanal consumido según Settings > Usage, ítems leídos, hallazgos publicados, calidad percibida. Al final se ajusta `nightly_tokens` para acercarse al 30% semanal y se decide si el `interest_score >= 4` es el umbral correcto.
- **Nota sobre prompt versions**: (1) `READER_PROMPT_VERSION` es `reader-v2` con abstract en `<abstract>`/`</abstract>`; (2) `POPULARIZER_PROMPT_VERSION` es `popularizer-v2` con reparador JSON; (3) `EDITOR_PROMPT_VERSION` es `editor-v1` entrada acotada a `title+level_curious`. Cada uno rompe comparabilidad con datos previos de la base — exactamente para lo que existe el campo `prompt_version` en `AgentCall`. T60 debe anotar este cambio de baselines al comparar (gasto, tasas de reintento, etc.). Primer dato real de Opus ejecutado 2026-09-17 con 3 candidatos: 3.381 tokens, 1 intento, 0,85 y 0,80 confidence en aprobados, decisión editorial correcta.
- **Hecho cuando**: `pipeline.toml` tiene valores calibrados con datos reales y un ADR documenta el criterio.

### T61 · Retrospectiva y plan de fase 2
- **Estado**: pending
- **Depende de**: T60
- **Alcance**: lista de deuda técnica real (no anticipada), decisiones abiertas que la fase 1 ha resuelto o hecho irrelevantes, y borrador de `PLAN_TAREAS.md` para fase 2 (Exoplanet Archive, Analista, explorador visual) y despliegue en Hetzner.
- **Hecho cuando**: el autor aprueba el plan de fase 2.

---

## Decisiones abiertas al arrancar

Van también a `docs/OPEN_DECISIONS.md`; se listan aquí para que el orquestador las tenga presentes desde T00.

- ~~Nombre del proyecto y del paquete Python~~ · resuelto: Nocturna, paquete `nocturna`.
- Categorías arXiv iniciales (propuesta: `astro-ph.EP`, `astro-ph.GA`; confirmar).
- Idioma de los hallazgos publicados: solo español, o español + inglés desde el principio.
- Si los prompts de agentes se versionan con un campo `prompt_version` en `AgentCall` (recomendado, coste bajo).
- Cómo autenticar el CLI de Claude Code en el VPS cuando llegue el despliegue. **No se resuelve en fase 1.**
