#!/usr/bin/env bash
# PreToolUse:Bash — reglas duras del proyecto. exit 2 bloquea y devuelve stderr a Claude.
# Falla cerrado: si no puede leer la entrada, bloquea.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cmd=$(python3 "$HERE/hook_input.py" command) || { echo "BLOQUEADO: el hook no pudo leer la entrada (python3 ausente o JSON inválido)." >&2; exit 2; }
[ -z "$cmd" ] && exit 0

# 1. Nunca llamar a la API con credenciales de suscripción ni exportar API key
if printf '%s' "$cmd" | grep -qE 'ANTHROPIC_API_KEY|api\.anthropic\.com/v1/messages'; then
  echo "BLOQUEADO: este proyecto corre contra la suscripción vía Agent SDK. Ni ANTHROPIC_API_KEY ni llamadas directas a /v1/messages. Ver CLAUDE.md." >&2
  exit 2
fi

# 2. Commits: sin atribución a IA, sin --no-verify, y solo vía /commit-execute
if printf '%s' "$cmd" | grep -qE '(^|[;&|]\s*)git\s+commit'; then
  # Atribución, no mención: el nombre del SDK puede aparecer en el mensaje.
  if printf '%s' "$cmd" | grep -qiE 'co-authored-by|generated with|🤖|claude code|by (claude|anthropic)'; then
    echo "BLOQUEADO: el mensaje de commit contiene atribución a IA. Regla del proyecto: nunca. Reescribe el mensaje." >&2
    exit 2
  fi
  if printf '%s' "$cmd" | grep -qE -- '--no-verify'; then
    echo "BLOQUEADO: --no-verify no está permitido." >&2
    exit 2
  fi
  if [ ! -f "${CLAUDE_PROJECT_DIR:-.}/.claude/.commit-approved" ]; then
    echo "BLOQUEADO: no hay aprobación de commit. Flujo: /commit-prepare -> el autor aprueba -> /commit-execute. Ver docs/DEVELOPMENT_WORKFLOW.md." >&2
    exit 2
  fi
fi

# 3. Nada de infraestructura de producción en fase 1
if printf '%s' "$cmd" | grep -qiE 'traefik|hetzner|certbot|letsencrypt'; then
  echo "BLOQUEADO: despliegue/Traefik no forman parte de la fase 1. Regístralo en docs/OPEN_DECISIONS.md si crees que hace falta." >&2
  exit 2
fi

# 4. Comandos destructivos
if printf '%s' "$cmd" | grep -qE 'rm\s+-rf\s+/|git\s+push\s+.*(-f|--force)|docker\s+compose\s+down\s+.*-v'; then
  echo "BLOQUEADO: comando destructivo. Pide confirmación explícita al autor fuera del hook." >&2
  exit 2
fi

exit 0
