# CLAUDE.md — Nocturna

Guía para Claude Code. Léela completa antes de tocar nada. Si algo no está definido aquí ni en `docs/`, no lo inventes: regístralo en `docs/OPEN_DECISIONS.md` y pregunta.

## Qué es este proyecto

**Nocturna** es un analizador del universo: un pipeline nocturno que lee novedades astronómicas (fase 1: abstracts de arXiv astro-ph), las analiza con agentes Claude y publica "hallazgos" en una web propia de solo lectura.

Tres piezas:

1. **Ingesta**: servidores MCP propios que exponen fuentes públicas como herramientas. Una fuente que solo consume código Python (no un agente) se integra como adaptador de `infrastructure/` sin servidor MCP (ADR 0012).
2. **Análisis**: orquestador sobre el Claude Agent SDK (Python) con subagentes por rol. Corre una vez por noche, con presupuesto fijo.
3. **Web**: Next.js sirviendo hallazgos desde PostgreSQL. Nunca llama a Claude.

## Restricción que gobierna todo el diseño

El análisis corre contra la **suscripción Claude Max personal del autor**, no contra una API key. Esto implica:

- La autenticación es la del CLI de Claude Code instalado en la máquina (`claude` logueado). **Nunca** se configura `ANTHROPIC_API_KEY` salvo que se cambie explícitamente el proveedor (ver `LLMProvider`).
- El único tráfico permitido hacia Claude es el que sale del Agent SDK o de `claude -p`. Prohibido llamar al endpoint `/v1/messages` desde código propio con credenciales de suscripción.
- La web **no** hace peticiones a Claude. Ninguna función "pregúntale a Claude" para visitantes. Si algún día se quiere, va por API key y presupuesto aparte.
- El pipeline comparte el límite semanal con el uso interactivo del autor. **Presupuesto: 30% de la semana** como intención, no medible (la suscripción es compartida, ADR 0011); el control efectivo es el tope absoluto `budget.nightly_tokens`, con corte duro. Un bucle descontrolado a las 3 de la mañana deja al autor sin Claude durante días; los límites duros no son opcionales.
- Ventana de ejecución: **00:00–04:45** hora local. Kill incondicional a las 04:45 para no abrir una segunda sesión de cinco horas a las 05:00.

## Stack

- **Backend / pipeline**: Python 3.12, FastAPI (API interna de lectura + endpoint de salud), SQLAlchemy 2 + Alembic, PostgreSQL 16, `claude-agent-sdk`.
- **MCP**: servidores in-process con `create_sdk_mcp_server` y `@tool` del propio SDK. Solo se saca un servidor a proceso externo si se necesita reutilizar fuera del pipeline.
- **Web**: Next.js (App Router), TypeScript, Tailwind. Consume la API de lectura de FastAPI.
- **Local**: `docker-compose.yml` solo con PostgreSQL. Backend con `uvicorn --reload` y web con `next dev` en el host. **Sin Traefik ni proxy inverso en local.** El despliegue (Hetzner, Docker, Traefik) es fase posterior y no se anticipa en el código.
- **Gestión de dependencias Python**: `uv` con `pyproject.toml`. Node: `pnpm`.

## Estructura del repositorio

```
.
├── CLAUDE.md
├── .claude/
│   ├── settings.json           # permisos y hooks
│   ├── agents/                 # subagentes de DESARROLLO (no del pipeline)
│   ├── commands/               # /next-task, /execute-task, /commit-prepare, /commit-execute, /review, /status, /calibrate
│   ├── skills/                 # agent-sdk-usage, ddd-conventions, budget-guard-review, testing-without-claude
│   └── hooks/                  # guards que imponen las reglas de este fichero
├── docs/
│   ├── AGENTS.md               # sistema de agentes de desarrollo
│   ├── DEVELOPMENT_WORKFLOW.md # ciclo de una tarea y qué hace el autor en cada punto
│   ├── PROMPT_INICIAL.md       # primer mensaje de la primera sesión
│   ├── PLAN_TAREAS.md          # orden de trabajo, estado por tarea
│   ├── ARCHITECTURE.md         # decisiones estructurales, se actualiza al cambiar algo
│   ├── OPEN_DECISIONS.md       # lo no definido; nunca se resuelve inventando
│   ├── TECHNICAL_DEBT.md
│   └── adr/                    # una decisión por fichero, numeradas
├── backend/
│   ├── pyproject.toml
│   ├── alembic/
│   ├── src/nocturna/
│   │   ├── domain/             # entidades y reglas puras, sin IO
│   │   ├── application/        # casos de uso, orquestación de agentes
│   │   ├── infrastructure/     # SQLAlchemy, MCP servers, proveedor LLM
│   │   ├── api/                # FastAPI, solo lectura + health
│   │   └── cli.py              # `nocturna run-night`, `nocturna run-item <id>`
│   └── tests/
├── web/
│   └── (Next.js)
├── config/
│   └── pipeline.toml           # presupuesto, ventana, modelos por rol, categorías arXiv
└── docker-compose.yml
```

