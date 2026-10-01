"""Tests de `backend/scripts/archive-snapshot-scheduled.sh` y de su plantilla launchd (T81).

Sin Docker, `uv` ni red reales: `uv` y `docker` son scripts de shell falsos en un
directorio aislado bajo `tmp_path`, y `NOCTURNA_PATH` apunta solo a ese
directorio mas `/usr/bin:/bin:/usr/sbin:/sbin` (de ahi salen `date`, `mkdir`,
`sleep` y `caffeinate`). Mismo esquema que `test_run_night_scheduled.py`.
`HOME` es siempre un `tmp_path`: los logs caen en `<home>/nocturna-logs`.
"""

from __future__ import annotations

import plistlib
import re
import shutil
import subprocess
from datetime import date, timedelta
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "archive-snapshot-scheduled.sh"
PLIST_TEMPLATE_PATH = BACKEND_ROOT / "scripts" / "com.nocturna.archive-snapshot.plist.template"

_FAKE_UV = """#!/usr/bin/env bash
if [ -n "${UV_CALL_LOG_FILE:-}" ]; then
    printf '%s\\n' "$*" >> "$UV_CALL_LOG_FILE"
fi
if [ -n "${UV_STDOUT_MSG:-}" ]; then
    printf '%s\\n' "$UV_STDOUT_MSG"
fi
if [ -n "${UV_STDERR_MSG:-}" ]; then
    printf '%s\\n' "$UV_STDERR_MSG" >&2
fi
exit "${UV_EXIT_CODE:-0}"
"""

_FAKE_DOCKER = """#!/usr/bin/env bash
if [ -n "${DOCKER_CALL_LOG_FILE:-}" ]; then
    printf '%s\\n' "$*" >> "$DOCKER_CALL_LOG_FILE"
fi
case "${1:-}" in
    info) exit "${DOCKER_INFO_EXIT:-0}" ;;
    compose) exit "${DOCKER_COMPOSE_EXIT:-0}" ;;
    inspect) printf '%s\\n' "${DOCKER_HEALTH_STATUS:-healthy}"; exit 0 ;;
    *) exit 0 ;;
esac
"""


def _install(path: Path, script: str) -> None:
    path.write_text(script)
    path.chmod(0o755)


class Env:
    def __init__(self, tmp_path: Path, *, uv: bool = True, docker: bool = True) -> None:
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        if uv:
            _install(self.bin / "uv", _FAKE_UV)
        if docker:
            _install(self.bin / "docker", _FAKE_DOCKER)
        self.uv_log = tmp_path / "uv-calls.log"
        self.docker_log = tmp_path / "docker-calls.log"
        self.tmp_path = tmp_path

    @property
    def logs(self) -> Path:
        return self.home / "nocturna-logs"

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "NOCTURNA_PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "NOCTURNA_WAIT_S": "1",
            "UV_CALL_LOG_FILE": str(self.uv_log),
            "DOCKER_CALL_LOG_FILE": str(self.docker_log),
            **extra,
        }
        return subprocess.run(
            [str(SCRIPT_PATH), *args],
            env=env,
            cwd=self.tmp_path,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )


def test_el_script_existe_y_es_ejecutable():
    assert SCRIPT_PATH.is_file() and SCRIPT_PATH.stat().st_mode & 0o111


def test_sin_uv_sale_77_sin_invocar_nada(tmp_path):
    env = Env(tmp_path, uv=False)

    result = env.run()

    assert result.returncode == 77
    assert "uv" in result.stderr
    assert not env.docker_log.exists() and not env.uv_log.exists()


@pytest.mark.parametrize("value", ["abc", "", "-1", "1.5", "10s"])
def test_nocturna_wait_s_invalido_sale_77(tmp_path, value):
    env = Env(tmp_path)

    result = env.run(NOCTURNA_WAIT_S=value)

    assert result.returncode == 77
    assert "NOCTURNA_WAIT_S" in result.stderr
    assert not env.docker_log.exists() and not env.uv_log.exists()


def test_nocturna_log_dir_vacio_sale_77(tmp_path):
    env = Env(tmp_path)

    result = env.run(NOCTURNA_LOG_DIR="")

    assert result.returncode == 77 and "NOCTURNA_LOG_DIR" in result.stderr
    assert not env.uv_log.exists()


def test_docker_que_no_responde_sale_75_sin_lanzar_el_comando(tmp_path):
    env = Env(tmp_path)

    result = env.run(DOCKER_INFO_EXIT="1")

    assert result.returncode == 75 and "Docker" in result.stderr
    assert not env.uv_log.exists()


def test_docker_compose_que_falla_sale_75(tmp_path):
    env = Env(tmp_path)

    result = env.run(DOCKER_COMPOSE_EXIT="1")

    assert result.returncode == 75 and "docker compose" in result.stderr
    assert not env.uv_log.exists()


@pytest.mark.parametrize("status", ["starting", "unhealthy"])
def test_postgres_no_healthy_sale_75_sin_lanzar_el_comando(tmp_path, status):
    env = Env(tmp_path)

    result = env.run(DOCKER_HEALTH_STATUS=status)

    assert result.returncode == 75 and "healthy" in result.stderr
    assert not env.uv_log.exists()


