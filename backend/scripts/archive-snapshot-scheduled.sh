#!/usr/bin/env bash
# archive-snapshot-scheduled.sh — Envoltorio de lanzamiento programado de
# `nocturna archive-snapshot` para `launchd` (T81). Sin LLM ni tokens: no
# necesita `claude`, ni centinela, ni ventana horaria. Una ejecución repetida
# es inocua (el snapshot es idempotente salvo por la fila del snapshot).
#
# Uso:
#     backend/scripts/archive-snapshot-scheduled.sh [args...]
# Los args se reenvían a `nocturna archive-snapshot` (--full, --dry-run).
#
# Variables de entorno:
#     NOCTURNA_LOG_DIR   Directorio de logs. Por defecto: $HOME/nocturna-logs.
#     NOCTURNA_WAIT_S    Segundos máximos de espera a Docker y a
#                        `nocturna-postgres` healthy. Por defecto: 300.
#     NOCTURNA_PATH      Rutas donde buscar `uv`. Por defecto:
#                        $HOME/.local/bin:/usr/bin:/bin:/usr/sbin:/sbin
#
# Códigos de salida:
#     0-2   Los de `nocturna archive-snapshot`, propagados sin modificar.
#     75    Precondiciones no listas (Docker o postgres) dentro de NOCTURNA_WAIT_S.
#     77    Entorno inválido: `uv` no resoluble en PATH, o NOCTURNA_WAIT_S /
#           NOCTURNA_LOG_DIR con un valor inválido.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

# Solo `uv`: este subcomando no lanza `claude`. Sin `source` de perfiles de
# shell: un proceso desatendido no hereda el entorno interactivo.
export PATH="${NOCTURNA_PATH:-$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin}"

# `${VAR-default}` sin dos puntos, a propósito: una variable exportada vacía
# debe llegar intacta a las validaciones y fallar con 77.
log_dir="${NOCTURNA_LOG_DIR-$HOME/nocturna-logs}"
wait_s="${NOCTURNA_WAIT_S-300}"

if [[ ! "$wait_s" =~ ^[0-9]+$ ]]; then
    echo "archive-snapshot-scheduled: NOCTURNA_WAIT_S debe ser un entero no negativo, no '$wait_s'" >&2
    exit 77
fi
if [[ -z "$log_dir" ]]; then
    echo "archive-snapshot-scheduled: NOCTURNA_LOG_DIR no puede estar vacío" >&2
    exit 77
fi
if ! command -v uv >/dev/null 2>&1; then
    echo "archive-snapshot-scheduled: no se encuentra 'uv' en PATH ($PATH); abortando" >&2
    exit 77
fi

mkdir -p "$log_dir"
day_key="$(date +%Y%m%d)"
out_log="$log_dir/archive-$day_key.out.log"
err_log="$log_dir/archive-$day_key.err.log"

wait_deadline=$(( $(date +%s) + wait_s ))

_poll_until_deadline() {
    while true; do
        if "$@" >/dev/null 2>&1; then
            return 0
        fi
        local now remaining
        now="$(date +%s)"
        if (( now >= wait_deadline )); then
            return 1
        fi
        remaining=$(( wait_deadline - now ))
        sleep "$(( remaining < 10 ? remaining : 10 ))"
    done
}

_docker_responding() {
    docker info
}

_postgres_healthy() {
    [[ "$(docker inspect -f '{{.State.Health.Status}}' nocturna-postgres 2>/dev/null)" == "healthy" ]]
}

if ! _poll_until_deadline _docker_responding; then
    echo "archive-snapshot-scheduled: Docker no respondió (docker info) tras ${wait_s}s; abortando." >&2
    exit 75
fi

if ! docker compose -f "${repo_root}/docker-compose.yml" up -d postgres >>"$err_log" 2>&1; then
    echo "archive-snapshot-scheduled: 'docker compose up -d postgres' falló; ver $err_log." >&2
    exit 75
fi

if ! _poll_until_deadline _postgres_healthy; then
    echo "archive-snapshot-scheduled: nocturna-postgres no llegó a 'healthy' tras ${wait_s}s; abortando." >&2
    exit 75
fi

# `Settings` lee `.env` relativo al cwd; backend/ es el proyecto uv.
cd "${repo_root}/backend"

# `--frozen`: nada de resolver dependencias en una ejecución desatendida.
set +e
caffeinate -is uv run --frozen nocturna archive-snapshot "$@" >>"$out_log" 2>>"$err_log"
exit_code=$?
set -e

{
    echo "fin: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "código de salida: $exit_code"
} >>"$out_log"

exit "$exit_code"
