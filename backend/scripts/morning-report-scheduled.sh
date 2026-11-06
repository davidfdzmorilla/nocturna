#!/usr/bin/env bash
# morning-report-scheduled.sh — Envoltorio de lanzamiento programado de
# `nocturna morning-report` para `launchd` (T105, ADR 0044 C). Red de seguridad
# de las 07:30: genera el informe de la noche aunque `run-night-scheduled.sh`
# no llegara a lanzarse (Mac apagado, dormido o sin sesión). Sin LLM ni tokens,
# sin centinela y sin `claude`.
#
# Solo espera a que `nocturna-postgres` esté `healthy`; nunca ejecuta
# `docker compose up`: levantar la base no es cosa de un informe.
#
# Uso:
#     backend/scripts/morning-report-scheduled.sh [args...]
# Los args se reenvían a `nocturna morning-report` (--night, --no-notify).
#
# Variables de entorno:
#     NOCTURNA_LOG_DIR   Directorio de logs. Por defecto: $HOME/nocturna-logs.
#     NOCTURNA_WAIT_S    Segundos máximos de espera a `nocturna-postgres` healthy.
#                        Por defecto: 300. Si no llega a tiempo, el informe se
#                        genera igual (con la base marcada como inaccesible).
#     NOCTURNA_PATH      Rutas donde buscar `uv`. Por defecto:
#                        $HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
#
# Códigos de salida:
#     0, 1  Los de `nocturna morning-report` (0 informe escrito, 1 no se pudo).
#     77    Entorno inválido: `uv` no resoluble (se avisa con `osascript` directo),
#           o NOCTURNA_WAIT_S / NOCTURNA_LOG_DIR con un valor inválido.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

export PATH="${NOCTURNA_PATH:-$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin}"

# `${VAR-default}` sin dos puntos, a propósito: una variable exportada vacía
# debe llegar intacta a las validaciones y fallar con 77.
log_dir="${NOCTURNA_LOG_DIR-$HOME/nocturna-logs}"
wait_s="${NOCTURNA_WAIT_S-300}"

if [[ ! "$wait_s" =~ ^[0-9]+$ ]]; then
    echo "morning-report-scheduled: NOCTURNA_WAIT_S debe ser un entero no negativo, no '$wait_s'" >&2
    exit 77
fi
if [[ -z "$log_dir" ]]; then
    echo "morning-report-scheduled: NOCTURNA_LOG_DIR no puede estar vacío" >&2
    exit 77
fi

if ! command -v uv >/dev/null 2>&1; then
    echo "morning-report-scheduled: no se encuentra 'uv' en PATH ($PATH); abortando" >&2
    # Texto fijo: sin `uv` no hay informe que mostrar, pero la ausencia debe verse.
    osascript -e 'display notification "Nocturna: no se pudo generar el informe (uv no encontrado)" with title "Nocturna"' \
        >/dev/null 2>&1 || true
    exit 77
fi

mkdir -p "$log_dir"
day_key="$(date +%Y%m%d)"
out_log="$log_dir/morning-$day_key.out.log"
err_log="$log_dir/morning-$day_key.err.log"

wait_deadline=$(( $(date +%s) + wait_s ))

_postgres_healthy() {
    [[ "$(docker inspect -f '{{.State.Health.Status}}' nocturna-postgres 2>/dev/null)" == "healthy" ]]
}

# Espera acotada. Si la base no está lista, sigue: el informe sin base ("base de
# datos inaccesible") es justo lo que hay que contar esa mañana.
while ! _postgres_healthy >/dev/null 2>&1; do
    now="$(date +%s)"
    if (( now >= wait_deadline )); then
        echo "morning-report-scheduled: nocturna-postgres no llegó a 'healthy' tras ${wait_s}s; se genera el informe sin base." >>"$err_log"
        break
    fi
    remaining=$(( wait_deadline - now ))
    sleep "$(( remaining < 10 ? remaining : 10 ))"
done

# `Settings` lee `.env` relativo al cwd; backend/ es el proyecto uv.
cd "${repo_root}/backend"

set +e
uv run --frozen nocturna morning-report --trigger scheduled "$@" >>"$out_log" 2>>"$err_log"
exit_code=$?
set -e

exit "$exit_code"
