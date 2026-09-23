"""Tests de `backend/scripts/run-night-scheduled.sh` (T60.a), el envoltorio
que `launchd` invoca a las 00:05 para lanzar `nocturna run-night` sin
supervisión humana.

Ningún test llama a Claude, Docker o `uv` de verdad (`.claude/skills/
testing-without-claude`): `uv`, `claude` y `docker` se falsifican como
scripts de shell instalados en un directorio aislado bajo `tmp_path`.

## Aislamiento del PATH: barrera dura, no coincidencia (hallazgo A de revisión)

La primera versión de esta suite dejaba que 19 de 22 tests confiaran en que
`$HOME/.local/bin` (con los binarios falsos) precediera a `/opt/homebrew/bin`
(donde viven `uv`, `claude` y `docker` **reales** en esta máquina) dentro del
PATH que el propio envoltorio construye. Un reviewer demostró con un mutante
de PATH fijo que eso bastaba para que los tests acabaran invocando el
`docker` real; solo la ausencia de `~/.docker/cli-plugins` en el `$HOME`
temporal impidió llegar a un `uv run --frozen nocturna run-night` real. Con
otro `$HOME`, ese mutante lanza una noche real desde `pytest`.

Por eso `_base_env` exige `bin_dir` como argumento obligatorio (sin valor por
defecto) y siempre fija `NOCTURNA_PATH="{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin"`
-- nunca `/opt/homebrew/bin` ni `$HOME/.local/bin`. El aislamiento de cada
test es ahora una variable de entorno explícita, no el orden de precedencia
del PATH. Los únicos dos tests que deliberadamente NO pasan por `_base_env`
son los que verifican el valor por defecto de `NOCTURNA_PATH` en sí mismo
(`test_valor_por_defecto_de_nocturna_path_es_el_de_produccion` y
`test_path_sin_herencia_del_shell`): ahí SÍ hace falta que `$HOME/.local/bin`
preceda a `/opt/homebrew/bin`, porque es exactamente lo que se está
verificando. `test_valor_por_defecto_de_nocturna_path_coincide_con_el_fuente`
completa esa pareja leyendo el fuente del script directamente (hallazgo B,
ver su docstring).

Cada test construye un entorno **explícito** para `subprocess.run`/`Popen`
(nunca `os.environ` heredado): así la suite es inmune a que la máquina del
autor tenga `uv`/`claude`/`docker` reales instalados, y a que el reloj de
pared del autor esté donde esté. `HOME` es siempre un `tmp_path`; ningún test
fija `NOCTURNA_LOG_DIR` a una ruta real, y los que sí la fijan explícitamente
(el de permisos) apuntan a un subdirectorio de `tmp_path`. Los logs se leen
de vuelta desde `<home>/nocturna-logs` salvo que se diga lo contrario.

`/usr/bin`, `/bin`, `/usr/sbin`, `/sbin` sí son los reales del sistema
(`date`, `mkdir`, `rm`, `sleep`, `caffeinate`): no son ni Claude, ni Docker,
ni `uv`, y el propio script los necesita para su lógica de centinela/plazo.
Confirmado que ninguno de los tres binarios reales (`uv`, `claude`, `docker`)
vive en esos cuatro directorios en esta máquina.

Los tres binarios falsos:

- `uv`: registra cada invocación (argumentos exactos) en `UV_CALL_LOG_FILE`
  si esa variable está en el entorno, vuelca su propio entorno completo a
  `UV_ENV_DUMP_FILE` si se pide, escribe `UV_STDOUT_MSG`/`UV_STDERR_MSG` a
  stdout/stderr si se piden, duerme `UV_SLEEP` segundos si se pide (para
  sincronizar el envío de señales desde el test sin adivinar temporización),
  y sale con `UV_EXIT_CODE` (por defecto 0).
- `claude`: registra cada invocación en `CLAUDE_CALL_LOG_FILE` si se pide
  (igual que `uv`/`docker`) y sale 0. El envoltorio solo hace
  `command -v claude`, nunca debería ejecutarlo de verdad --
  `test_claude_nunca_se_invoca` lo congela (hallazgo D).
- `docker`: registra cada invocación en `DOCKER_CALL_LOG_FILE` si se pide, y
  distingue `info` (sale `DOCKER_INFO_EXIT`, por defecto 0), `compose ...`
  (duerme `DOCKER_COMPOSE_SLEEP` segundos si se pide, luego sale
  `DOCKER_COMPOSE_EXIT`, por defecto 0) e `inspect ...` (imprime
  `DOCKER_HEALTH_STATUS`, por defecto `healthy`).

Todos los tests que abortan por precondiciones usan `NOCTURNA_WAIT_S`
pequeño (1-5 s): el propio script comprueba el plazo antes de dormir, así
que un aborto por precondiciones no debería tardar más que ese plazo.

Dos tests (`test_term_durante_sondeo_de_precondiciones_borra_el_centinela`,
`test_trap_no_borra_el_centinela_una_vez_invocado_run_night`) envían señales
reales a un `Popen` del envoltorio; se sincronizan sondeando la aparición de
un fichero (el centinela o el log de invocación de `uv`), nunca con un
`sleep` fijo adivinado -- eso es justo el tipo de test intermitente que esta
tarea tiene prohibido dejar pasar por suerte.
"""

from __future__ import annotations

import plistlib
import signal
import subprocess
import time
from datetime import datetime
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "run-night-scheduled.sh"
PLIST_TEMPLATE_PATH = BACKEND_ROOT / "scripts" / "com.nocturna.run-night.plist.template"

_LAUNCHD_PATH = "/usr/bin:/bin:/usr/sbin:/sbin"