## Modelo de dominio (fase 1)

- **Item**: unidad de ingesta. `source` (arxiv), `external_id`, `title`, `abstract`, `categories`, `published_at`, `fetched_at`, `status` (new / read / discarded / published / failed). `status` sigue solo el camino `paper_explained`; publicar o descartar un `catalog_tension` no lo cambia, y un ítem con puntuación < 4 queda `discarded` aunque tenga un `catalog_tension` publicado. Lo publicado se decide por `Finding.published_at` (ADR 0015 §5, ADR 0017). `exoplanet_match` (fase 2, T79): marca de la ingesta, cierta si título o abstract nombran un planeta o sistema concreto según `[exoplanet_filter]`; decide `reader-v3` y la prioridad en la cola (ADR 0018).
- **Reading**: salida del Lector para un Item. `summary`, `objects` (lista de nombres), `claims` (lista), `interest_score` (1–5), `tokens_in`, `tokens_out`, `model`, `measurements` (fase 2, T71.c: lista de `Measurement` —planeta, parámetro mass/radius/period, valor, errores, unidad, límite, origen this_work/literature, cita literal—; `None` = no extraído, vacía = sin medidas; value object sin identidad, ADR 0014).
- **Finding** (hallazgo): lo que se publica. `item_id`, `type` (`paper_explained`; en fase 2 también `catalog_tension`, ADR 0012), `title`, `level_curious`, `level_amateur`, `level_technical`, `confidence` (0–1, asignado por Editor), `published_at`, `run_id`, `catalog_tension` (fase 2, T72: value object `CatalogTension` con el resultado de T73 —planeta canónico del archivo, parámetro, enlace a su ficha, umbral y σ de referencia, y cada comparación medida del paper ↔ solución previa con su σ—; informado si y solo si `type == catalog_tension`; JSONB versionado, ADR 0017).
- **Run**: una ejecución nocturna. `started_at`, `finished_at`, `status` (completed / partial / failed / killed), `budget_tokens`, `tokens_used`, `items_fetched`, `items_read`, `findings_published`, `notes`.
- **AgentCall**: registro de cada llamada a un agente. `run_id`, `item_id` (nullable), `agent` (reader / popularizer / editor), `model`, `tokens_in`, `tokens_out`, `duration_ms`, `status`, `prompt_version` (nullable).

DDD real, sin sobrearquitectura: entidades y casos de uso claros, repositorios como interfaces en `domain` implementadas en `infrastructure`. No inventar agregados, eventos ni bounded contexts que la fase 1 no necesita.

## Agentes (fase 1)

Definidos programáticamente con `AgentDefinition` en el orquestador, no en `.claude/agents/` (esa carpeta es para los agentes de *desarrollo*, ver más abajo). Los prompts viven en `backend/src/nocturna/application/agents/prompts/` como ficheros `.md` versionados.

| Agente | Modelo por defecto | Entrada | Salida (JSON estructurado) |
|---|---|---|---|
| **Reader** | Sonnet | un Item | Reading; con `reader-v3` (ítems de `[reader] measurement_categories`, hoy astro-ph.EP, con `exoplanet_match` cierto) además `measurements`, filtradas en Python medida a medida |
| **Popularizer** | Sonnet | Reading con `interest_score >= 4` | los tres niveles de texto |
| **Editor** | Opus | todos los candidatos de la noche, en una sola llamada | lista de `item_id` a publicar con `confidence` y motivo |

Reglas:

- **Una conversación nueva por ítem.** Ningún agente acumula contexto entre ítems. El contexto largo quema el límite semanal más rápido.
- Salida siempre en JSON validado con Pydantic. Si el JSON no valida, un reintento; si falla de nuevo, el ítem se marca `failed` y se sigue.
- El modelo de cada rol se lee de `config/pipeline.toml`. Nunca hardcodeado.
- Contrastador y Analista **no existen en fase 1**. No dejar stubs ni interfaces "para el futuro".

## Control de gasto (no negociable)

Implementado en `application/budget.py` y aplicado por el orquestador antes de cada llamada:

- `budget.nightly_tokens`: tope de tokens (entrada + salida) por noche. Se acumula por `AgentCall` en base de datos, no en memoria, para sobrevivir a reinicios.
- `limits.max_items_per_night`, `limits.max_turns_per_agent`, `limits.item_timeout_s`, `limits.run_timeout_s`.
- `window.start = "00:00"`, `window.hard_stop = "04:45"`. Un hook `PreToolUse`/comprobación previa a cada llamada verifica la hora; pasado `hard_stop`, se cancela lo que haya en vuelo y el Run se marca `killed`.
- Orden de gasto en la noche: primero Reader sobre todos los ítems (barato), después Popularizer sobre los candidatos, y **Editor al final con presupuesto reservado**. Si el presupuesto se agota antes del Editor, el Run se marca `partial` y no se publica nada esa noche.
- El Editor se llama como máximo **una vez por noche**.
- `budget.weekly_reset_weekday` y `budget.weekly_reset_hour` se leen de configuración; el orquestador puede aplicar `budget.reset_day_multiplier` esa noche.

Valor fijo (sin calibración automática): `nightly_tokens = 300_000`. Si el autor observa presión en su presupuesto semanal en otros proyectos, baja este tope a mano. Otros valores: `max_items_per_night = 30` (desde T71.c, ADR 0014), `max_turns_per_agent = 3`. Cada variante del Reader autoriza con su propia estimación (`reader_estimated_tokens`, `reader_v3_estimated_tokens`).

## Proveedor LLM desacoplado

`domain/llm.py` define la interfaz `LLMProvider` (lo mínimo: `run_agent(agent, input) -> AgentResult` con tokens y modelo). Implementaciones en `infrastructure/llm/`:

- `AgentSDKProvider`: la única activa en fase 1. Usa `claude-agent-sdk` con la autenticación del CLI.
- `ApiKeyProvider`: **no se implementa en fase 1**. Se documenta en `ARCHITECTURE.md` que existe el hueco. Si la política de suscripción cambia, cambiar el proveedor es un valor en `pipeline.toml`.

## Web (fase 1)

- `/` feed cronológico de Findings publicados, paginado.
- `/hallazgo/[id]` detalle con los tres niveles y enlace al arXiv original.
- Banner fijo y visible: "Análisis generado automáticamente por IA. No es un resultado científico validado."
- SSR/ISR contra la API de lectura. Cero llamadas a Claude, cero lógica de análisis.
- Sin autenticación, sin panel de administración en fase 1.

## Cómo trabajo con Claude Code en este repo

### Flujo

1. El orquestador de desarrollo lee `docs/PLAN_TAREAS.md`, toma la primera tarea `pending` cuyas dependencias estén `done`, y **propone un plan** al autor.
2. **Nada se ejecuta sin aprobación explícita del plan.**
3. Los subagentes de desarrollo (definidos en `.claude/agents/`) hacen el trabajo: architect, backend, frontend, database, tester, reviewer, docs-keeper, committer. Detalle en `docs/AGENTS.md`; ciclo completo en `docs/DEVELOPMENT_WORKFLOW.md`.
4. Al terminar, se actualiza el estado de la tarea en `PLAN_TAREAS.md` y, si aplica, `ARCHITECTURE.md` o un ADR nuevo.
5. **Preparar el commit y ejecutarlo son pasos separados.** Se muestra el mensaje y los ficheros, y se espera confirmación.

### Reglas

