#!/usr/bin/env bash
# run-night-scheduled.sh — Envoltorio de lanzamiento programado de
# `nocturna run-night` para `launchd` (T60.a). Sin lógica de negocio ni de
# ventana horaria: la ventana (00:00-04:45) y el presupuesto los posee en
# exclusiva `application/budget.py`. Duplicar esa lógica aquí en bash
# crearía una segunda fuente de verdad. Este script no sabe nada de tokens,
# presupuesto ni `BudgetGuard`.
#
# Uso:
#     backend/scripts/run-night-scheduled.sh [args...]
# Los args se reenvían tal cual a `nocturna run-night` (p.ej. --since,
# --categories; ver `nocturna run-night --help`).
#
# Variables de entorno:
#     NOCTURNA_LOG_DIR   Directorio de logs. Por defecto: $HOME/nocturna-logs.
#     NOCTURNA_WAIT_S    Segundos máximos de espera a las precondiciones
#                        (Docker, contenedor de PostgreSQL). Por defecto: 300.
#     NOCTURNA_PATH      Rutas donde buscar `uv` y `claude`. Por defecto:
#                        $HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin
#                        (el PATH real de la máquina del autor). Se
#                        sobrescribe en tests y al depurar, para poder
#                        construir un entorno sin `uv`/`claude` de forma
#                        hermética sin depender del PATH real de la máquina
#                        donde corran.
#
# Códigos de salida:
#     0-8   Los de `nocturna run-night`, propagados sin modificar.
#     75    Precondiciones no listas (Docker o `nocturna-postgres` no
#           alcanzaron el estado esperado dentro de NOCTURNA_WAIT_S), o no
#           se pudo crear el centinela (permisos de NOCTURNA_LOG_DIR).
#     76    Ya hubo un lanzamiento en esta ventana (centinela de la noche).
#     77    Entorno o configuración inválidos: `uv`/`claude` no resolubles
#           en PATH, o NOCTURNA_WAIT_S/NOCTURNA_LOG_DIR con un valor
#           inválido (p.ej. NOCTURNA_WAIT_S no numérico, NOCTURNA_LOG_DIR
#           vacío). Siempre antes de tocar el centinela: no se gasta nada.
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

# `launchd` arranca los agentes con PATH=/usr/bin:/bin:/usr/sbin:/sbin, donde
# no están ni `uv` (instalado en ~/.local/bin) ni `claude` (instalado en
# /opt/homebrew/bin). Dar la ruta absoluta de `uv` no bastaría: el Agent SDK
# lanza el binario `claude` como subproceso y lo busca por nombre en PATH, así
# que `claude` tiene que estar resoluble en el PATH heredado por ese
# subproceso. Deliberadamente sin `source` de ningún .zshrc/.zprofile/perfil
# de shell: eso arrastraría todo el entorno interactivo del autor (alias,
# funciones, variables) a un proceso desatendido de madrugada.
#
# Parametrizado con NOCTURNA_PATH, igual que NOCTURNA_LOG_DIR/NOCTURNA_WAIT_S
# más abajo: sin la variable, el valor por defecto es exactamente el PATH de
# producción de arriba, sin cambio de comportamiento. Un valor mal puesto
# falla de forma segura: si no aparecen `uv`/`claude` en él, las
# comprobaciones de más abajo abortan con 77 antes de invocar `run-night`,
# sin gastar un token y sin tocar Docker.
#
# Aquí SÍ se usa `${VAR:-default}` con dos puntos, al revés que en log_dir y
# wait_s de más abajo, y es deliberado: un NOCTURNA_PATH exportado vacío cae
# al PATH de producción, que es el valor correcto. La forma sin dos puntos
# dejaría el PATH vacío y abortaría la noche con 77 -- convertiría una errata
# inocua en una noche de calibración perdida. El caso "valor inservible" ya
# lo cubren los `command -v` de más abajo; log_dir y wait_s no tenían esa red.
export PATH="${NOCTURNA_PATH:-$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin}"

# `${VAR-default}`, sin los dos puntos, a propósito -- NO `${VAR:-default}`.
# La forma con `:-` sustituye el valor por defecto tanto si la variable está
# sin definir como si está definida y vacía, así que una variable exportada
# vacía (p.ej. `export NOCTURNA_LOG_DIR=$ALGO_SIN_DEFINIR`) caería en
# silencio al valor por defecto antes de que las validaciones de abajo
# pudieran ver la cadena vacía, dejándolas sin efecto. Con `${VAR-default}`
# solo se sustituye si la variable no está definida en absoluto; una vacía
# llega intacta a `[[ -n "$log_dir" ]]`/la regex de `wait_s`, que es
# justo lo que se quiere: en un lanzador que corre a las 00:05 sin nadie
# mirando, fallar con 77 es la conducta segura, no caer a un valor por
# defecto que oculte el error de configuración.
log_dir="${NOCTURNA_LOG_DIR-$HOME/nocturna-logs}"
wait_s="${NOCTURNA_WAIT_S-300}"

