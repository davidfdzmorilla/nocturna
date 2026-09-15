#!/usr/bin/env bash
# SubagentStop — deja traza de qué subagente terminó, para auditar la sesión. Nunca bloquea.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
raw=$(python3 "$HERE/hook_input.py" agent session 2>/dev/null) || exit 0
mkdir -p "${CLAUDE_PROJECT_DIR:-.}/.claude/logs"
printf '%s\tagent=%s\tsession=%s\n' "$(date -Iseconds)" "${raw%%$'\x1f'*}" "${raw#*$'\x1f'}" >> "${CLAUDE_PROJECT_DIR:-.}/.claude/logs/subagents.log"
exit 0
