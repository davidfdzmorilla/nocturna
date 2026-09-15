# Sistema de agentes de desarrollo

Este documento describe **cómo se desarrolla** el proyecto con Claude Code. No confundir con los agentes del pipeline (Reader, Popularizer, Editor), que viven en `backend/src/nocturna/application/agents/` y se describen en `ARCHITECTURE.md`.

## Piezas

| Pieza | Dónde | Para qué |
|---|---|---|
| Orquestador | la sesión principal de Claude Code, guiada por `CLAUDE.md` y los comandos | Lee el plan, delega, pide aprobación, nunca implementa directamente |
| Subagentes | `.claude/agents/*.md` | Trabajo especializado con contexto aislado |
| Comandos | `.claude/commands/*.md` | Puntos de entrada del flujo (`/next-task`, `/execute-task`, …) |
| Skills | `.claude/skills/*/SKILL.md` | Conocimiento reutilizable que un subagente invoca cuando lo necesita |
| Hooks | `.claude/settings.json` + `.claude/hooks/*.sh` | Reglas que se **imponen**, no se sugieren |

## Subagentes

| Nombre | Modelo | Herramientas | Rol |
|---|---|---|---|
| `architect` | opus | lectura | Plan de implementación para aprobación. No escribe código. |
| `backend` | sonnet | lectura, escritura, bash | Python: dominio, casos de uso, infraestructura, MCP, API |
| `database` | sonnet | lectura, escritura, bash | Modelos ORM, migraciones, repositorios |
| `frontend` | sonnet | lectura, escritura, bash | Next.js |
| `tester` | sonnet | lectura, escritura, bash | Tests; nunca llama a Claude |
| `reviewer` | opus | lectura, bash | Revisión con veredicto; dos pasadas si toca control de gasto |
| `docs-keeper` | haiku | lectura, escritura | Cierra la tarea en `docs/` |
| `committer` | haiku | lectura, bash | PREPARE y EXECUTE de commits, separados |

Elección de modelos: Opus donde el juicio importa (diseño, revisión), Sonnet para implementar, Haiku para tareas mecánicas. Todo esto consume la suscripción Max del autor durante el día, a la vez que el pipeline la consume de noche; por eso los subagentes son estrechos y devuelven resúmenes, no volcados.

## Skills

| Skill | La invoca | Cuándo |
|---|---|---|
| `agent-sdk-usage` | `backend` | Antes de escribir código que importe `claude_agent_sdk` |
| `ddd-conventions` | `backend`, `database`, `reviewer` | Al tocar `domain/` o `application/` |
| `budget-guard-review` | `reviewer` | Toda revisión que toque `budget.py`, `LLMProvider` o un agente |
| `testing-without-claude` | `tester` | Al escribir tests en `backend/tests` |

## Hooks

| Evento | Script | Qué impone |
|---|---|---|
| `PreToolUse` Bash | `guard-bash.sh` | Sin `ANTHROPIC_API_KEY` ni llamadas a `/v1/messages`; commits sin atribución a IA, sin `--no-verify` y solo con aprobación (`.claude/.commit-approved`); nada de Traefik/Hetzner en fase 1; sin comandos destructivos |
| `PreToolUse` Edit/Write | `guard-write.sh` | Sin escribir `.env`; ADR existentes inmutables; sin `ANTHROPIC_API_KEY` en contenido; sin atribución a IA; `domain/` sin imports de infraestructura |
| `PostToolUse` Edit/Write | `post-edit-format.sh` | `ruff` / `prettier` sobre el fichero tocado |
| `SubagentStop` | `subagent-stop.sh` | Traza en `.claude/logs/subagents.log` |
| `Stop` | `stop-check.sh` | Avisa si hay cambios sin reflejar en `PLAN_TAREAS.md`; borra `.commit-approved` |

Los hooks bloquean con `exit 2` y devuelven el motivo a Claude por `stderr`. Leen el JSON de entrada con `python3` (`hooks/hook_input.py`); los guards fallan cerrados: si no pueden leer la entrada, bloquean. `permissions.deny` en `settings.json` es una segunda capa (lectura de `.env`, `git push`, `rm -rf`).

## Por qué el commit va en dos pasos

`/commit-prepare` muestra ficheros y mensaje y termina el turno. `/commit-execute` crea `.claude/.commit-approved`, ejecuta y lo borra. `guard-bash.sh` rechaza cualquier `git commit` sin ese fichero, y `stop-check.sh` lo borra al final de cada turno. Resultado: no puede haber un commit que el autor no haya visto antes.
