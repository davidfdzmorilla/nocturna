#!/usr/bin/env bash
# night-report.sh — Informe de la mañana (T60): versiones + siete consultas
# de solo lectura sobre la última noche, vía el psql del contenedor de
# `docker compose` (sin dependencias nuevas). Uso:
#
#     backend/scripts/night-report.sh
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

echo "Fecha del informe: $(date '+%Y-%m-%d %H:%M %Z')"
echo "Claude Code CLI: $(claude --version)"
echo "claude-agent-sdk: $(cd "${repo_root}/backend" && uv pip show claude-agent-sdk | awk '/^Version:/ {print $2}')"
echo

docker compose -f "${repo_root}/docker-compose.yml" exec -T postgres \
    psql -U nocturna -d nocturna -v ON_ERROR_STOP=1 < "${script_dir}/night_report.sql"
