#!/usr/bin/env bash
# restore.sh — Restaura en el VPS el volcado de PostgreSQL enviado por el Mac (T108).
#
# Se ejecuta SOLO como orden forzada de SSH (ver authorized_keys.example): el
# cliente no elige el comando, solo aporta SSH_ORIGINAL_COMMAND, que aquí se
# valida con una expresión anclada y nunca se evalúa ni se pasa a un shell. El
# volcado (formato custom de pg_dump) llega por stdin.
#
# Orden de pasos: bloqueo -> validar la orden -> copiar stdin a un temporal con
# tope -> sha256 -> revisión de Alembic -> pg_restore -> reaplicar el rol de la
# API. Nada de esto lee el fichero de entorno del compose: `docker compose` lo lee por su cuenta y la
# contraseña de nocturna_ro la toma 10-api-readonly-role.sh del entorno del
# contenedor de PostgreSQL.
#
# Orden esperada: "restore <sha256 en hex, 64> <revisión de Alembic>".
#
# Variables de entorno (las fija el entorno de la orden forzada; los tests las
# sobrescriben):
#     NOCTURNA_SYNC_LOCK          Fichero de bloqueo. Por defecto
#                                 <repo>/deploy/sync/.restore.lock: un directorio
#                                 del propio usuario, no /tmp compartido (evita
#                                 enlaces simbólicos plantados por otros usuarios).
#     NOCTURNA_SYNC_MAX_BYTES     Tope del volcado. Por defecto 1073741824 (1 GiB).
#     NOCTURNA_SYNC_EXPECTED_HEAD Revisión esperada; solo para tests. Si falta se
#                                 pregunta a Alembic con el servicio `migrate`.
#     NOCTURNA_COMPOSE_PROJECT    Nombre de proyecto de compose (-p), opcional.
#     NOCTURNA_SYNC_RESTORE_TIMEOUT_S  Tope de pg_restore, en segundos. Por defecto 600.
#
# Topes de tiempo (coreutils `timeout`): un restore bloqueado no debe retener el flock.
# `alembic heads` 120 s, `pg_restore` NOCTURNA_SYNC_RESTORE_TIMEOUT_S, reaplicar el rol
# 120 s. Además pg_restore lleva PGOPTIONS='-c lock_timeout=60s' (dentro del contenedor,
# con `-e`) para no esperar indefinidamente un bloqueo de tabla. `timeout` mata el cliente
# `docker compose`; el lock_timeout y la transacción única acotan lo que quede en el servidor.
#
# Códigos de salida:
#     0   Restaurado y rol de la API reaplicado.
#     10  Orden inválida (no casa con la expresión anclada o falta).
#     11  El sha256 del volcado recibido no coincide.
#     12  La revisión de Alembic del volcado no es la `head` del código desplegado, o
#         `alembic heads` superó su tope de tiempo.
#     13  pg_restore falló o superó su tope de tiempo (transacción única: la base queda
#         como estaba).
#     14  El rol de solo lectura no se pudo reaplicar (o superó su tope de tiempo).
#     15  Ya hay otra restauración en curso.
#     16  El volcado supera el tope de tamaño (o configuración del tope inválida).
#     17  Fallo interno del VPS no previsto (bloqueo, temporal, orden, etc.) o
#         NOCTURNA_SYNC_RESTORE_TIMEOUT_S inválido.
set -euo pipefail
# Cualquier fallo no controlado (exec 9>, mktemp, head...) sale con 17, no con el 1 de `set -e`.
# Las salidas explícitas de abajo no pasan por ERR.
trap 'echo "restore: fallo interno del VPS" >&2; exit 17' ERR

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

lock_file="${NOCTURNA_SYNC_LOCK:-${script_dir}/.restore.lock}"
max_bytes="${NOCTURNA_SYNC_MAX_BYTES:-1073741824}"
restore_timeout_s="${NOCTURNA_SYNC_RESTORE_TIMEOUT_S:-600}"

log() {
    printf '%s restore: %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$1" >&2
}

# 1. Bloqueo: una sola restauración a la vez. El descriptor 9 vive hasta salir.
exec 9>"$lock_file"
if ! flock -n 9; then
    log "otra restauración en curso"
    exit 15
fi

# 2. Orden. `=~` no evalúa nada; los campos salen de BASH_REMATCH.
command_re='^restore ([0-9a-f]{64}) ([0-9a-z_]{1,64})$'
if [[ ! "${SSH_ORIGINAL_COMMAND:-}" =~ $command_re ]]; then
    log "orden inválida"
    exit 10