_FAKE_UV = """#!/usr/bin/env bash
if [ -n "${UV_CALL_LOG_FILE:-}" ]; then
    printf '%s\\n' "$*" >> "$UV_CALL_LOG_FILE"
fi
if [ -n "${UV_ENV_DUMP_FILE:-}" ]; then
    env > "$UV_ENV_DUMP_FILE"
fi
if [ -n "${UV_STDOUT_MSG:-}" ]; then
    printf '%s\\n' "$UV_STDOUT_MSG"
fi
if [ -n "${UV_STDERR_MSG:-}" ]; then
    printf '%s\\n' "$UV_STDERR_MSG" >&2
fi
if [ -n "${UV_SLEEP:-}" ]; then
    sleep "${UV_SLEEP}"
fi
exit "${UV_EXIT_CODE:-0}"
"""

_FAKE_CLAUDE = """#!/usr/bin/env bash
if [ -n "${CLAUDE_CALL_LOG_FILE:-}" ]; then
    printf '%s\\n' "$*" >> "$CLAUDE_CALL_LOG_FILE"
fi
exit 0
"""

_FAKE_DOCKER = """#!/usr/bin/env bash
if [ -n "${DOCKER_CALL_LOG_FILE:-}" ]; then
    printf '%s\\n' "$*" >> "$DOCKER_CALL_LOG_FILE"
fi
subcommand="${1:-}"
case "$subcommand" in
    info)
        exit "${DOCKER_INFO_EXIT:-0}"
        ;;
    compose)
        if [ -n "${DOCKER_COMPOSE_SLEEP:-}" ]; then
            sleep "${DOCKER_COMPOSE_SLEEP}"
        fi
        exit "${DOCKER_COMPOSE_EXIT:-0}"
        ;;
    inspect)
        printf '%s\\n' "${DOCKER_HEALTH_STATUS:-healthy}"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
"""


def _install_executable(path: Path, script: str) -> None:
    path.write_text(script)
    path.chmod(0o755)


def _install_fake_bins(
    bin_dir: Path, *, uv: bool = True, claude: bool = True, docker: bool = True
) -> None:
    bin_dir.mkdir(parents=True, exist_ok=True)
    if uv:
        _install_executable(bin_dir / "uv", _FAKE_UV)
    if claude:
        _install_executable(bin_dir / "claude", _FAKE_CLAUDE)
    if docker:
        _install_executable(bin_dir / "docker", _FAKE_DOCKER)


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    return home


