#!/usr/bin/env bash
# PreToolUse:Edit|Write — protege ficheros y contenido. Falla cerrado.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
raw=$(python3 "$HERE/hook_input.py" file_path content 2>/dev/null) || { echo "BLOQUEADO: el hook no pudo leer la entrada." >&2; exit 2; }
path="${raw%%$'\x1f'*}"
content="${raw#*$'\x1f'}"

case "$path" in
  *.env|*.env.*)
    echo "BLOQUEADO: no se escriben ficheros .env desde Claude Code. El autor los gestiona a mano." >&2; exit 2;;
  */docs/adr/*)
    if [ -f "$path" ]; then
      echo "BLOQUEADO: los ADR existentes no se editan; se supersede con uno nuevo." >&2; exit 2
    fi;;
esac

if printf '%s' "$content" | grep -qE 'ANTHROPIC_API_KEY\s*='; then
  echo "BLOQUEADO: no se configura ANTHROPIC_API_KEY en este proyecto. Ver CLAUDE.md, sección 'Restricción que gobierna todo el diseño'." >&2
  exit 2
fi

if printf '%s' "$content" | grep -qiE 'co-authored-by:.*(claude|anthropic)|generated (with|by) claude'; then
  echo "BLOQUEADO: atribución a IA en contenido. No se permite en código, docs ni commits." >&2
  exit 2
fi

# Dominio puro: sin IO en backend/src/nocturna/domain/
case "$path" in
  */backend/src/nocturna/domain/*.py)
    if printf '%s' "$content" | grep -qE '^\s*(from|import)\s+(sqlalchemy|claude_agent_sdk|fastapi|httpx|requests)'; then
      echo "BLOQUEADO: domain/ no importa infraestructura (SQLAlchemy, SDK, FastAPI, HTTP). Muévelo a application/ o infrastructure/." >&2
      exit 2
    fi;;
esac

exit 0
