"""Binarios falsos de T108 (sincronizacion Mac -> VPS): `ssh`, `docker`, `flock`, `uv`.

Cada falso deja un rastro en ficheros que fija el test por variable de entorno; ninguno toca
red, Docker ni Claude. `ORDER_LOG` recibe una linea por llamada relevante, de todos los falsos
a la vez: asi se comprueba el orden entre procesos distintos.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SYSTEM_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

# ssh: un argumento por linea en SSH_ARGV_FILE (cada llamada acaba en `--`); stdin a
# SSH_STDIN_FILE.
FAKE_SSH = """#!/usr/bin/env bash
printf 'ssh\\n' >> "${ORDER_LOG:-/dev/null}"
if [ -n "${SSH_ARGV_FILE:-}" ]; then
    for a in "$@"; do printf '%s\\n' "$a" >> "$SSH_ARGV_FILE"; done
    printf -- '--\\n' >> "$SSH_ARGV_FILE"
fi
if [ -n "${SSH_STDIN_FILE:-}" ]; then
    cat > "$SSH_STDIN_FILE"
else
    cat >/dev/null
fi
if [ -n "${SSH_HANG_S:-}" ]; then
    sleep "$SSH_HANG_S"
fi
exit "${SSH_EXIT:-0}"
"""

# docker del lado del Mac (sync-to-vps.sh): inspect / exec pg_dump / exec psql.
FAKE_DOCKER_MAC = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "${DOCKER_CALL_LOG_FILE:-/dev/null}"
case "${1:-}" in
    info) exit "${DOCKER_INFO_EXIT:-0}" ;;
    compose) exit "${DOCKER_COMPOSE_EXIT:-0}" ;;
    inspect) printf '%s\\n' "${DOCKER_HEALTH_STATUS-healthy}"; exit 0 ;;
    exec)
        case "$*" in
            *pg_dump*)
                [ -n "${DUMP_STDERR:-}" ] && printf '%s\\n' "$DUMP_STDERR" >&2
                printf '%s' "${DUMP_CONTENT:-DUMP-BYTES}"
                exit "${DUMP_EXIT:-0}" ;;
            *psql*)
                printf '%s\\n' "${ALEMBIC_REV-abc123def456}"
                exit "${PSQL_EXIT:-0}" ;;
        esac
        exit 0 ;;
esac
exit 0
"""

# uv de los envoltorios: orden global y codigos por subcomando.
FAKE_UV_ORDER = """#!/usr/bin/env bash
case "$*" in
    *morning-report*) printf 'morning-report\\n' >> "${ORDER_LOG:-/dev/null}"; exit 0 ;;
    *evaluate-tensions*) printf 'evaluate-tensions\\n' >> "${ORDER_LOG:-/dev/null}"
        exit "${UV_EVALUATE_EXIT_CODE:-0}" ;;
    *run-night*) printf 'run-night\\n' >> "${ORDER_LOG:-/dev/null}"
        exit "${UV_EXIT_CODE:-0}" ;;
    *archive-snapshot*) printf 'archive-snapshot\\n' >> "${ORDER_LOG:-/dev/null}"
        exit "${UV_EXIT_CODE:-0}" ;;
esac
exit 0
"""

FAKE_CAFFEINATE = """#!/usr/bin/env bash
while [ "$#" -gt 0 ]; do
    case "$1" in
        -*) shift ;;
        *) break ;;
    esac
done
"$@"
exit $?
"""

FAKE_CLAUDE = """#!/usr/bin/env bash
exit 0
"""

# docker del lado del VPS (restore.sh): registra argv y stdin de cada llamada.
# PG_RESTORE_EXIT / ROLE_EXIT mandan el codigo de cada fase.
FAKE_DOCKER_VPS = """#!/usr/bin/env bash
printf 'docker %s\\n' "$*" >> "${ORDER_LOG:-/dev/null}"
printf '%s\\n' "$*" >> "${DOCKER_CALL_LOG_FILE:-/dev/null}"
# `docker compose exec -e K=V`: el valor llega al proceso de dentro; se deja constancia.
prev=""
for a in "$@"; do
    if [ "$prev" = "-e" ]; then
        case "$*" in
            *pg_restore*) printf '%s\\n' "$a" >> "${DOCKER_ENV_SEEN_FILE:-/dev/null}" ;;
        esac
    fi
    prev="$a"
done
case "$*" in
    *pg_restore*)
        if [ -n "${PG_RESTORE_STDIN_FILE:-}" ]; then
            cat > "$PG_RESTORE_STDIN_FILE"
        else
            cat >/dev/null
        fi
        exit "${PG_RESTORE_EXIT:-0}" ;;
    *10-api-readonly-role.sh*) exit "${ROLE_EXIT:-0}" ;;
    *alembic*heads*) printf "%b" "${HEADS_OUTPUT:-}"; exit "${HEADS_EXIT:-0}" ;;
esac
exit 0
"""

# timeout falso (coreutils no esta en este Mac): registra "<segundos> <orden>" y ejecuta la
# orden, salvo que TIMEOUT_FIRES sea una subcadena de la orden: entonces sale con 124.
FAKE_TIMEOUT = """#!/usr/bin/env bash
secs="$1"; shift
printf 'timeout %s %s\\n' "$secs" "$*" >> "${TIMEOUT_LOG_FILE:-/dev/null}"
if [ -n "${TIMEOUT_FIRES:-}" ]; then
    case "$*" in *"$TIMEOUT_FIRES"*) cat >/dev/null 2>&1 </dev/null; exit 124 ;; esac
fi
exec "$@"
"""

# flock falso: `flock -n 9` sale con FLOCK_EXIT (por defecto 0).
FAKE_FLOCK = """#!/usr/bin/env bash
printf 'flock %s\\n' "$*" >> "${ORDER_LOG:-/dev/null}"
exit "${FLOCK_EXIT:-0}"
"""


def install_all(bin_dir: Path, **scripts: str) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    for name, script in scripts.items():
        path = bin_dir / name
        path.write_text(script)
        path.chmod(0o755)


def run(cmd, *, env, cwd, stdin: bytes | None = None, timeout: float = 30):
    return subprocess.run(
        cmd, env=env, cwd=cwd, input=stdin, capture_output=True, timeout=timeout, check=False
    )