- Comunicación con el autor en español, tersa y directa. Decisiones tomadas y documentadas antes que preguntas; pero lo no definido **no se inventa**: va a `OPEN_DECISIONS.md`.
- Los commits **nunca** llevan atribución a Claude, Anthropic ni a ninguna IA. Ni en el mensaje, ni en `Co-authored-by`, ni en comentarios de código.
- Mensajes de commit en inglés, imperativo, conventional commits (`feat:`, `fix:`, `docs:`, `chore:`).
- Tests para dominio y casos de uso (pytest). Los agentes LLM se prueban con un `FakeLLMProvider` que devuelve JSON fijo; **ningún test llama a Claude de verdad**.
- No añadir dependencias sin justificarlo en el plan.
- No preparar nada para producción (Traefik, dominios, TLS, secretos de Hetzner) hasta que el plan lo diga.
- Cuando una tarea toca el control de gasto, el Reviewer revisa específicamente que ningún camino de código pueda llamar a un agente saltándose `budget.py`.

### Comandos

```
# local
docker compose up -d                      # PostgreSQL
cd backend && uv sync && uv run alembic upgrade head
uv run nocturna run-night --dry-run       # ingesta + plan de gasto, sin llamar a agentes
uv run nocturna run-night                 # ejecución real
uv run nocturna run-item <item_id>        # un solo ítem, para depurar
uv run nocturna archive-snapshot [--full] [--dry-run]  # histórico del NASA Exoplanet Archive (T81); --dry-run no escribe
uv run pytest
uv run pytest -m db                       # contra el PostgreSQL de compose
uv run pytest -m manual                   # humos que SÍ llaman a Claude; requiere NOCTURNA_ALLOW_REAL_CLAUDE=1

# API de lectura (terminal aparte, desde backend/)
uv run uvicorn nocturna.api.app:create_app --factory --reload --port 8000

# datos de demostración, mientras no haya corrido una noche real
NOCTURNA_ALLOW_SEED=1 uv run python scripts/seed_demo.py

# web (terminal aparte)
cd web && pnpm install && pnpm dev        # necesita NOCTURNA_API_URL=http://localhost:8000
cd web && pnpm test && pnpm lint && pnpm build
```

## Estado actual

**Fase 1 completa en código y verificada en producción**: Reader (T41), Popularizer (T42), Editor (T43), Orquestador nocturno (T44), API de lectura (T50) y Web Next.js (T51) finalizados. Cuatro noches automáticas ejecutadas sin interrupciones (2026-09-25 a 2026-09-28) con launchd. Gasto: 76–84% del tope nocturno (300.000 tokens). Humos reales: Reader 3.056 tokens/ítem, Popularizer ~4.560 tokens/candidato (~9.120 con reintento), Editor 3.381 tokens/3 candidatos (Opus). Gasto lateral del CLI (Haiku) ~1.163 tokens/sesión, variable entre versiones. API: `/health`, `/findings?page=&size=`, `/findings/{id}` sobre PostgreSQL, sin escritura, CORS para `localhost:3000`. Web: SSR dinámico, feed paginado `/`, detalle `/hallazgo/[id]` con selector de nivel por URL, Lighthouse accesibilidad 100/100. Suite backend: 2069 passed a 2026-10-01. Suite web: 37 tests verdes, lint y build verdes con API parada. T60 cerrada (ADR 0011). T61 cerrada: fase 2 aprobada (ADR 0012): tensión de un objeto frente al NASA Exoplanet Archive, calculada en Python; Claude solo redacta. Fase 2 en curso: T70 (tope de `page`), T71 (viabilidad), T71.b (prueba de atribución) y T71.c (Reader v3 con medidas, ADR 0013/0014) cerradas; la primera noche con `reader-v3` (2026-09-30) completó sin incidencias. T73 (cálculo de σ, ADR 0015) y T74 (adaptador del NASA Exoplanet Archive y `--dry-run` con cruce, ADR 0016) cerradas; T75 espera 7 noches con `reader-v3`. T72 (tipo `catalog_tension` en el dominio, ADR 0017) cerrada. T79 (filtro de exoplanetas en la ingesta y prioridad en la cola, ADR 0018) cerrada: mergeada, base migrada y rellenada el 2026-10-01. Fase 2 con dos vías (decisión del autor del 2026-10-01): arXiv y el NASA Exoplanet Archive, unidas por `arxiv_id`. T80 (guard de ADR) cerrada. T81 (snapshot semanal del archivo, ADR 0019) implementada: pendiente de merge con migración; ver DEVELOPMENT_WORKFLOW.md. Después T82 (`run-item --reader v3 --force`), T83–T85 y ADR 0020 (T86); T75 sigue a la espera de 7 noches con `reader-v3`. Sin despliegue.
