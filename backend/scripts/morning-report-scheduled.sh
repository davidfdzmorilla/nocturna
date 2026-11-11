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

# T110.a: vigilante de la comprobación pública. `getaddrinfo` y otras esperas no las
# acota el cliente HTTP; si el proceso se cuelga, se perdería el informe. macOS no trae
# `timeout`: mismo patrón que sync-to-vps.sh (grupo de procesos propio y un vigilante
# que lo mata). Duplicado a propósito en run-night-scheduled.sh. Tope:
# NOCTURNA_PROBE_TIMEOUT_S (por defecto 90). Devuelve el código del probe, o 3 si venció.
probe_flag=""
probe_watchdog_pid=""
probe_pid_global=""
probe_cleanup() {
    [[ -n "$probe_flag" ]] && rm -f "$probe_flag"
    [[ -n "$probe_pid_global" ]] && kill -KILL -- "-$probe_pid_global" 2>/dev/null
    [[ -n "$probe_watchdog_pid" ]] && kill -- "-$probe_watchdog_pid" 2>/dev/null
    return 0
}
run_probe_guarded() {
    local trigger="$1" limit_s="${NOCTURNA_PROBE_TIMEOUT_S:-90}"
    local flag probe_pid watchdog_pid code
    if [[ ! "$limit_s" =~ ^[0-9]+$ || "$limit_s" -eq 0 ]]; then
        limit_s=90
    fi
    flag="$(mktemp "${TMPDIR:-/tmp}/nocturna-probe.XXXXXX")"
    rm -f "$flag"
    # Si el envoltorio muere (EXIT, o INT/TERM que de otro modo no ejecutan el trap EXIT),
    # no queda el fichero de marca ni el vigilante. Trap EXIT propio de esta función: el
    # envoltorio no usa ninguno, y INT/TERM se restauran a "por defecto" al volver (en este
    # punto de ambos scripts no hay otros traps de INT/TERM activos).
    probe_flag="$flag"
    probe_watchdog_pid=""
    trap 'probe_cleanup' EXIT
    trap 'probe_cleanup; exit 143' INT TERM
    set -m
    uv run --frozen nocturna phase3-probe --trigger "$trigger" >>"$out_log" 2>>"$err_log" &
    probe_pid=$!
    probe_pid_global="$probe_pid"
    (
        sleep "$limit_s" &
        wait $!
        : >"$flag"
        kill -TERM -- "-$probe_pid" 2>/dev/null || true
        sleep 5 &
        wait $!
        kill -KILL -- "-$probe_pid" 2>/dev/null || true
    ) >/dev/null 2>&1 &
    watchdog_pid=$!
    probe_watchdog_pid="$watchdog_pid"
    set +m
    code=0
    wait "$probe_pid" 2>/dev/null || code=$?
    if [[ -e "$flag" ]]; then
        # Venció el tope: el vigilante ya envió TERM. Se le deja terminar (5 s) para que
        # envíe el KILL a quien ignore TERM; `wait` del probe puede volver antes si lo que
        # murió fue `caffeinate` y no el comando.
        wait "$watchdog_pid" 2>/dev/null || true
    else
        kill -- "-$watchdog_pid" 2>/dev/null || true
        # Recoge al vigilante: evita el aviso "Terminated" del shell en el log de launchd.
        wait "$watchdog_pid" 2>/dev/null || true
    fi
    probe_watchdog_pid=""
    if [[ -e "$flag" ]]; then
        rm -f "$flag"
        echo "morning-report-scheduled: phase3-probe superó ${limit_s}s y se cortó (código 3)." >>"$err_log"
        code=3
    fi
    probe_flag=""
    probe_pid_global=""
    trap - EXIT INT TERM
    return "$code"
}

# T110.a: comprobación de la web pública antes del informe (cierre de fase 3,
# ADR 0044). Sin base, sin LLM; su código (78 = URL no configurada) nunca
# cambia el del informe. Este script no escribe .status: se ignora el código.
set +e
run_probe_guarded scheduled
set -e

set +e
uv run --frozen nocturna morning-report --trigger scheduled "$@" >>"$out_log" 2>>"$err_log"
exit_code=$?
set -e

exit "$exit_code"
