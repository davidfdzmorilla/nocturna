# CLAUDE.md — Nocturna

Guía para Claude Code. Léela completa antes de tocar nada. Si algo no está definido aquí ni en `docs/`, no lo inventes: regístralo en `docs/OPEN_DECISIONS.md` y pregunta.

## Qué es este proyecto

**Nocturna** es un analizador del universo: un pipeline nocturno que lee novedades astronómicas (fase 1: abstracts de arXiv astro-ph), las analiza con agentes Claude y publica "hallazgos" en una web propia de solo lectura.

Tres piezas:

1. **Ingesta**: servidores MCP propios que exponen fuentes públicas como herramientas.
2. **Análisis**: orquestador sobre el Claude Agent SDK (Python) con subagentes por rol. Corre una vez por noche, con presupuesto fijo.
3. **Web**: Next.js sirviendo hallazgos desde PostgreSQL. Nunca llama a Claude.

## Restricción que gobierna todo el diseño

El análisis corre contra la **suscripción Claude Max personal del autor**, no contra una API key. Esto implica:

- La autenticación es la del CLI de Claude Code instalado en la máquina (`claude` logueado). **Nunca** se configura `ANTHROPIC_API_KEY` salvo que se cambie explícitamente el proveedor (ver `LLMProvider`).
- El único tráfico permitido hacia Claude es el que sale del Agent SDK o de `claude -p`. Prohibido llamar al endpoint `/v1/messages` desde código propio con credenciales de suscripción.
- La web **no** hace peticiones a Claude. Ninguna función "pregúntale a Claude" para visitantes. Si algún día se quiere, va por API key y presupuesto aparte.
- El pipeline comparte el límite semanal con el uso interactivo del autor. **Presupuesto: 30% de la semana**, configurable, con corte duro. Un bucle descontrolado a las 3 de la mañana deja al autor sin Claude durante días; los límites duros no son opcionales.
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

- **Item**: unidad de ingesta. `source` (arxiv), `external_id`, `title`, `abstract`, `categories`, `published_at`, `fetched_at`, `status` (new / read / discarded / published / failed).
- **Reading**: salida del Lector para un Item. `summary`, `objects` (lista de nombres), `claims` (lista), `interest_score` (1–5), `tokens_in`, `tokens_out`, `model`.
- **Finding** (hallazgo): lo que se publica. `item_id`, `type` (fase 1: `paper_explained`), `title`, `level_curious`, `level_amateur`, `level_technical`, `confidence` (0–1, asignado por Editor), `published_at`, `run_id`.
- **Run**: una ejecución nocturna. `started_at`, `finished_at`, `status` (completed / partial / failed / killed), `budget_tokens`, `tokens_used`, `items_fetched`, `items_read`, `findings_published`, `notes`.
- **AgentCall**: registro de cada llamada a un agente. `run_id`, `item_id` (nullable), `agent` (reader / popularizer / editor), `model`, `tokens_in`, `tokens_out`, `duration_ms`, `status`, `prompt_version` (nullable).

DDD real, sin sobrearquitectura: entidades y casos de uso claros, repositorios como interfaces en `domain` implementadas en `infrastructure`. No inventar agregados, eventos ni bounded contexts que la fase 1 no necesita.

## Agentes (fase 1)

Definidos programáticamente con `AgentDefinition` en el orquestador, no en `.claude/agents/` (esa carpeta es para los agentes de *desarrollo*, ver más abajo). Los prompts viven en `backend/src/nocturna/application/agents/prompts/` como ficheros `.md` versionados.

| Agente | Modelo por defecto | Entrada | Salida (JSON estructurado) |
|---|---|---|---|
| **Reader** | Sonnet | un Item | Reading |
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

Valores iniciales conservadores (se calibran con Settings > Usage las dos primeras semanas): `nightly_tokens = 300_000`, `max_items_per_night = 40`, `max_turns_per_agent = 3`.

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
uv run pytest
cd web && pnpm install && pnpm dev
```

## Estado actual

Fase 1: Reader (T41), Popularizer (T42), Editor (T43) y Orquestador nocturno (T44) completados. Humos reales ejecutados: Reader 3.056 tokens/ítem, Popularizer ~4.560 tokens/candidato (~9.120 con reintento), Editor 3.381 tokens/3 candidatos (Opus). Gasto lateral del CLI (Haiku) ~1.163 tokens/sesión, variable entre versiones, sin control desde `pipeline.toml`. Suite: 773 passed. Próximo: T50 (API de lectura) y T60 (calibración real de dos semanas).