fi
expected_sha="${BASH_REMATCH[1]}"
revision="${BASH_REMATCH[2]}"

if [[ ! "$max_bytes" =~ ^[0-9]+$ ]]; then
    log "NOCTURNA_SYNC_MAX_BYTES inválido"
    exit 16
fi
if [[ ! "$restore_timeout_s" =~ ^[0-9]+$ || "$restore_timeout_s" -eq 0 ]]; then
    log "NOCTURNA_SYNC_RESTORE_TIMEOUT_S inválido"
    exit 17
fi

# 3. Copia con tope: se lee un byte de más para distinguir "justo en el tope".
tmp_dump="$(mktemp "${TMPDIR:-/tmp}/nocturna-restore.XXXXXX")"
trap 'rm -f "$tmp_dump"' EXIT
head -c "$((max_bytes + 1))" >"$tmp_dump"
size="$(wc -c <"$tmp_dump" | tr -d ' ')"
if (( size > max_bytes )); then
    log "volcado por encima del tope de ${max_bytes} bytes"
    exit 16
fi

# 4. sha256.
if command -v sha256sum >/dev/null 2>&1; then
    actual_sha="$(sha256sum "$tmp_dump" | cut -d' ' -f1)"
else
    actual_sha="$(shasum -a 256 "$tmp_dump" | cut -d' ' -f1)"
fi
if [[ "$actual_sha" != "$expected_sha" ]]; then
    log "sha256 no coincide"
    exit 11
fi

compose=(docker compose -f "${repo_root}/deploy/compose.yml")
if [[ -n "${NOCTURNA_COMPOSE_PROJECT:-}" ]]; then
    compose+=(-p "$NOCTURNA_COMPOSE_PROJECT")
fi

# 5. Revisión: el esquema del volcado debe ser el del código desplegado.
if [[ -n "${NOCTURNA_SYNC_EXPECTED_HEAD:-}" ]]; then
    head_revision="$NOCTURNA_SYNC_EXPECTED_HEAD"
else
    heads_rc=0
    heads_output="$(timeout 120 "${compose[@]}" --profile ops run --rm -T --no-deps migrate alembic heads </dev/null 2>/dev/null)" || heads_rc=$?
    if [[ "$heads_rc" -eq 124 ]]; then
        log "alembic heads superó el tope de tiempo"
        exit 12
    fi
    head_revision="$(printf '%s\n' "$heads_output" | sed -nE 's/^([0-9a-z_]+) \(head\)$/\1/p')"
    # Más de una cabeza o ninguna: no se restaura.
    if [[ "$(printf '%s\n' "$head_revision" | grep -c .)" -ne 1 ]]; then
        log "no se pudo determinar una única revisión head de Alembic"
        exit 12
    fi
fi
if [[ "$head_revision" != "$revision" ]]; then
    log "revisión del volcado ($revision) distinta de la head desplegada ($head_revision)"
    exit 12
fi

# 6. Restauración en una transacción: si algo falla, la base queda como estaba.
pg_rc=0
PGOPTIONS_VALUE='-c lock_timeout=60s'
timeout "$restore_timeout_s" "${compose[@]}" exec -T -e "PGOPTIONS=${PGOPTIONS_VALUE}" postgres pg_restore -U nocturna -d nocturna \
    --clean --if-exists --no-owner --no-privileges --single-transaction --exit-on-error \
    <"$tmp_dump" >/dev/null || pg_rc=$?
if [[ "$pg_rc" -eq 124 ]]; then
    log "pg_restore superó el tope de ${restore_timeout_s} s"
    exit 13
elif [[ "$pg_rc" -ne 0 ]]; then
    log "pg_restore falló"
    exit 13
fi

# 7. --no-privileges quita los GRANT: se reaplica el rol de solo lectura de la API.
role_rc=0
timeout 120 "${compose[@]}" exec -T postgres sh /docker-entrypoint-initdb.d/10-api-readonly-role.sh \
    </dev/null >/dev/null || role_rc=$?
if [[ "$role_rc" -ne 0 ]]; then
    if [[ "$role_rc" -eq 124 ]]; then
        log "reaplicar el rol superó el tope de tiempo"
    else
        log "no se pudo reaplicar el rol de solo lectura"
    fi
    exit 14
fi

log "restauración correcta (revisión $revision)"
exit 0
