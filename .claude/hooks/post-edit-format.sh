#!/usr/bin/env bash
# PostToolUse:Edit|Write — formatea el fichero tocado. Nunca bloquea.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
path=$(python3 "$HERE/hook_input.py" file_path 2>/dev/null) || exit 0
{ [ -z "$path" ] || [ ! -f "$path" ]; } && exit 0
cd "${CLAUDE_PROJECT_DIR:-.}" || exit 0
case "$path" in
  *.py)
    [ -d backend ] && (cd backend && uv run ruff format "$path" >/dev/null 2>&1; uv run ruff check --fix "$path" >/dev/null 2>&1) ;;
  *.ts|*.tsx|*.js|*.jsx|*.json|*.css)
    [ -f web/package.json ] && (cd web && pnpm exec prettier --write "$path" >/dev/null 2>&1) ;;
esac
exit 0
