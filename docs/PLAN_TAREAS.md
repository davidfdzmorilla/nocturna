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
- **Estado**: pending
- **Depende de**: T00
- **Alcance**: Architect, Backend, Frontend, Database, Tester, Reviewer, Docs-keeper, Committer. Cada uno con su rol, herramientas permitidas y las reglas de `CLAUDE.md` que le aplican (Docs-keeper: cierre de tarea en documentación; Committer: sin atribución a IA, preparar ≠ ejecutar; Reviewer: vigilancia específica sobre `budget.py`).
- **Hecho cuando**: el orquestador puede delegar T02 sin instrucciones adicionales.

### T02 · Entorno local
- **Estado**: pending
- **Depende de**: T01
- **Alcance**: `docker-compose.yml` solo con PostgreSQL 16 y volumen. `backend/pyproject.toml` con `uv`, FastAPI, SQLAlchemy 2, Alembic, Pydantic, `claude-agent-sdk`, pytest. `config/pipeline.toml` con todos los valores iniciales de `CLAUDE.md`. Carga de configuración tipada (Pydantic Settings).
- **Hecho cuando**: `docker compose up -d`, `uv sync`, `uv run pytest` (con un test trivial) pasan en limpio.

---

## Bloque 1 — Dominio y persistencia

### T10 · Modelo de dominio
- **Estado**: pending
- **Depende de**: T02
- **Alcance**: entidades `Item`, `Reading`, `Finding`, `Run`, `AgentCall` en `domain/`, con sus reglas (transiciones de `status`, `interest_score` en 1–5, `confidence` en 0–1). Interfaces de repositorio. Interfaz `LLMProvider`.
- **Hecho cuando**: tests de dominio cubren transiciones e invariantes. Cero imports de SQLAlchemy o del SDK en `domain/`.

### T11 · Persistencia
- **Estado**: pending
- **Depende de**: T10
- **Alcance**: modelos SQLAlchemy, migración inicial Alembic, repositorios en `infrastructure/`. Índices en `items(source, external_id)` (único) y `findings(published_at)`.
- **Hecho cuando**: `alembic upgrade head` limpio; tests de repositorio contra PostgreSQL de compose.

---

## Bloque 2 — Ingesta

### T20 · Servidor MCP `arxiv-astro`
- **Estado**: pending
- **Depende de**: T11
- **Alcance**: servidor in-process con dos herramientas: `fetch_new(since: date, categories: list[str])` y `get_abstract(arxiv_id: str)`. Cliente HTTP a la API de arXiv con respeto a su rate limit (3 s entre peticiones). Los resultados se persisten como `Item` con `status = new`; deduplicación por `external_id`.
- **Hecho cuando**: `nocturna run-night --dry-run` ingesta los abstracts del día para las categorías configuradas y los muestra sin llamar a ningún agente. Tests con respuestas de arXiv grabadas.

---

## Bloque 3 — Control de gasto (antes que cualquier agente)

### T30 · `budget.py` y límites duros
- **Estado**: pending
- **Depende de**: T11
- **Alcance**: `BudgetGuard` que lee `pipeline.toml`, acumula tokens desde `AgentCall` en base de datos y expone `can_call(agent, estimated_tokens) -> bool`. Comprobación de ventana horaria con `hard_stop`. Reserva de presupuesto para el Editor. Contadores de ítems y turnos. Marcado de `Run` como `partial` / `killed`.
- **Hecho cuando**: tests que demuestran: corte al alcanzar `nightly_tokens`; rechazo pasada `hard_stop`; reserva del Editor intacta aunque el Reader agote lo suyo; persistencia del acumulado tras reinicio simulado.
- **Nota para el Reviewer**: esta tarea se revisa dos veces. Nada en Bloque 4 arranca sin T30 `done`.

---

## Bloque 4 — Agentes

### T40 · `AgentSDKProvider` y `FakeLLMProvider`
- **Estado**: pending
- **Depende de**: T30
- **Alcance**: implementación de `LLMProvider` sobre `claude-agent-sdk` con `query()` y `ClaudeAgentOptions`: modelo por rol, `max_turns` desde configuración, servidores MCP inyectados, sin `setting_sources` (todo programático), sin `ANTHROPIC_API_KEY`. Extracción de tokens y modelo de los mensajes `result`. `FakeLLMProvider` para tests que devuelve JSON fijo por agente.
- **Hecho cuando**: un test de humo **manual y marcado como tal** (`pytest -m manual`) hace una llamada real mínima y registra tokens en `AgentCall`. El resto de la suite no toca Claude.
- **Antes de empezar**: consultar la documentación vigente del SDK de Python (`platform.claude.com/docs/en/agent-sdk/python`) para nombres de opciones y estructura de mensajes; no fiarse de memoria.

### T41 · Agente Reader
- **Estado**: pending
- **Depende de**: T40, T20
- **Alcance**: prompt en `prompts/reader.md`, esquema Pydantic de salida (`Reading`), caso de uso `ReadItem` que pasa por `BudgetGuard`, valida JSON, reintenta una vez, marca `failed` si vuelve a fallar. Una conversación por ítem.
- **Hecho cuando**: `nocturna run-item <id>` produce una `Reading` persistida. Tests con `FakeLLMProvider` para el camino feliz, JSON inválido y presupuesto agotado.

### T42 · Agente Popularizer
- **Estado**: pending
- **Depende de**: T41
- **Alcance**: prompt en `prompts/popularizer.md`, salida con `level_curious`, `level_amateur`, `level_technical`. Caso de uso `PopularizeReading`, solo para `interest_score >= 4`. Crea `Finding` en estado no publicado.
- **Hecho cuando**: tests equivalentes a T41. Un `Finding` sin publicar en base de datos tras `run-item` sobre un ítem con puntuación alta.

### T43 · Agente Editor
- **Estado**: pending
- **Depende de**: T42
- **Alcance**: prompt en `prompts/editor.md`. Recibe todos los `Finding` candidatos de la noche en **una sola llamada** con Opus; devuelve lista de `item_id` a publicar, `confidence` y motivo. Caso de uso `EditNight` que publica los aprobados y descarta el resto.
- **Hecho cuando**: tests: publica solo los aprobados; si `BudgetGuard` no permite la llamada, ningún `Finding` se publica y el `Run` queda `partial`.

### T44 · Orquestador `run-night`
- **Estado**: pending
- **Depende de**: T43
- **Alcance**: caso de uso `RunNight`: crea `Run`, ingesta (T20), Reader sobre todos los `new` en orden de llegada hasta `max_items_per_night`, Popularizer sobre candidatos, Editor al final, cierre del `Run` con estado y métricas. Manejo de `hard_stop` cancelando lo que esté en vuelo. Logging estructurado (JSON) por ítem y por agente.
- **Hecho cuando**: una noche completa en local, contra la suscripción, termina con `Run.status = completed` y hallazgos publicados. Se registra en `docs/` el consumo observado en Settings > Usage a la mañana siguiente (primera calibración).

---

## Bloque 5 — Web

### T50 · API de lectura
- **Estado**: pending
- **Depende de**: T11
- **Alcance**: FastAPI con `GET /health`, `GET /findings?page=&size=`, `GET /findings/{id}`. Solo `Finding` publicados. Sin escritura. CORS para `localhost:3000`.
- **Hecho cuando**: tests de API contra base de datos de compose. Puede empezar en paralelo con Bloque 4 si el autor lo aprueba.

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
