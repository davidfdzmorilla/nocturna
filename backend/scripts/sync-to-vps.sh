#!/usr/bin/env bash
# sync-to-vps.sh — Envía al VPS un volcado de la base local y lo restaura allí (T108).
#
# Corre en el Mac, tras la noche (run-night-scheduled.sh) y tras el snapshot del
# archivo (archive-snapshot-scheduled.sh). Sin LLM, sin tokens y sin ninguna
# credencial del LLM. La clave SSH es dedicada y en el VPS solo puede lanzar
# deploy/sync/restore.sh (orden forzada). Nada de este script lee contraseñas ni
# ficheros de entorno: `pg_dump` corre dentro del contenedor local con su propio
# entorno.
#
# Variables de entorno:
#     NOCTURNA_SYNC_TARGET           usuario@host del VPS. Vacío, sin definir o con
#                                    el marcador literal de la plantilla del plist
#                                    (__NOCTURNA_SYNC_TARGET__) = no configurado.
#     NOCTURNA_SYNC_KEY              Clave privada ed25519 dedicada (fichero).
#     NOCTURNA_SYNC_KNOWN_HOSTS      known_hosts con la huella del VPS (fichero).
#     NOCTURNA_SYNC_SOURCE_CONTAINER Contenedor local. Por defecto nocturna-postgres.
#     NOCTURNA_SYNC_TIMEOUT_S        Tope total de pg_dump + ssh, en segundos. Por defecto 900.
#     NOCTURNA_LOG_DIR               Directorio de logs. Por defecto $HOME/nocturna-logs.
#     NOCTURNA_PATH                  PATH de búsqueda de `docker`, `ssh`, etc. Por
#                                    defecto el PATH de producción de los demás
#                                    envoltorios.
#
# Códigos de salida:
#     0    Sincronizado.
#     1    `pg_dump` falló.
#     2    Fallo de transporte (ssh: 255 u otro código no reservado).
#     3    Tiempo agotado: NOCTURNA_SYNC_TIMEOUT_S (por defecto 900) para todo el trabajo.
#     10-17 Los de deploy/sync/restore.sh en el VPS, propagados (ver ese script).
#     75   PostgreSQL local no está sano.
#     77   Entorno inválido: clave o known_hosts ausentes o no son ficheros, destino
#          mal formado, o revisión de Alembic local mal formada.
#     78   Sincronización no configurada (sin NOCTURNA_SYNC_TARGET): no hace nada.
set -euo pipefail

export PATH="${NOCTURNA_PATH:-$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin}"

target="${NOCTURNA_SYNC_TARGET:-}"
if [[ -z "$target" || "$target" == "__NOCTURNA_SYNC_TARGET__" ]]; then
    echo "sync-to-vps: NOCTURNA_SYNC_TARGET no configurado; no se hace nada." >&2
    exit 78
fi

log_dir="${NOCTURNA_LOG_DIR-$HOME/nocturna-logs}"
if [[ -z "$log_dir" ]]; then
    echo "sync-to-vps: NOCTURNA_LOG_DIR no puede estar vacío" >&2
    exit 77
fi
mkdir -p "$log_dir"
sync_log="$log_dir/sync-$(date +%Y%m%d).log"

# Log breve y sin secretos: ni clave, ni destino, ni contenido del volcado.
log() {
    printf '%s sync-to-vps: %s\n' "$(date '+%Y-%m-%dT%H:%M:%S%z')" "$1" >>"$sync_log" 2>/dev/null || true
}

fail() {
    # $1 código, $2 mensaje
    log "$2 (código $1)"
    echo "sync-to-vps: $2" >&2
    exit "$1"
}

key="${NOCTURNA_SYNC_KEY:-}"
known_hosts="${NOCTURNA_SYNC_KNOWN_HOSTS:-}"
container="${NOCTURNA_SYNC_SOURCE_CONTAINER:-nocturna-postgres}"

if [[ ! "$target" =~ ^[A-Za-z0-9_][A-Za-z0-9._-]*@[A-Za-z0-9][A-Za-z0-9.:_-]*$ ]]; then
    fail 77 "NOCTURNA_SYNC_TARGET debe tener la forma usuario@host"
