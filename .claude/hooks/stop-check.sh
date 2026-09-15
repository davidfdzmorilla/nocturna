#!/usr/bin/env bash
# Stop — al terminar un turno del orquestador: aviso si hay trabajo sin registrar y caduca la aprobación de commit.
set -uo pipefail
cd "$CLAUDE_PROJECT_DIR" || exit 0
if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  if [ -n "$(git status --porcelain 2>/dev/null)" ] && ! git status --porcelain 2>/dev/null | grep -q 'docs/PLAN_TAREAS.md'; then
    echo "Hay cambios sin commit y docs/PLAN_TAREAS.md no se ha tocado. Si la tarea avanzó, actualiza su estado antes de /commit-prepare." >&2
  fi
fi
rm -f .claude/.commit-approved
exit 0
