"""Tests de `backend/scripts/morning-report-scheduled.sh` y de su plantilla launchd (T105).

Sin Docker, `uv` ni `osascript` reales: los tres son scripts de shell falsos en un directorio
aislado bajo `tmp_path`, el único que `NOCTURNA_PATH` antepone al PATH del sistema.
"""

from __future__ import annotations

import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "morning-report-scheduled.sh"
PLIST_TEMPLATE_PATH = BACKEND_ROOT / "scripts" / "com.nocturna.morning-report.plist.template"

_FAKE_UV = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$UV_CALL_LOG_FILE"
exit "${UV_EXIT_CODE:-0}"
"""

_FAKE_DOCKER = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$DOCKER_CALL_LOG_FILE"
case "${1:-}" in
    inspect) printf '%s\\n' "${DOCKER_HEALTH_STATUS:-healthy}"; exit 0 ;;
    *) exit 0 ;;
esac
"""

_FAKE_OSASCRIPT = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$OSASCRIPT_CALL_LOG_FILE"
exit 0
"""


def _install(path: Path, script: str) -> None:
    path.write_text(script)
    path.chmod(0o755)


class Env:
    def __init__(self, tmp_path: Path, *, uv: bool = True) -> None:
        self.tmp_path = tmp_path
        self.home = tmp_path / "home"
        self.home.mkdir()
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        if uv:
            _install(self.bin / "uv", _FAKE_UV)
        _install(self.bin / "docker", _FAKE_DOCKER)
        _install(self.bin / "osascript", _FAKE_OSASCRIPT)
        self.uv_log = tmp_path / "uv-calls.log"
        self.docker_log = tmp_path / "docker-calls.log"
        self.osascript_log = tmp_path / "osascript-calls.log"

    def run(self, *args: str, **extra: str) -> subprocess.CompletedProcess[str]:
        env = {
            "HOME": str(self.home),
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "NOCTURNA_PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
            "NOCTURNA_WAIT_S": "1",
            "UV_CALL_LOG_FILE": str(self.uv_log),
            "DOCKER_CALL_LOG_FILE": str(self.docker_log),
            "OSASCRIPT_CALL_LOG_FILE": str(self.osascript_log),
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


def test_lanza_morning_report_programado_y_propaga_el_codigo(tmp_path):
    env = Env(tmp_path)

    result = env.run("--no-notify", UV_EXIT_CODE="1")

    assert result.returncode == 1
    assert env.uv_log.read_text().splitlines() == [
        "run --frozen nocturna morning-report --trigger scheduled --no-notify"
    ]


def test_no_levanta_docker_compose(tmp_path):
    env = Env(tmp_path)

    assert env.run().returncode == 0

    calls = env.docker_log.read_text().splitlines()
    assert calls and all(not c.startswith("compose") for c in calls)


def test_postgres_no_healthy_no_impide_el_informe(tmp_path):
    env = Env(tmp_path)

    result = env.run(DOCKER_HEALTH_STATUS="starting")

    assert result.returncode == 0
    assert len(env.uv_log.read_text().splitlines()) == 1
    err = (env.home / "nocturna-logs").glob("morning-*.err.log")
    assert "sin base" in next(iter(err)).read_text()


def test_sin_uv_avisa_con_osascript_y_sale_77(tmp_path):
    env = Env(tmp_path, uv=False)

    result = env.run()

    assert result.returncode == 77
    assert "no se pudo generar el informe (uv no encontrado)" in env.osascript_log.read_text()


@pytest.mark.parametrize("wait_s", ["", "abc", "-1"])
def test_nocturna_wait_s_invalido_sale_77(tmp_path, wait_s):
    assert Env(tmp_path).run(NOCTURNA_WAIT_S=wait_s).returncode == 77


def test_nocturna_log_dir_vacio_sale_77(tmp_path):
    assert Env(tmp_path).run(NOCTURNA_LOG_DIR="").returncode == 77


def test_la_plantilla_del_plist_pasa_plutil_lint():
    plutil = shutil.which("plutil")
    if plutil is None:
        pytest.skip("plutil solo existe en macOS")
    result = subprocess.run(
        [plutil, "-lint", str(PLIST_TEMPLATE_PATH)], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_el_plist_es_diario_a_las_07_30_con_run_at_load_y_sin_keepalive():
    with PLIST_TEMPLATE_PATH.open("rb") as fh:
        plist = plistlib.load(fh)

    assert plist["StartCalendarInterval"] == {"Hour": 7, "Minute": 30}
    assert plist["RunAtLoad"] is True
    assert "KeepAlive" not in plist
    assert plist["Label"] == "com.nocturna.morning-report"
    assert plist["ProgramArguments"] == [
        "__REPO_ROOT__/backend/scripts/morning-report-scheduled.sh"
    ]