# Validación de configuración, antes de tocar el centinela o Docker: un
# NOCTURNA_WAIT_S no numérico (typo, variable vacía) revienta más abajo en
# `wait_deadline=$(( ... + wait_s ))` con un error aritmético bajo `set -u`
# -- pero eso ocurriría DESPUÉS de crear el centinela, quemando una noche de
# calibración por un error tipográfico y obligando a borrar el fichero a
# mano. Se valida aquí, antes de cualquier otra cosa, con el mismo 77 que
# "entorno incompleto": la semántica es la misma ("no se puede arrancar con
# seguridad") y ocurre en el mismo punto del script, antes de todo gasto.
if [[ ! "$wait_s" =~ ^[0-9]+$ ]]; then
    echo "run-night-scheduled: NOCTURNA_WAIT_S debe ser un entero no negativo, no '$wait_s'" >&2
    exit 77
fi

# NOCTURNA_LOG_DIR vacío no es "usa el directorio actual": es casi con
# seguridad un error de configuración (una sustitución de variable vacía en
# el plist de launchd, por ejemplo). Comprobación barata, mismo motivo que
# la de arriba.
if [[ -z "$log_dir" ]]; then
    echo "run-night-scheduled: NOCTURNA_LOG_DIR no puede estar vacío" >&2
    exit 77
fi

# No se define, exporta ni borra ANTHROPIC_API_KEY: si estuviera presente en
# el entorno de `launchd` (heredado de algún plist global, por ejemplo),
# `AgentSDKProvider()` debe seguir fallando con `ApiKeyInEnvironment` -- esa
# es la guarda que exige CLAUDE.md contra usar API key en vez de la
# suscripción, y este envoltorio no la toca en ningún sentido.
missing=""
command -v uv >/dev/null 2>&1 || missing="uv"
command -v claude >/dev/null 2>&1 || {
    if [[ -n "$missing" ]]; then
        missing="$missing y claude"
    else
        missing="claude"
    fi
}
if [[ -n "$missing" ]]; then
    echo "run-night-scheduled: no se encuentra '$missing' en PATH ($PATH); abortando sin lanzar nada" >&2
    exit 77
fi

mkdir -p "$log_dir"

# Centinela de una ejecución por ventana. La ventana 00:00-04:45 no cruza
# medianoche (empieza y termina el mismo día natural), así que la fecha
# local de "ahora" identifica sin ambigüedad la noche en curso: una noche es
# exactamente una fecha local.
night_key="$(date +%Y%m%d)"
sentinel="$log_dir/.launched-$night_key"
out_log="$log_dir/night-$night_key.out.log"
err_log="$log_dir/night-$night_key.err.log"

# Garantía exacta del centinela: si se llegó a invocar `run-night`, la
# noche queda marcada -- incluida una que muera a mitad (el motivo por el
# que se crea más abajo, antes de las precondiciones, y no después). Los
# abortos de código 75 de aquí abajo (fallo al crear el propio centinela,
# Docker no responde, `docker compose up` falla, postgres no llega a
# `healthy`) son, por construcción, anteriores a la invocación de
# `run-night`: no han gastado nada, así que no consumen la noche, y cada
# uno borra el centinela antes de salir. `abort_precondition` es el único
# sitio que borra `$sentinel`, y solo se llama desde comprobaciones
# anteriores a esa invocación -- ninguna de ellas puede ejecutarse ya
# invocado `run-night` más abajo, así que no hay forma de que este borrado
# alcance a un intento que sí gastó tokens.
abort_precondition() {
    rm -f "$sentinel"
    echo "$1" >&2
    exit 75
}

# Comprobación-y-creación atómica del centinela, con `set -C` (noclobber):
# `>` falla si el fichero destino ya existe, en vez de truncarlo. Antes,
# `[[ -e "$sentinel" ]]` y la escritura eran dos pasos separados por varias
# instrucciones -- una ventana de sub-segundo en la que dos invocaciones que
# arrancan casi a la vez (un relanzamiento manual solapado, un `launchctl
# kickstart` sobre una ejecución en curso) podían pasar ambas la
# comprobación y gastar dos presupuestos completos. Con `set -C` la propia
# escritura es el test-y-set: si ya existe, falla por "ya existe"; si el
# directorio no admite escritura, falla por permisos; los dos casos se
# distinguen después, explícitamente, en vez de asumir que cualquier fallo
# de la escritura significa "ya lanzada esta noche" -- antes, un `mkdir -p`
# sobre un NOCTURNA_LOG_DIR de solo lectura hacía que el script siguiera
# adelante sin centinela y acabara en 75 por carambola, con un mensaje que
# culpaba a `docker compose` de lo que era un problema de permisos.
set -C
if { echo "inicio: $(date '+%Y-%m-%d %H:%M:%S %Z')"; echo "pid: $$"; } >"$sentinel" 2>/dev/null; then
    set +C