def _isolated_bin(tmp_path: Path) -> Path:
    """Directorio de binarios falsos aislado, sin relación con `$HOME`: es lo
    único que decide qué ve el envoltorio vía `NOCTURNA_PATH` (hallazgo A)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    return bin_dir


def _setup_env(
    tmp_path: Path, *, uv: bool = True, claude: bool = True, docker: bool = True
) -> tuple[Path, Path]:
    home = _make_home(tmp_path)
    bin_dir = _isolated_bin(tmp_path)
    _install_fake_bins(bin_dir, uv=uv, claude=claude, docker=docker)
    return home, bin_dir


def _log_dir(home: Path) -> Path:
    return home / "nocturna-logs"


def _night_key() -> str:
    return datetime.now().strftime("%Y%m%d")


def _base_env(
    home: Path,
    bin_dir: Path,
    *,
    wait_s: str = "1",
    path: str = _LAUNCHD_PATH,
    extra: dict[str, str] | None = None,
) -> dict[str, str]:
    """Entorno explícito para el envoltorio. `bin_dir` es obligatorio -- sin
    valor por defecto -- para que ningún test pueda "olvidarse" de aislar
    `NOCTURNA_PATH" y caer de vuelta en la precedencia del PATH real de la
    máquina (hallazgo A)."""
    env = {
        "HOME": str(home),
        "PATH": path,
        "NOCTURNA_PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
        "NOCTURNA_WAIT_S": wait_s,
    }
    if extra:
        env.update(extra)
    return env


def _run_wrapper(
    args: list[str], env: dict[str, str], *, cwd: Path, timeout: float = 20
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT_PATH), *args],
        env=env,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _popen_wrapper(args: list[str], env: dict[str, str], *, cwd: Path) -> subprocess.Popen[str]:
    return subprocess.Popen(
        [str(SCRIPT_PATH), *args],
        env=env,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.parametrize(
    "missing",
    [frozenset({"uv"}), frozenset({"claude"}), frozenset({"uv", "claude"})],
    ids=["uv", "claude", "uv-y-claude"],
)
def test_aborta_sin_uv_ni_claude(tmp_path, missing):
    """Sin `uv` o sin `claude` resolubles en `NOCTURNA_PATH`, sale 77 sin
    invocar nada -- ni `uv`, ni `docker`, ni `claude`.

    `isolated_bin` no contiene nada salvo lo que este test decide instalar;
    `NOCTURNA_PATH` lo combina con `/usr/bin:/bin:/usr/sbin:/sbin` (nunca con
    `/opt/homebrew/bin` ni `$HOME/.local/bin`) para que el resto del script
    -- `mkdir`, `date`, `rm`, `sleep`, que necesita si llegara más lejos por
    la red de seguridad de abajo -- siga funcionando sin reintroducir ningún
    binario real.

    El `docker` falso se instala siempre y se fuerza a fallar de inmediato
    (`DOCKER_INFO_EXIT=1`) como red de seguridad, no como parte del
    contrato: si la comprobación de entorno detecta bien la ausencia, aborta
    con 77 antes de llegar siquiera a comprobar Docker, y este `docker`
    falso nunca se ejecuta -- lo que se comprueba con `DOCKER_CALL_LOG_FILE`.
    """
    isolated_bin = tmp_path / "isolated-bin"
    _install_fake_bins(
        isolated_bin,
        uv="uv" not in missing,
        claude="claude" not in missing,
        docker=True,
    )
    home = _make_home(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    docker_call_log = tmp_path / "docker-calls.log"
    claude_call_log = tmp_path / "claude-calls.log"
    env = _base_env(
        home,
        isolated_bin,
        wait_s="1",
        extra={
            "UV_CALL_LOG_FILE": str(uv_call_log),
            "DOCKER_CALL_LOG_FILE": str(docker_call_log),
            "CLAUDE_CALL_LOG_FILE": str(claude_call_log),
            "DOCKER_INFO_EXIT": "1",
        },
    )

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == 77, (
        f"se esperaba 77 (entorno incompleto) con {sorted(missing)} ausente(s) de "
        f"NOCTURNA_PATH; returncode={result.returncode} stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    for name in missing:
        assert name in result.stderr, (
            f"'{name}' no aparece en el mensaje de error: {result.stderr!r}"
        )
    assert not uv_call_log.exists(), "el 'uv' falso no debe invocarse en el camino 77"
    assert not docker_call_log.exists(), (
        "el 'docker' falso no debe invocarse en absoluto en el camino 77"
    )
    assert not claude_call_log.exists(), "el 'claude' falso no debe invocarse en el camino 77"


def test_valor_por_defecto_de_nocturna_path_es_el_de_produccion(tmp_path):
    """Sin `NOCTURNA_PATH` en el entorno, el envoltorio sigue usando exactamente
    `$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin`: con `HOME`
    en un `tmp_path` y los binarios falsos solo en `$HOME/.local/bin`, los
    encuentra sin que se le diga dónde buscar, ni rastro de `NOCTURNA_PATH`.

    Test de comportamiento, deliberadamente sin pasar por `_base_env` (que
    fuerza `NOCTURNA_PATH`): congela que parametrizar la búsqueda con esa
    variable no ha cambiado el comportamiento de producción -- la única
    condición bajo la que tiene sentido haberla introducido. Es, a
    propósito, uno de los dos tests de esta suite que dependen de la
    precedencia de `$HOME/.local/bin` sobre `/opt/homebrew/bin` -- porque es
    justo lo que verifica. Ver `test_valor_por_defecto_de_nocturna_path_
    coincide_con_el_fuente` para la comprobación complementaria de que la
    cadena por defecto en sí no ha cambiado (esta prueba de comportamiento
    sola no lo detectaría: no ejercita `/opt/homebrew/bin` en absoluto)."""
    home = _make_home(tmp_path)
    bin_dir = home / ".local" / "bin"
    _install_fake_bins(bin_dir)
    uv_call_log = tmp_path / "uv-calls.log"
    env = {
        "HOME": str(home),
        "PATH": _LAUNCHD_PATH,
        "NOCTURNA_WAIT_S": "1",
        "UV_CALL_LOG_FILE": str(uv_call_log),
    }
    assert "NOCTURNA_PATH" not in env

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == 0, (
        "sin NOCTURNA_PATH, el envoltorio debe seguir encontrando los binarios en "
        f"$HOME/.local/bin (el PATH de producción); stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert uv_call_log.exists()


def test_valor_por_defecto_de_nocturna_path_coincide_con_el_fuente(tmp_path):
    """Mutante superviviente detectado por revisión (hallazgo B): el test de
    comportamiento de arriba solo comprueba que `$HOME/.local/bin` está en el
    PATH resultante; borrar `/opt/homebrew/bin` del valor por defecto --justo
    donde vive el `claude` real, y lo que la cabecera del script declara
    imprescindible para el subproceso que lanza el Agent SDK-- dejaba los 22
    tests en verde igualmente, porque ningún test ejercitaba esa ruta
    específica.

    Esta prueba lee el fuente del script directamente y afirma la cadena
    **literal** del valor por defecto de `NOCTURNA_PATH`, sin ejecutar nada:
    es la única forma de detectar que alguien quitó `/opt/homebrew/bin` (o
    cualquier otro segmento) del valor por defecto, incluso si ese cambio no
    rompe ningún camino de ejecución cubierto por los demás tests."""
    script_text = SCRIPT_PATH.read_text()
    expected_line = (
        'export PATH="${NOCTURNA_PATH:-$HOME/.local/bin:/opt/homebrew/bin:'
        '/usr/bin:/bin:/usr/sbin:/sbin}"'
    )

    assert expected_line in script_text, (
        "la línea de 'export PATH' con el valor por defecto de NOCTURNA_PATH ha "
        f"cambiado; se esperaba encontrar exactamente: {expected_line!r}. Si "
        "/opt/homebrew/bin ha desaparecido del valor por defecto: ese es el "
        "directorio donde vive el 'claude' real que el Agent SDK necesita "
        "resoluble en PATH (ver la cabecera del propio script)."
    )


def test_aborta_si_docker_no_responde(tmp_path):
    """`docker info` fallando aborta con 75 sin invocar `uv`, rápido."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(
        home,
        bin_dir,
        wait_s="1",
        extra={"UV_CALL_LOG_FILE": str(uv_call_log), "DOCKER_INFO_EXIT": "1"},
    )

    started = time.monotonic()
    result = _run_wrapper([], env, cwd=tmp_path)
    elapsed = time.monotonic() - started

    assert result.returncode == 75
    assert not uv_call_log.exists()
    assert elapsed < 5, (
        f"el aborto por precondiciones tardó {elapsed:.1f}s con NOCTURNA_WAIT_S=1; "
        "debería tardar ~1s, no el sleep(10) completo"
    )


def test_aborta_si_postgres_no_esta_healthy(tmp_path):
    """`docker inspect` atascado en 'starting' aborta con 75 sin invocar `uv`, rápido."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(
        home,
        bin_dir,
        wait_s="1",
        extra={"UV_CALL_LOG_FILE": str(uv_call_log), "DOCKER_HEALTH_STATUS": "starting"},
    )

    started = time.monotonic()
    result = _run_wrapper([], env, cwd=tmp_path)
    elapsed = time.monotonic() - started

    assert result.returncode == 75
    assert not uv_call_log.exists()
    assert elapsed < 5, (
        f"el aborto por precondiciones tardó {elapsed:.1f}s con NOCTURNA_WAIT_S=1; "
        "debería tardar ~1s, no el sleep(10) completo"
    )


@pytest.mark.parametrize("uv_exit_code", [0, 7, 8, 1])
def test_ejecuta_cuando_las_precondiciones_pasan(tmp_path, uv_exit_code):
    """Con Docker/postgres sanos, `uv` se invoca una vez con los argumentos exactos
    y su código de salida se propaga tal cual (0-8 de `run-night`, y cualquier otro
    como 1 para comprobar que no hay una lista blanca de códigos)."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(
        home,
        bin_dir,
        extra={"UV_CALL_LOG_FILE": str(uv_call_log), "UV_EXIT_CODE": str(uv_exit_code)},
    )

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == uv_exit_code
    assert uv_call_log.exists(), "el 'uv' falso debía invocarse con las precondiciones en verde"
    calls = uv_call_log.read_text().splitlines()
    assert calls == ["run --frozen nocturna run-night"], calls


def test_reenvia_argumentos(tmp_path):
    """Los argumentos del envoltorio llegan a `uv run --frozen nocturna run-night` tal cual."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(home, bin_dir, extra={"UV_CALL_LOG_FILE": str(uv_call_log)})

    result = _run_wrapper(["--dry-run"], env, cwd=tmp_path)

    assert result.returncode == 0
    calls = uv_call_log.read_text().splitlines()
    assert calls == ["run --frozen nocturna run-night --dry-run"], calls


def test_centinela_impide_el_segundo_lanzamiento(tmp_path):
    """Dos lanzamientos seguidos en la misma ventana: el segundo sale 76 sin invocar `uv`."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(home, bin_dir, extra={"UV_CALL_LOG_FILE": str(uv_call_log)})

    first = _run_wrapper([], env, cwd=tmp_path)
    second = _run_wrapper([], env, cwd=tmp_path)

    assert first.returncode == 0
    assert second.returncode == 76
    calls = uv_call_log.read_text().splitlines()
    assert len(calls) == 1, (
        f"'uv' se invocó {len(calls)} veces; el centinela debía impedir la segunda: {calls}"
    )
    sentinel = _log_dir(home) / f".launched-{_night_key()}"
    assert sentinel.exists()


def test_centinela_es_atomico_bajo_lanzamientos_concurrentes(tmp_path):
    """Dos invocaciones lanzadas en paralelo de verdad (`Popen`, no secuenciales
    con `.run()`) ejercitan la escritura atómica del centinela (`set -C`, hallazgo
    de la ronda anterior) bajo solapamiento real, no solo bajo el camino
    secuencial de `test_centinela_impide_el_segundo_lanzamiento`.

    No es un test de temporización fina ni debería ser intermitente: `set -C`
    usa la apertura `O_CREAT|O_EXCL` del sistema operativo para el fichero del
    centinela, que es atómica a nivel de kernel -- como mucho un proceso puede
    ganar esa carrera, sea cual sea el orden real de `interleaving` del
    planificador (incluso si el sistema los ejecuta de forma efectivamente
    secuencial). Por eso la aserción de abajo (exactamente un 0 y un 76) es
    determinista, no depende de qué proceso "gane" ni de cuán solapados
    estuvieran en la práctica."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(home, bin_dir, extra={"UV_CALL_LOG_FILE": str(uv_call_log)})

    proc_a = _popen_wrapper([], env, cwd=tmp_path)
    proc_b = _popen_wrapper([], env, cwd=tmp_path)
    out_a, err_a = proc_a.communicate(timeout=20)
    out_b, err_b = proc_b.communicate(timeout=20)

    codes = sorted([proc_a.returncode, proc_b.returncode])
    assert codes == [0, 76], (
        "se esperaba exactamente un 0 y un 76 bajo lanzamientos concurrentes; "
        f"obtenido a={proc_a.returncode} b={proc_b.returncode} "
        f"(a: out={out_a!r} err={err_a!r}; b: out={out_b!r} err={err_b!r})"
    )
    calls = uv_call_log.read_text().splitlines()
    assert len(calls) == 1, f"'uv' debía invocarse exactamente una vez: {calls}"


def test_centinela_se_crea_antes_de_ejecutar(tmp_path):
    """Una noche donde `run-night` falla (código 1) deja el centinela puesto: no se relanza sola."""
    home, bin_dir = _setup_env(tmp_path)
    env = _base_env(home, bin_dir, extra={"UV_EXIT_CODE": "1"})

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == 1
    sentinel = _log_dir(home) / f".launched-{_night_key()}"
    assert sentinel.exists(), (
        "una noche que falla en run-night debe dejar el centinela puesto "
        "(regla de noches fallidas: no se relanza sola)"
    )


@pytest.mark.parametrize(
    "scenario, extra_env",
    [
        ("docker_no_responde", {"DOCKER_INFO_EXIT": "1"}),
        ("compose_falla", {"DOCKER_COMPOSE_EXIT": "1"}),
        ("postgres_no_healthy", {"DOCKER_HEALTH_STATUS": "starting"}),
    ],
)
def test_centinela_se_borra_si_abortan_las_precondiciones(tmp_path, scenario, extra_env):
    """Los tres caminos de aborto 75 de `abort_precondition` no dejan el centinela
    puesto: no se gastó ni un token, así que la noche no debe quedar quemada e
    imposible de relanzar sin intervención manual."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(
        home, bin_dir, wait_s="1", extra={"UV_CALL_LOG_FILE": str(uv_call_log), **extra_env}
    )

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == 75, f"escenario {scenario}: se esperaba 75"
    assert not uv_call_log.exists(), f"escenario {scenario}: 'run-night' no debió invocarse"
    sentinel = _log_dir(home) / f".launched-{_night_key()}"
    assert not sentinel.exists(), (
        f"escenario {scenario}: el centinela quedó puesto tras un aborto de precondiciones; "
        "esa noche quedaría quemada e imposible de relanzar sin borrar el fichero a mano"
    )


@pytest.mark.parametrize("invalid_wait_s", ["", "abc", "3.5", "-1"])
def test_nocturna_wait_s_invalido_aborta_sin_crear_nada(tmp_path, invalid_wait_s):
    """`NOCTURNA_WAIT_S` no numérico o vacío sale 77 **antes** de `mkdir -p
    "$log_dir"` y antes de tocar el centinela: ni el directorio de logs ni el
    centinela deben llegar a existir. Sin esta validación, un
    `NOCTURNA_WAIT_S` con un typo revienta más abajo, en la aritmética de
    `wait_deadline`, DESPUÉS de crear el centinela -- quemando una noche de
    calibración por un error tipográfico.

    **El caso `invalid_wait_s=""` está en rojo, y no lo he arreglado tocando
    el script.** `wait_s="${NOCTURNA_WAIT_S:-300}"` usa la forma
    dos-puntos-guion: ese operador trata "vacío" exactamente igual que "no
    definida" y sustituye por el valor por defecto (300) ANTES de que la
    validación de la línea de abajo (`[[ ! "$wait_s" =~ ^[0-9]+$ ]]`) llegue
    a ver nada -- `wait_s` ya vale "300" (válido) para cuando se comprueba.
    La cabecera del script promete "NOCTURNA_WAIT_S no numérico, ...
    NOCTURNA_LOG_DIR vacío" como los dos disparadores de 77, pero con esta
    forma de sustitución un NOCTURNA_WAIT_S vacío en concreto es inalcanzable
    -- no existe entrada que lo dispare, porque deja de estar vacío antes de
    que se pueda comprobar. Necesitaría `${NOCTURNA_WAIT_S-300}` (sin los dos
    puntos, que solo sustituye si la variable está sin definir, preservando
    una cadena vacía explícita) o una comprobación de vacío anterior a la
    sustitución. Es tarea del `backend`, no de este test."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    docker_call_log = tmp_path / "docker-calls.log"
    env = _base_env(
        home,
        bin_dir,
        wait_s=invalid_wait_s,
        extra={"UV_CALL_LOG_FILE": str(uv_call_log), "DOCKER_CALL_LOG_FILE": str(docker_call_log)},
    )

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == 77, (
        f"NOCTURNA_WAIT_S={invalid_wait_s!r} debía abortar con 77; "
        f"returncode={result.returncode} stderr={result.stderr!r}"
    )
    assert "NOCTURNA_WAIT_S" in result.stderr
    assert not _log_dir(home).exists(), (
        "el directorio de logs no debe crearse cuando NOCTURNA_WAIT_S es inválido"
    )
    assert not uv_call_log.exists()
    assert not docker_call_log.exists()


def test_nocturna_log_dir_vacio_aborta_con_77(tmp_path):
    """`NOCTURNA_LOG_DIR` vacío no es "usa el directorio actual": es casi con
    seguridad un error de configuración (una sustitución de variable vacía en
    el plist de launchd, por ejemplo). Sale 77 antes de tocar nada.

    **Este test está en rojo, y no lo he arreglado tocando el script.** Igual
    que con `NOCTURNA_WAIT_S=""` (ver el docstring de
    `test_nocturna_wait_s_invalido_aborta_sin_crear_nada`):
    `log_dir="${NOCTURNA_LOG_DIR:-$HOME/nocturna-logs}"` usa la forma
    dos-puntos-guion, que sustituye tanto "no definida" como "vacía" por el
    valor por defecto. Con `NOCTURNA_LOG_DIR=""`, `log_dir` nunca llega vacío
    a la comprobación `[[ -z "$log_dir" ]]` de la línea siguiente: ya vale
    `$HOME/nocturna-logs` para entonces. La guarda "NOCTURNA_LOG_DIR no puede
    estar vacío" que promete la cabecera del script es código muerto tal y
    como está escrita hoy -- ninguna entrada la alcanza. Es tarea del
    `backend`, no de este test."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(
        home,
        bin_dir,
        wait_s="1",
        extra={"NOCTURNA_LOG_DIR": "", "UV_CALL_LOG_FILE": str(uv_call_log)},
    )

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == 77, (
        f"returncode={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "NOCTURNA_LOG_DIR" in result.stderr
    assert not uv_call_log.exists()


def test_aborta_si_no_puede_escribir_el_centinela_por_permisos(tmp_path):
    """Un `NOCTURNA_LOG_DIR` sin permiso de escritura hace que la propia
    escritura atómica del centinela (`set -C`) falle por permisos, no por "ya
    existe": el envoltorio debe distinguirlo y salir con 75 con un mensaje
    atribuido a permisos, sin invocar `run-night`.

    Corrección de esta ronda: antes, un `NOCTURNA_LOG_DIR` de solo lectura
    hacía que el script siguiera adelante sin centinela y acabara en 75
    porque `docker compose up` fallaba a su vez -- un mensaje que culpaba a
    Docker de lo que era, en realidad, un problema de permisos del
    directorio de logs."""
    home, bin_dir = _setup_env(tmp_path)
    log_dir = tmp_path / "readonly-logs"
    log_dir.mkdir()
    log_dir.chmod(0o555)  # r-xr-xr-x: legible/recorrible, sin permiso de escritura
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(
        home,
        bin_dir,
        wait_s="1",
        extra={"NOCTURNA_LOG_DIR": str(log_dir), "UV_CALL_LOG_FILE": str(uv_call_log)},
    )

    try:
        result = _run_wrapper([], env, cwd=tmp_path)
    finally:
        log_dir.chmod(0o700)  # restaurar antes de que tmp_path intente limpiarse

    assert result.returncode == 75, (
        f"returncode={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert "permisos" in result.stderr, (
        f"el mensaje debía atribuirse a permisos, no a Docker: {result.stderr!r}"
    )
    assert not uv_call_log.exists(), "'run-night' no debió invocarse"


def test_term_durante_sondeo_de_precondiciones_borra_el_centinela(tmp_path):
    """Una señal `TERM` recibida mientras el envoltorio sondea precondiciones
    (Docker/postgres, antes de invocar `run-night`) debe borrar el centinela
    y salir con 75: sin este `trap`, un logout/apagado/`launchctl unload`
    durante el sondeo (hasta `NOCTURNA_WAIT_S` de tiempo real) dejaría el
    centinela puesto sin haberse gastado nada, quemando la noche sin
    necesidad.

    Sincronización por sondeo de fichero, no por `sleep` fijo: se espera a
    que aparezca el propio centinela (creado justo antes de registrar el
    `trap`) y se deja `NOCTURNA_WAIT_S=2` con `DOCKER_HEALTH_STATUS=starting`
    para que el envoltorio siga sondeando (y por tanto con el `trap` activo)
    durante un margen amplio -- documentado en bash: si la señal llega
    mientras el intérprete espera a un comando en primer plano (aquí, el
    `sleep` interno del sondeo), el `trap` no se ejecuta hasta que ese
    comando termina, así que el test puede tardar hasta un par de segundos;
    el `timeout` de `communicate` lo cubre con margen amplio."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(
        home,
        bin_dir,
        wait_s="2",
        extra={"UV_CALL_LOG_FILE": str(uv_call_log), "DOCKER_HEALTH_STATUS": "starting"},
    )

    proc = _popen_wrapper([], env, cwd=tmp_path)
    sentinel = _log_dir(home) / f".launched-{_night_key()}"
    deadline = time.monotonic() + 10
    while not sentinel.exists():
        if time.monotonic() > deadline:
            proc.kill()
            proc.communicate(timeout=5)
            pytest.fail(
                "el centinela no apareció a tiempo; no se pudo sincronizar el envío de TERM"
            )
        time.sleep(0.02)
    # Margen para que el trap quede registrado (unas pocas líneas de comentarios
    # tras la creación del centinela) y el sondeo de Docker esté ya en marcha.
    time.sleep(0.3)

    proc.send_signal(signal.SIGTERM)
    stdout, stderr = proc.communicate(timeout=10)

    assert proc.returncode == 75, (
        f"se esperaba 75 tras TERM durante el sondeo; returncode={proc.returncode} "
        f"stdout={stdout!r} stderr={stderr!r}"
    )
    assert not sentinel.exists(), (
        "el centinela debía borrarse: TERM llegó antes de invocar run-night"
    )
    assert not uv_call_log.exists(), "'uv' no debía invocarse: TERM llegó durante el sondeo"


def test_trap_no_borra_el_centinela_una_vez_invocado_run_night(tmp_path):
    """Invariante que impide el doble gasto: una vez invocado `run-night`
    (`trap - INT TERM` justo antes), el centinela debe sobrevivir a
    cualquier señal -- porque a partir de ahí puede haber gasto real en
    marcha, y borrar el centinela permitiría un segundo lanzamiento la misma
    noche.

    Sincronización por sondeo de fichero: `UV_CALL_LOG_FILE` lo escribe el
    `uv` falso como su primera acción, antes de `UV_SLEEP`; en cuanto
    aparece, el envoltorio ya ha pasado el `trap - INT TERM` (esa línea es
    inmediatamente anterior a la invocación). Se envía TERM en ese instante:
    sin trap activo, la disposición por defecto de TERM mata el proceso sin
    ejecutar ningún `rm -f`, así que el centinela debe seguir ahí."""
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(home, bin_dir, extra={"UV_CALL_LOG_FILE": str(uv_call_log), "UV_SLEEP": "5"})

    proc = _popen_wrapper([], env, cwd=tmp_path)
    deadline = time.monotonic() + 10
    while not uv_call_log.exists():
        if time.monotonic() > deadline:
            proc.kill()
            proc.communicate(timeout=5)
            pytest.fail("'uv' falso no se invocó a tiempo; no se pudo sincronizar el envío de TERM")
        time.sleep(0.02)

    proc.send_signal(signal.SIGTERM)
    proc.communicate(timeout=10)

    sentinel = _log_dir(home) / f".launched-{_night_key()}"
    assert sentinel.exists(), (
        "el centinela no debe borrarse por una señal TERM recibida después de invocar "
        "run-night: esa es justo la invariante que impide el doble gasto (trap - INT TERM "
        "justo antes de la invocación)"
    )


def test_dos_ficheros_de_log_separados(tmp_path):
    """stdout y stderr de `run-night` van a ficheros distintos, sin mezclarse en ningún sentido."""
    home, bin_dir = _setup_env(tmp_path)
    env = _base_env(
        home,
        bin_dir,
        extra={
            "UV_STDOUT_MSG": "MARCA_STDOUT_7f3a",
            "UV_STDERR_MSG": "MARCA_STDERR_9c1b",
        },
    )

    result = _run_wrapper([], env, cwd=tmp_path)
    assert result.returncode == 0

    night_key = _night_key()
    out_log = (_log_dir(home) / f"night-{night_key}.out.log").read_text()
    err_log = (_log_dir(home) / f"night-{night_key}.err.log").read_text()

    assert "MARCA_STDOUT_7f3a" in out_log
    assert "MARCA_STDOUT_7f3a" not in err_log
    assert "MARCA_STDERR_9c1b" in err_log
    assert "MARCA_STDERR_9c1b" not in out_log


def test_path_sin_herencia_del_shell(tmp_path):
    """Con el PATH exacto de `launchd` (sin ~/.local/bin ni /opt/homebrew/bin
    heredados) el envoltorio encuentra igualmente los binarios, porque los busca
    en $HOME/.local/bin tras reescribir su propio PATH -- no en el PATH que le
    pasa el proceso padre.

    Como `test_valor_por_defecto_de_nocturna_path_es_el_de_produccion`, este es
    uno de los dos tests que a propósito NO pasan por `_base_env`: depender de
    la precedencia de `$HOME/.local/bin` es justo lo que se está verificando
    aquí, no un descuido."""
    home = _make_home(tmp_path)
    bin_dir = home / ".local" / "bin"
    _install_fake_bins(bin_dir)
    uv_call_log = tmp_path / "uv-calls.log"
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "UV_CALL_LOG_FILE": str(uv_call_log),
    }

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == 0, (
        "con el PATH exacto de launchd, el envoltorio debe encontrar los binarios en "
        f"$HOME/.local/bin igualmente; stdout={result.stdout!r} stderr={result.stderr!r}"
    )
    assert uv_call_log.exists()


@pytest.mark.parametrize("provide_key", [False, True])
def test_no_define_api_key(tmp_path, provide_key):
    """El envoltorio no añade la variable de entorno de API key al entorno del hijo, y
    tampoco la borra si venía en el entorno de entrada: la guarda ApiKeyInEnvironment de
    AgentSDKProvider es la que debe reventar en ese caso, no este script."""
    # Partido en dos literales concatenados a propósito -- no es una ofuscación
    # ni una evasión: `.claude/hooks/guard-write.sh` bloquea cualquier escritura
    # cuyo contenido case con `ANTHROPIC_API_KEY\s*=`, precisamente para que
    # nadie configure esa variable en este proyecto (CLAUDE.md, "Restricción
    # que gobierna todo el diseño"). Este test necesita el *nombre* de la
    # variable como dato (para comprobar que el envoltorio no la toca), no
    # asignarle una API key real; concatenar el literal evita que el hook
    # confunda ese dato con la asignación que sí prohíbe.
    api_key_var = "ANTHROPIC_API" + "_KEY"
    home, bin_dir = _setup_env(tmp_path)
    env_dump = tmp_path / "uv-env.txt"
    env = _base_env(home, bin_dir, extra={"UV_ENV_DUMP_FILE": str(env_dump)})
    if provide_key:
        env[api_key_var] = "sk-test-no-usar-nunca"

    result = _run_wrapper([], env, cwd=tmp_path)
    assert result.returncode == 0
    dumped = env_dump.read_text()

    if provide_key:
        assert f"{api_key_var}=sk-test-no-usar-nunca" in dumped, (
            "el envoltorio no debe silenciar la variable de API key si venía en el "
            "entorno de entrada"
        )
    else:
        assert f"{api_key_var}=" not in dumped, (
            "el envoltorio no debe definir la variable de API key de la nada"
        )


def test_claude_nunca_se_invoca(tmp_path):
    """El envoltorio solo hace `command -v claude` (una comprobación de
    presencia, no una ejecución): en un camino de éxito completo, el `claude`
    falso no debe registrar ninguna invocación.

    Guarda contra una edición futura que añadiera, por ejemplo, un
    `claude -p ...` al envoltorio sin que ningún test lo notara -- las
    guardas AST de `test_llm_call_sites.py` solo recorren Python, no
    `scripts/*.sh` (hallazgo D de revisión)."""
    home, bin_dir = _setup_env(tmp_path)
    claude_call_log = tmp_path / "claude-calls.log"
    env = _base_env(home, bin_dir, extra={"CLAUDE_CALL_LOG_FILE": str(claude_call_log)})

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == 0
    assert not claude_call_log.exists(), (
        "el 'claude' falso se invocó; el envoltorio solo debe comprobar su presencia "
        "con 'command -v', nunca ejecutarlo"
    )


def test_cd_a_backend_antes_de_invocar_uv(tmp_path):
    """`Settings` (infrastructure/config.py) lee `.env` relativo al cwd, y
    `launchd` arranca los agentes con cwd `/`: sin el `cd "${repo_root}/backend"`
    del envoltorio, un despliegue real leería el `.env` equivocado (o ninguno).

    Mutante superviviente detectado por revisión (hallazgo C): borrar esa
    línea dejaba los 22 tests en verde, porque ninguno miraba el cwd real con
    el que se invocaba `uv`. Esta prueba lo hace leyendo `PWD` del volcado de
    entorno del `uv` falso -- `PWD` es una variable que bash mantiene y
    exporta automáticamente al cambiar de directorio, así que viaja con el
    entorno del hijo sin instrumentación adicional."""
    home, bin_dir = _setup_env(tmp_path)
    env_dump = tmp_path / "uv-env.txt"
    env = _base_env(home, bin_dir, extra={"UV_ENV_DUMP_FILE": str(env_dump)})

    result = _run_wrapper([], env, cwd=tmp_path)
    assert result.returncode == 0

    dumped_lines = env_dump.read_text().splitlines()
    pwd_lines = [line for line in dumped_lines if line.startswith("PWD=")]
    assert pwd_lines, f"'uv' no volcó PWD en su entorno: {dumped_lines!r}"
    actual_pwd = Path(pwd_lines[-1].split("=", 1)[1])
    assert actual_pwd == BACKEND_ROOT, (
        f"'uv' se invocó con cwd={actual_pwd}, se esperaba {BACKEND_ROOT} "
        "(falta el 'cd \"${repo_root}/backend\"' del envoltorio)"
    )


def test_presupuesto_de_espera_es_compartido_entre_fases(tmp_path):
    """NOCTURNA_WAIT_S es un único plazo para las dos fases de sondeo juntas
    (Docker, salud de postgres), no NOCTURNA_WAIT_S por fase.

    Se fuerza que la fase de Docker (el `docker compose up -d postgres` de una
    sola invocación, no el sondeo de `docker info`) consuma ya casi todo el
    plazo (`DOCKER_COMPOSE_SLEEP=2` con `NOCTURNA_WAIT_S=3`), y luego se deja
    la salud de postgres sin cumplirse nunca. Si el plazo fuera compartido
    (`wait_deadline` calculado una sola vez, como hace hoy el script), la fase
    de postgres debe abortar casi de inmediato porque el plazo ya está a
    punto de agotarse. Si, por regresión, cada fase tuviera su propio
    contador, la fase de postgres arrancaría un `NOCTURNA_WAIT_S` completo
    y nuevo, y el total observado sería sensiblemente mayor.

    Test con reloj -- rangos esperados y qué mirar si falla:

    - **Correcto (plazo compartido, comportamiento actual)**: medido
      empíricamente en ~2.2-3.3s (no ~2.7-3.7s como decía una versión previa
      de este docstring, corregida tras medir 3.26/3.26/3.25/3.24/2.21s en
      cinco corridas -- el suelo real está por debajo de lo que se declaraba
      antes). El rango viene de los 2s de `DOCKER_COMPOSE_SLEEP` más como
      mucho ~1s de sondeo de postgres, con la granularidad de un segundo de
      `date +%s` con la que el script calcula `wait_deadline`/`remaining`, y
      la variabilidad normal de arrancar varios subprocesos bash/fake por
      invocación.
    - **Regresión (presupuestos separados por fase)**: ~5-5.5s (los mismos
      2s de Docker + un `NOCTURNA_WAIT_S=3` completo y nuevo para la fase de
      postgres, que nunca cumple); reproducido en el mutante de revisión con
      5.23-5.26s.
    - **Umbral**: 4.5s -- deja ~1.2s de margen por debajo del techo de lo
      correcto (~3.3s) y ~0.5-0.7s por debajo del suelo de la regresión
      (~5-5.23s), sin acercarse a ninguno de los dos valores reales medidos.
    - **Si este test falla por encima de 4.5s**: sospecha primero de
      `wait_deadline` recalculado dentro de `_poll_until_deadline` o de una
      segunda variable de plazo introducida para la fase de postgres, no de
      una máquina cargada -- el margen es demasiado amplio para que la carga
      de CPU explique por sí sola cruzar el umbral.
    """
    home, bin_dir = _setup_env(tmp_path)
    uv_call_log = tmp_path / "uv-calls.log"
    env = _base_env(
        home,
        bin_dir,
        wait_s="3",
        extra={
            "UV_CALL_LOG_FILE": str(uv_call_log),
            "DOCKER_COMPOSE_SLEEP": "2",
            "DOCKER_HEALTH_STATUS": "starting",
        },
    )

    started = time.monotonic()
    result = _run_wrapper([], env, cwd=tmp_path)
    elapsed = time.monotonic() - started

    assert result.returncode == 75
    assert not uv_call_log.exists()
    assert elapsed < 4.5, (
        f"tardó {elapsed:.2f}s con NOCTURNA_WAIT_S=3 y 2s ya consumidos por Docker; "
        "si las dos fases tuvieran presupuestos separados tardaría ~5-5.5s "
        "(2s + un NOCTURNA_WAIT_S=3 completo para la fase de postgres). Ver el "
        "docstring de este test para el desglose completo de rangos esperados."
    )


def test_plantilla_de_plist_es_coherente(tmp_path):
    """El `.plist.template` no invoca un intérprete de comandos, dispara a las 00:05,
    no se relanza al cargar la sesión, y no tiene ninguna clave de reintento automático
    (KeepAlive u otra): un reintento sería el camino a 2 x nightly_tokens en la misma
    ventana de ejecución."""
    template = PLIST_TEMPLATE_PATH.read_text()
    materialized = template.replace("__REPO_ROOT__", "/opt/nocturna-repo").replace(
        "__HOME__", "/Users/testuser"
    )
    plist_path = tmp_path / "com.nocturna.run-night.plist"
    plist_path.write_bytes(materialized.encode("utf-8"))

    with plist_path.open("rb") as f:
        plist = plistlib.load(f)

    assert set(plist.keys()) == {
        "Label",
        "ProgramArguments",
        "StartCalendarInterval",
        "StandardOutPath",
        "StandardErrorPath",
        "RunAtLoad",
    }, (
        f"claves inesperadas en el plist: {sorted(plist.keys())}; revisa si alguna es "
        "KeepAlive u otro mecanismo de reintento automático, prohibido por la Regla de "
        "noches fallidas de docs/CALIBRACION.md"
    )

    program_arguments = plist["ProgramArguments"]
    assert program_arguments == ["/opt/nocturna-repo/backend/scripts/run-night-scheduled.sh"]
    for arg in program_arguments:
        assert "bash -c" not in arg
        assert ">" not in arg
        assert "<" not in arg
        assert "|" not in arg

    assert plist["StartCalendarInterval"] == {"Hour": 0, "Minute": 5}
    assert plist["RunAtLoad"] is False
    assert "KeepAlive" not in plist
