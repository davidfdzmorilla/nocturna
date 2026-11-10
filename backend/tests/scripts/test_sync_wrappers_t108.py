"""T108 (A): los envoltorios lanzan `sync-to-vps.sh` en su sitio y sin cambiar su codigo de salida.

Se ejecuta el `sync-to-vps.sh` REAL de `backend/scripts/` contra `ssh`, `docker`, `uv` y
`caffeinate` falsos en `NOCTURNA_PATH`. Todos escriben en un `ORDER_LOG` comun: el orden se
comprueba con ese rastro. Nunca hay red, Docker ni Claude. Nombres en espanol.
"""

from __future__ import annotations

import plistlib
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pytest
from helpers import sync_fakes as fk

BACKEND = Path(__file__).resolve().parents[2]
NIGHT = BACKEND / "scripts" / "run-night-scheduled.sh"
ARCHIVE = BACKEND / "scripts" / "archive-snapshot-scheduled.sh"
MARKER = "__NOCTURNA_SYNC_TARGET__"


@dataclass
class Rig:
    tmp: Path
    script: Path
    status_prefix: str  # night | archive
    main_step: str  # run-night | archive-snapshot
    extra_env: dict

    @property
    def home(self) -> Path:
        return self.tmp / "home"

    @property
    def order(self) -> list[str]:
        f = self.tmp / "order.log"
        return f.read_text().splitlines() if f.exists() else []

    @property
    def status(self) -> list[str]:
        key = datetime.now().strftime("%Y%m%d")
        f = self.home / "nocturna-logs" / f"{self.status_prefix}-{key}.status"
        return f.read_text().splitlines() if f.exists() else []

    def run(self, *args: str, **env: str) -> subprocess.CompletedProcess[str]:
        bin_dir = self.tmp / "bin"
        full = {
            "HOME": str(self.home),
            "PATH": fk.SYSTEM_PATH,
            "NOCTURNA_PATH": f"{bin_dir}:{fk.SYSTEM_PATH}",
            "NOCTURNA_WAIT_S": "1",
            "ORDER_LOG": str(self.tmp / "order.log"),
            "SSH_ARGV_FILE": str(self.tmp / "ssh-argv.log"),
            "DOCKER_CALL_LOG_FILE": str(self.tmp / "docker.log"),
            **self.extra_env,
            **env,
        }
        return subprocess.run(
            [str(self.script), *args],
            env=full,
            cwd=self.tmp,
            capture_output=True,
            text=True,
            timeout=40,
            check=False,
        )


def _make_rig(tmp_path: Path, script: Path, *, configured: bool = True) -> Rig:
    (tmp_path / "home").mkdir()
    fk.install_all(
        tmp_path / "bin",
        ssh=fk.FAKE_SSH,
        docker=fk.FAKE_DOCKER_MAC,
        uv=fk.FAKE_UV_ORDER,
        caffeinate=fk.FAKE_CAFFEINATE,
        claude=fk.FAKE_CLAUDE,
    )
    key = tmp_path / "key"
    key.write_text("k")
    known = tmp_path / "known_hosts"
    known.write_text("h")
    extra = {}
    if configured:
        extra = {
            "NOCTURNA_SYNC_TARGET": "sync@vps.example.org",
            "NOCTURNA_SYNC_KEY": str(key),
            "NOCTURNA_SYNC_KNOWN_HOSTS": str(known),
        }
    is_night = script == NIGHT
    return Rig(
        tmp_path,
        script,
        "night" if is_night else "archive",
        "run-night" if is_night else "archive-snapshot",
        extra,
    )


@pytest.fixture(params=["night", "archive"])
def rig(request, tmp_path) -> Rig:
    return _make_rig(tmp_path, NIGHT if request.param == "night" else ARCHIVE)


@pytest.fixture(params=["night", "archive"])
def unconfigured(request, tmp_path) -> Rig:
    return _make_rig(tmp_path, NIGHT if request.param == "night" else ARCHIVE, configured=False)


def test_el_sync_va_despues_de_la_tarea_principal_y_antes_del_informe(rig):
    result = rig.run(UV_EXIT_CODE="0")

    assert result.returncode == 0, result.stderr
    steps = [
        s
        for s in rig.order
        if s in {"run-night", "evaluate-tensions", "archive-snapshot", "ssh", "morning-report"}
    ]
    if rig.status_prefix == "night":
        assert steps == ["run-night", "evaluate-tensions", "ssh", "morning-report"]
    else:
        assert steps == ["archive-snapshot", "ssh", "morning-report"]


@pytest.mark.parametrize("main_code", [0, 3])
@pytest.mark.parametrize("sync_ssh_exit", [2, 13])
def test_el_codigo_de_la_noche_no_lo_cambia_el_sync(rig, main_code, sync_ssh_exit):
    # ssh 13 -> sync 13 (propagado); ssh 2 -> transporte, sync 2.
    result = rig.run(UV_EXIT_CODE=str(main_code), SSH_EXIT=str(sync_ssh_exit))

    assert result.returncode == main_code
    assert f"sync={sync_ssh_exit}" in rig.status