else
    set +C
    if [[ -e "$sentinel" ]]; then
        echo "run-night-scheduled: ya se lanzó una ejecución esta noche ($night_key); ver $sentinel." >&2
        echo "Para relanzar hay que borrar ese fichero a mano -- deliberadamente no hay --force" \
            "(docs/CALIBRACION.md, Regla de noches fallidas: una noche fallida no se relanza)." >&2
        exit 76
    fi
    echo "run-night-scheduled: no se pudo crear el centinela ($sentinel); comprueba permisos de $log_dir." >&2
    exit 75
fi

# El centinela se crea ANTES de invocar run-night (arriba): así una noche
# que muera a mitad de camino queda marcada como intentada
# (docs/CALIBRACION.md, Regla de noches fallidas: "una noche fallida no se
# relanza, se anota y se espera a mañana"); no hay bandera --force para
# forzar un segundo lanzamiento.
#
# Una señal externa durante el sondeo de precondiciones de más abajo (hasta
# NOCTURNA_WAIT_S de tiempo real: un logout, un apagado, un `launchctl
# unload`) mataría el proceso dejando el centinela puesto sin haberse
# gastado nada -- la noche se perdería sin necesidad. Este trap lo evita
# borrando el centinela y saliendo con 75; se desactiva más abajo,
# inmediatamente antes de invocar run-night, y ese orden no es negociable:
# a partir de esa línea el centinela debe sobrevivir a cualquier señal,
# porque a partir de ahí puede haber gasto real en marcha.
trap 'rm -f "$sentinel"; exit 75' INT TERM

# Presupuesto de tiempo único y compartido por las dos fases de sondeo
# (Docker, salud de nocturna-postgres): sin un `wait_deadline` común, cada
# fase con su propio `elapsed=0` sumaría hasta 2 x NOCTURNA_WAIT_S en vez de
# los NOCTURNA_WAIT_S que promete la cabecera. Si la primera fase consume
# 280 de los 300 s por defecto, a la segunda solo le quedan 20.
wait_deadline=$(( $(date +%s) + wait_s ))

# Sondea el comando dado hasta que tenga éxito o se agote `wait_deadline`.
# Comprueba el plazo ANTES de dormir (para no pagar un `sleep 10` completo
# cuando queda menos tiempo que eso -- importa con los NOCTURNA_WAIT_S de
# 1-2 s que usarán los tests) y duerme como mucho lo que quede de plazo,
# nunca más.
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

# Abortar sin gastar tokens es aceptable; gastar a medias no lo es -- por
# eso ninguna de estas tres comprobaciones invoca run-night si no se cumple
# dentro del plazo compartido.
if ! _poll_until_deadline _docker_responding; then
    abort_precondition "run-night-scheduled: Docker no respondió (docker info) tras ${wait_s}s; abortando."
fi

# `up -d` es idempotente: si postgres ya está en marcha no hace nada, y cubre
# el caso de la máquina recién reiniciada donde el contenedor todavía no
# existe. Se invoca una sola vez, no dentro de ningún bucle de espera.
if ! docker compose -f "${repo_root}/docker-compose.yml" up -d postgres >>"$err_log" 2>&1; then
    abort_precondition "run-night-scheduled: 'docker compose up -d postgres' falló; ver $err_log."
fi

if ! _poll_until_deadline _postgres_healthy; then
    abort_precondition "run-night-scheduled: nocturna-postgres no llegó a 'healthy' tras ${wait_s}s; abortando."
fi

# `Settings` (infrastructure/config.py) lee `.env` relativo al cwd, y
# backend/ es el directorio del proyecto uv: el cd es obligatorio, no
# cosmético.
cd "${repo_root}/backend"

# Se desactiva el trap de señales aquí, justo antes de invocar run-night:
# a partir de esta línea el centinela debe sobrevivir a cualquier señal,
# porque ya puede haber gasto real en marcha (ver el comentario junto al
# `trap` más arriba).
trap - INT TERM

# `caffeinate -is` impide que la máquina se suspenda durante la noche --
# pero solo con corriente alterna: en batería, `-s` no evita la suspensión.
# El runbook de operación exige el portátil enchufado durante la ventana
# nocturna; esta bandera no cubre el caso general de estar a batería, solo
# el que el runbook garantiza. Una noche cortada por suspensión es una
# noche medio gastada (tokens ya consumidos, Run sin cerrar limpiamente).
# `--frozen` impide que `uv` resuelva o actualice dependencias a las 00:05
# y cambie la versión de claude-agent-sdk a mitad de la serie de
# calibración de T60, lo que rompería la comparabilidad entre noches. Los
# logs van a ficheros separados (stdout/stderr), nunca mezclados, para que
# night-report.sh y una revisión manual puedan distinguir el informe de la
# noche del ruido de depuración.
set +e
caffeinate -is uv run --frozen nocturna run-night "$@" >>"$out_log" 2>>"$err_log"
exit_code=$?
set -e

{
    echo "fin: $(date '+%Y-%m-%d %H:%M:%S %Z')"
    echo "código de salida: $exit_code"
    echo "log de salida estándar: $out_log"
    echo "log de errores: $err_log"
} >>"$out_log"

exit "$exit_code"