fi
if [[ -z "$key" || ! -f "$key" ]]; then
    fail 77 "NOCTURNA_SYNC_KEY no es un fichero"
fi
if [[ -z "$known_hosts" || ! -f "$known_hosts" ]]; then
    fail 77 "NOCTURNA_SYNC_KNOWN_HOSTS no es un fichero"
fi

health="$(docker inspect -f '{{.State.Health.Status}}' "$container" 2>/dev/null || true)"
if [[ "$health" != "healthy" ]]; then
    fail 75 "PostgreSQL local no está sano"
fi

tmp_dump="$(mktemp "${TMPDIR:-/tmp}/nocturna-sync.XXXXXX")"
timeout_flag="${tmp_dump}.timedout"
watchdog_pid=""
trap 'if [[ -n "$watchdog_pid" ]]; then kill -- "-$watchdog_pid" 2>/dev/null || true; fi; rm -f "$tmp_dump" "$timeout_flag"' EXIT

limit_s="${NOCTURNA_SYNC_TIMEOUT_S:-900}"
if [[ ! "$limit_s" =~ ^[0-9]+$ || "$limit_s" -eq 0 ]]; then
    fail 77 "NOCTURNA_SYNC_TIMEOUT_S debe ser un entero positivo"
fi

# Todo el trabajo (pg_dump, sha, revisión, ssh) corre en un subshell en segundo plano.
# macOS no trae `timeout`: un vigilante duerme el tope y mata el GRUPO del trabajo.
# `set -m` da a cada trabajo en segundo plano su propio grupo de procesos (pgid = pid del
# subshell), así `kill -- -pgid` alcanza también a pg_dump/ssh y a sus hijos (`sleep`, etc.)
# sin dejar huérfanos y sin tocar al envoltorio que nos lanzó.
work() {
    if ! docker exec "$container" pg_dump -U nocturna -d nocturna -Fc --no-owner --no-privileges \
        >"$tmp_dump" 2>>"$sync_log"; then
        fail 1 "pg_dump falló"
    fi

    local sha rev size ssh_code
    if command -v shasum >/dev/null 2>&1; then
        sha="$(shasum -a 256 "$tmp_dump" | cut -d' ' -f1)"
    else
        sha="$(sha256sum "$tmp_dump" | cut -d' ' -f1)"
    fi

    rev="$(docker exec "$container" psql -U nocturna -d nocturna -At \
        -c 'select version_num from alembic_version' 2>/dev/null || true)"
    if [[ ! "$rev" =~ ^[0-9a-z_]{1,64}$ ]]; then
        fail 77 "revisión de Alembic local ausente o mal formada"
    fi

    size="$(wc -c <"$tmp_dump" | tr -d ' ')"
    log "enviando volcado de ${size} bytes, revisión ${rev}"

    set +e
    ssh -F none -i "$key" -o IdentitiesOnly=yes -o BatchMode=yes \
        -o StrictHostKeyChecking=yes -o UserKnownHostsFile="$known_hosts" \
        -a -o ForwardAgent=no -o ClearAllForwardings=yes -o ConnectTimeout=30 \
        -o ServerAliveInterval=15 -o ServerAliveCountMax=4 \
        "$target" "restore $sha $rev" <"$tmp_dump" >/dev/null 2>>"$sync_log"
    ssh_code=$?
    set -e

    if [[ "$ssh_code" -eq 0 ]]; then
        log "sincronización correcta"
        exit 0
    fi
    if [[ "$ssh_code" -ge 10 && "$ssh_code" -le 17 ]]; then
        fail "$ssh_code" "el VPS rechazó la restauración"
    fi
    fail 2 "fallo de transporte (ssh $ssh_code)"
}

set -m
work &
work_pid=$!
(
    sleep "$limit_s" &
    wait $!
    : >"$timeout_flag"
    kill -TERM -- "-$work_pid" 2>/dev/null || true
) >/dev/null 2>&1 &
watchdog_pid=$!
set +m

set +e
wait "$work_pid" 2>/dev/null
work_code=$?
set -e

if [[ -e "$timeout_flag" ]]; then
    fail 3 "tiempo agotado tras ${limit_s} s"
fi
exit "$work_code"