@pytest.mark.parametrize("main_code", [0, 1])
def test_un_ssh_colgado_da_sync_3_llega_al_informe_y_no_cambia_el_codigo(rig, main_code):
    result = rig.run(
        UV_EXIT_CODE=str(main_code),
        SSH_HANG_S="4317",
        NOCTURNA_SYNC_TIMEOUT_S="2",
    )

    assert result.returncode == main_code, result.stderr
    assert "sync=3" in rig.status
    assert rig.order[-1] == "morning-report"
    assert not subprocess.run(["pgrep", "-f", "sleep 4317"], capture_output=True).stdout


@pytest.mark.parametrize("sync_ssh_exit", [0, 11, 255])
def test_el_codigo_registrado_es_el_del_sync(rig, sync_ssh_exit):
    expected = {0: 0, 11: 11, 255: 2}[sync_ssh_exit]

    result = rig.run(SSH_EXIT=str(sync_ssh_exit))

    assert result.returncode == 0
    assert f"sync={expected}" in rig.status


def test_sync_queda_en_el_status_tras_la_tarea_principal(rig):
    rig.run()

    status = rig.status
    assert status[-1] == "sync=0"
    assert any(line.startswith(f"{rig.main_step}=") for line in status[:-1])


def test_dry_run_no_lanza_sync(rig):
    result = rig.run("--dry-run")

    assert result.returncode == 0
    assert "ssh" not in rig.order
    assert not (rig.tmp / "ssh-argv.log").exists()
    assert not any(line.startswith("sync=") for line in rig.status)


def test_salida_75_no_lanza_sync(rig):
    result = rig.run(DOCKER_HEALTH_STATUS="starting")

    assert result.returncode == 75
    assert "ssh" not in rig.order
    assert not any(line.startswith("sync=") for line in rig.status)


def test_salida_77_no_lanza_sync(rig):
    result = rig.run(NOCTURNA_WAIT_S="abc")

    assert result.returncode == 77
    assert "ssh" not in rig.order
    assert not any(line.startswith("sync=") for line in rig.status)


def test_noche_sin_uv_sale_77_sin_sync(tmp_path):
    rig = _make_rig(tmp_path, NIGHT)
    (tmp_path / "bin" / "uv").unlink()

    result = rig.run()

    assert result.returncode == 77
    assert "ssh" not in rig.order


def test_noche_duplicada_sale_76_y_no_vuelve_a_sincronizar(tmp_path):
    rig = _make_rig(tmp_path, NIGHT)

    first = rig.run()
    second = rig.run()

    assert (first.returncode, second.returncode) == (0, 76)
    assert rig.order.count("ssh") == 1
    assert rig.status.count("sync=0") == 1


@pytest.mark.parametrize("target", [None, "", MARKER])
def test_sin_destino_o_con_el_marcador_el_sync_es_78_y_ssh_no_se_invoca(rig, target):
    env = {"NOCTURNA_SYNC_TARGET": target} if target is not None else {}
    if target is None:
        rig.extra_env.pop("NOCTURNA_SYNC_TARGET")

    result = rig.run(**env)

    assert result.returncode == 0
    assert "sync=78" in rig.status
    assert "ssh" not in rig.order
    assert not (rig.tmp / "ssh-argv.log").exists()


def test_sin_configurar_sigue_dejando_78_y_el_informe(unconfigured):
    result = unconfigured.run(UV_EXIT_CODE="3")

    assert result.returncode == 3
    assert "sync=78" in unconfigured.status
    assert unconfigured.order[-1] == "morning-report"


@pytest.mark.parametrize(
    "template",
    [
        "com.nocturna.run-night.plist.template",
        "com.nocturna.archive-snapshot.plist.template",
    ],
)
def test_las_plantillas_plist_traen_los_marcadores_de_sync(template):
    text = (BACKEND / "scripts" / template).read_text()
    plist = plistlib.loads(text.replace("__HOME__", "/tmp/h").encode())

    assert plist["EnvironmentVariables"] == {
        "NOCTURNA_SYNC_TARGET": "__NOCTURNA_SYNC_TARGET__",
        "NOCTURNA_SYNC_KEY": "__NOCTURNA_SYNC_KEY__",
        "NOCTURNA_SYNC_KNOWN_HOSTS": "__NOCTURNA_SYNC_KNOWN_HOSTS__",
    }
    assert "KeepAlive" not in plist


def test_el_marcador_del_plist_es_el_que_reconoce_sync_to_vps():
    assert MARKER in (BACKEND / "scripts" / "sync-to-vps.sh").read_text()
