# Nocturna

Analizador del universo: un pipeline nocturno que lee novedades astronómicas (fase 1: abstracts de arXiv astro-ph), las analiza con agentes Claude y publica "hallazgos" en una web de solo lectura.

## Restricción de diseño

El pipeline corre contra la suscripción Claude Max personal del autor a través del Agent SDK, **nunca con `ANTHROPIC_API_KEY`**. Esto implica presupuesto fijo (30% semanal), ventana temporal estrecha (00:00–04:45) y corte incondicional para no interferir con el trabajo diario. Ver [ADR 0001](docs/adr/0001-suscripcion-como-proveedor.md).

## Estructura del repositorio

Estructura objetivo de la fase 1. Hoy solo existen la documentación y las carpetas vacías: T02 añade `docker-compose.yml`, `backend/pyproject.toml` y `config/pipeline.toml`; T51 genera el proyecto Next.js.

```
├── CLAUDE.md                          # guía para trabajar con Claude Code
├── README.md                          # este archivo
├── .gitignore
├── .claude/                           # permisos, subagentes, comandos, skills y hooks
├── config/
│   └── pipeline.toml                  # configuración (presupuesto, modelos, arXiv)
├── backend/
│   ├── pyproject.toml
│   ├── alembic/                       # migraciones
│   ├── src/nocturna/
│   │   ├── domain/                    # entidades y reglas puras
│   │   ├── application/               # casos de uso, agentes
│   │   ├── infrastructure/            # SQLAlchemy, MCP, LLM
│   │   ├── api/                       # FastAPI, lectura + salud
│   │   └── cli.py
│   └── tests/
├── web/                               # Next.js (App Router, TypeScript, Tailwind)
├── docs/
│   ├── AGENTS.md                      # sistema de subagentes de desarrollo
│   ├── DEVELOPMENT_WORKFLOW.md        # ciclo de una tarea
│   ├── ARCHITECTURE.md                # decisiones estructurales
│   ├── PLAN_TAREAS.md                 # orden de trabajo
│   ├── OPEN_DECISIONS.md              # lo no definido
│   ├── PROMPT_INICIAL.md              # primer mensaje de la primera sesión
│   ├── TECHNICAL_DEBT.md
│   └── adr/0001-suscripcion-como-proveedor.md
└── docker-compose.yml                 # PostgreSQL 16
```

## Orden de lectura de la documentación

1. `README.md` (este archivo)
2. `CLAUDE.md` (guía de trabajo)
3. `docs/AGENTS.md` (sistema de agentes de desarrollo)
4. `docs/DEVELOPMENT_WORKFLOW.md` (ciclo de una tarea)
5. `docs/ARCHITECTURE.md` (decisiones estructurales)
6. `docs/adr/0001-suscripcion-como-proveedor.md` (decisión sobre el proveedor LLM)
7. `docs/PLAN_TAREAS.md` (orden de trabajo)
8. `docs/OPEN_DECISIONS.md` (lo no definido)
9. `docs/TECHNICAL_DEBT.md` (deuda técnica real)
10. `docs/PROMPT_INICIAL.md` (arranque de la primera sesión de trabajo)

## Comandos locales

Referencia de la fase 1 completa. Ninguno funciona todavía: dependen de T02 en adelante.

```bash
# Inicia PostgreSQL
docker compose up -d

# Instala dependencias Python y crea base de datos
cd backend
uv sync
uv run alembic upgrade head

# Ingesta sin llamar a agentes (verificación de configuración)
uv run nocturna run-night --dry-run

# Ejecución real de una noche completa
uv run nocturna run-night

# Depuración de un ítem específico
uv run nocturna run-item <item_id>

# Tests
uv run pytest

# Web de lectura
cd web
pnpm install
pnpm dev
```

## Advertencia

La web publica **análisis automatizado por inteligencia artificial, no validado científicamente**. Los hallazgos son anotaciones generadas a partir de abstracts; no sustituyen la lectura del artículo completo ni confirmación experta.