@pytest.mark.parametrize("code", [0, 1, 2])
def test_propaga_el_codigo_de_archive_snapshot(tmp_path, code):
    env = Env(tmp_path)

    result = env.run(UV_EXIT_CODE=str(code))

    assert result.returncode == code
    out_log = next(env.logs.glob("archive-*.out.log"))
    assert out_log.read_text().rstrip().endswith(f"código de salida: {code}")


def test_lanza_uv_con_frozen_y_el_subcomando_y_reenvia_los_argumentos(tmp_path):
    env = Env(tmp_path)

    assert env.run("--full", "--dry-run").returncode == 0

    assert env.uv_log.read_text().splitlines() == [
        "run --frozen nocturna archive-snapshot --full --dry-run"
    ]


def test_levanta_postgres_con_el_compose_del_repositorio(tmp_path):
    env = Env(tmp_path)

    env.run()

    calls = env.docker_log.read_text().splitlines()
    assert "info" in calls
    assert any(
        c.startswith("compose -f ") and c.endswith("docker-compose.yml up -d postgres")
        for c in calls
    )
    assert any(c.startswith("inspect") and "nocturna-postgres" in c for c in calls)


def test_los_logs_son_archive_fecha_out_y_err(tmp_path):
    env = Env(tmp_path)

    result = env.run(UV_STDOUT_MSG="hola stdout", UV_STDERR_MSG="hola stderr")

    assert result.returncode == 0
    outs = sorted(p.name for p in env.logs.glob("archive-*.out.log"))
    errs = sorted(p.name for p in env.logs.glob("archive-*.err.log"))
    assert len(outs) == 1 and len(errs) == 1
    out_match = re.fullmatch(r"archive-(\d{8})\.out\.log", outs[0])
    err_match = re.fullmatch(r"archive-(\d{8})\.err\.log", errs[0])
    assert out_match and err_match and out_match.group(1) == err_match.group(1)
    day = date(
        int(out_match.group(1)[:4]), int(out_match.group(1)[4:6]), int(out_match.group(1)[6:])
    )
    assert abs(day - date.today()) <= timedelta(days=1)  # tolera cruzar medianoche
    out = (env.logs / outs[0]).read_text()
    assert "hola stdout" in out and "código de salida: 0" in out
    assert "hola stderr" in (env.logs / errs[0]).read_text()
    # stdout/stderr del comando no se mezclan con los del envoltorio.
    assert "hola" not in result.stdout + result.stderr


def test_nocturna_log_dir_cambia_el_directorio_de_logs(tmp_path):
    env = Env(tmp_path)
    custom = tmp_path / "otros-logs"

    env.run(NOCTURNA_LOG_DIR=str(custom))

    assert list(custom.glob("archive-*.out.log"))
    assert not env.logs.exists()


def test_no_necesita_claude_ni_centinela(tmp_path):
    env = Env(tmp_path)  # sin binario `claude` en el PATH

    assert env.run().returncode == 0
    assert not list(env.logs.glob("*sentinel*")) and not list(env.logs.glob("*.lock"))


def test_el_fuente_no_menciona_claude_como_binario_requerido():
    text = SCRIPT_PATH.read_text()
    assert "command -v claude" not in text


# ------------------------------------------------------------------ plantilla launchd


def test_la_plantilla_del_plist_pasa_plutil_lint():
    plutil = shutil.which("plutil")
    if plutil is None:
        pytest.skip("plutil solo existe en macOS")

    result = subprocess.run(
        [plutil, "-lint", str(PLIST_TEMPLATE_PATH)], capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0, result.stdout + result.stderr


def _plist() -> dict:
    with PLIST_TEMPLATE_PATH.open("rb") as fh:
        return plistlib.load(fh)


def test_el_plist_se_programa_el_viernes_a_las_10_00():
    interval = _plist()["StartCalendarInterval"]

    assert interval == {"Weekday": 5, "Hour": 10, "Minute": 0}


def test_el_plist_no_arranca_al_cargar_ni_reintenta_en_bucle():
    plist = _plist()

    assert plist["RunAtLoad"] is False
    assert "KeepAlive" not in plist
    assert plist["Label"] == "com.nocturna.archive-snapshot"


def test_el_plist_lanza_el_envoltorio_con_la_plantilla_de_ruta():
    plist = _plist()

    assert plist["ProgramArguments"] == [
        "__REPO_ROOT__/backend/scripts/archive-snapshot-scheduled.sh"
    ]
    assert plist["StandardOutPath"].startswith("__HOME__/")


def _default_path(script: Path) -> str:
    match = re.search(r'^export PATH="\$\{NOCTURNA_PATH:-(.+?)\}"$', script.read_text(), re.M)
    assert match, f"sin PATH por defecto en {script.name}"
    return match.group(1)


def test_el_path_por_defecto_coincide_con_el_de_run_night_y_incluye_homebrew():
    run_night = BACKEND_ROOT / "scripts" / "run-night-scheduled.sh"

    default = _default_path(SCRIPT_PATH)

    assert default == _default_path(run_night)
    assert "/opt/homebrew/bin" in default.split(":")
