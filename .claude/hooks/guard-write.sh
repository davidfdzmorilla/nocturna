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
    # Un ADR es inmutable cuando está en git (índice o commit) en el repo del
    # propio fichero; un borrador sin rastrear se puede editar (T80). Falla
    # cerrado si no se puede comprobar.
    # Un symlink podría apuntar a un ADR publicado: falla cerrado. El pathspec
    # es literal (sin globs) e insensible a mayúsculas, porque en APFS
    # "0001-A.md" es el mismo fichero que "0001-a.md".
    if [ -L "$path" ]; then
      echo "BLOQUEADO: no se escribe en un ADR a través de un symlink. Falla cerrado." >&2; exit 2
    fi
    dir=$(dirname "$path"); name=$(basename "$path")
    env -u GIT_DIR -u GIT_WORK_TREE -u GIT_INDEX_FILE \
      git -C "$dir" ls-files --error-unmatch -- ":(literal,icase)$name" >/dev/null 2>&1
    rc=$?
    case $rc in
      0) echo "BLOQUEADO: este ADR ya está en git (commit o índice); se supersede con uno nuevo." >&2; exit 2;;
      1) ;;
      *) echo "BLOQUEADO: no se pudo comprobar con git si el ADR está publicado (rc=$rc). Falla cerrado." >&2; exit 2;;
    esac;;
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
