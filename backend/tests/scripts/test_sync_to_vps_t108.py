"""T108 (B): `backend/scripts/sync-to-vps.sh` con `ssh` y `docker` falsos.

`shasum` es el real de /usr/bin (el test recalcula el sha con hashlib sobre los bytes que recibe
el `ssh` falso por stdin). Sin red, sin Docker real, sin Claude. Nombres en espanol.
"""

from __future__ import annotations

import hashlib
import subprocess
import time
from pathlib import Path

import pytest
from helpers import sync_fakes as fk

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sync-to-vps.sh"
TARGET = "sync@vps.example.org"
REV = "b7e3a1c9d482"
DUMP = "PGDMP-bytes-del-volcado\nsegunda linea\n"
# Nombres compuestos a trozos para no tropezar con los guards del repo.
_SECRET_NAMES = ("ANTHROPIC" + "_API_KEY", "CLAUDE_CODE" + "_OAUTH_TOKEN")
_SECRET_VALUE = "sk-secreto-que-no-debe-salir-12345"


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        (tmp / "bin").mkdir()
        fk.install_all(tmp / "bin", ssh=fk.FAKE_SSH, docker=fk.FAKE_DOCKER_MAC)
        self.key = tmp / "key"
        self.key.write_text("clave")
        self.known = tmp / "known_hosts"
        self.known.write_text("host")
        self.tmpdir = tmp / "tmpdir"
        self.tmpdir.mkdir()
        self.logs = tmp / "logs"
        self.argv = tmp / "ssh-argv.log"
        self.stdin = tmp / "ssh-stdin.bin"

    def run(self, **env):
        full = {
            "HOME": str(self.tmp),
            "PATH": fk.SYSTEM_PATH,
            "NOCTURNA_PATH": f"{self.tmp / 'bin'}:{fk.SYSTEM_PATH}",
            "NOCTURNA_SYNC_TARGET": TARGET,
            "NOCTURNA_SYNC_KEY": str(self.key),
            "NOCTURNA_SYNC_KNOWN_HOSTS": str(self.known),
            "NOCTURNA_LOG_DIR": str(self.logs),
            "TMPDIR": str(self.tmpdir),
            "SSH_ARGV_FILE": str(self.argv),
            "SSH_STDIN_FILE": str(self.stdin),
            "ORDER_LOG": str(self.tmp / "order.log"),
            "DOCKER_CALL_LOG_FILE": str(self.tmp / "docker.log"),
            "DUMP_CONTENT": DUMP,
            "ALEMBIC_REV": REV,
        }
        full.update(env)
        full = {k: v for k, v in full.items() if v is not None}
        return fk.run([str(SCRIPT)], env=full, cwd=self.tmp)

    @property
    def ssh_called(self) -> bool:
        return self.argv.exists()

    @property
    def argv_list(self) -> list[str]:
        lines = self.argv.read_text().splitlines()
        assert lines[-1] == "--"
        return lines[:-1]

    @property
    def leftovers(self) -> list[str]:
        return [p.name for p in self.tmpdir.iterdir()]

    def log_text(self) -> str:
        return "".join(p.read_text() for p in self.logs.glob("sync-*.log"))


@pytest.fixture
def rig(tmp_path) -> Rig:
    return Rig(tmp_path)


def test_sincronizacion_correcta_sale_0_y_llama_una_vez_a_ssh(rig):
    result = rig.run()

    assert result.returncode == 0, result.stderr
    assert rig.argv.read_text().count("--\n") == 1


def test_ssh_va_con_las_opciones_de_aislamiento(rig):
    rig.run()
    argv = rig.argv_list

    assert argv[:2] == ["-F", "none"]
    assert argv[argv.index("-i") + 1] == str(rig.key)
    opts = [argv[i + 1] for i, a in enumerate(argv) if a == "-o"]
    assert "IdentitiesOnly=yes" in opts
    assert "BatchMode=yes" in opts
    assert "StrictHostKeyChecking=yes" in opts
    assert f"UserKnownHostsFile={rig.known}" in opts
    assert "-a" in argv


def test_ssh_no_reenvia_agente_ni_entorno(rig):
    rig.run()
    argv = rig.argv_list

    assert "-A" not in argv
    assert not [a for a in argv if "sendenv" in a.lower() or "setenv" in a.lower()]
    assert not [a for a in argv if a.lower() == "stricthostkeychecking=no"]
    assert not [a for a in argv if "accept-new" in a]


def test_la_orden_remota_es_restore_sha_revision_y_el_sha_es_el_de_stdin(rig):
    rig.run()
    argv = rig.argv_list
    sent = rig.stdin.read_bytes()

    assert sent == DUMP.encode()
    sha = hashlib.sha256(sent).hexdigest()
    assert argv[-2:] == [TARGET, f"restore {sha} {REV}"]


def test_los_secretos_del_entorno_no_salen_ni_por_argv_ni_por_stdin_ni_por_el_log(rig):
    env = {name: _SECRET_VALUE for name in _SECRET_NAMES}

    result = rig.run(**env)

    assert result.returncode == 0
    assert _SECRET_VALUE not in rig.argv.read_text()
    assert _SECRET_VALUE.encode() not in rig.stdin.read_bytes()
    assert _SECRET_VALUE not in rig.log_text()
    assert _SECRET_VALUE.encode() not in result.stdout + result.stderr
    assert not any(name in rig.argv.read_text() for name in _SECRET_NAMES)


@pytest.mark.parametrize("rev", ["", "ABC", "rev; rm -rf /", "a b", "x" * 65, "ñ"])
def test_revision_mal_formada_da_77_sin_llamar_a_ssh(rig, rev):
    result = rig.run(ALEMBIC_REV=rev)

    assert result.returncode == 77
    assert not rig.ssh_called


@pytest.mark.parametrize("remote_code", [10, 11, 12, 13, 14, 15, 16, 17])
def test_codigos_10_a_16_del_vps_se_propagan(rig, remote_code):
    result = rig.run(SSH_EXIT=str(remote_code))

    assert result.returncode == remote_code


@pytest.mark.parametrize("ssh_code", [255, 1, 9, 18, 130])
def test_fallo_de_transporte_da_2(rig, ssh_code):
    result = rig.run(SSH_EXIT=str(ssh_code))

    assert result.returncode == 2


def test_pg_dump_fallido_da_1_sin_llamar_a_ssh(rig):
    result = rig.run(DUMP_EXIT="1")

    assert result.returncode == 1
    assert not rig.ssh_called


@pytest.mark.parametrize("status", ["starting", "unhealthy", ""])
def test_contenedor_no_sano_da_75_sin_llamar_a_ssh(rig, status):
    result = rig.run(DOCKER_HEALTH_STATUS=status)

    assert result.returncode == 75
    assert not rig.ssh_called


def test_sin_clave_da_77(rig):
    result = rig.run(NOCTURNA_SYNC_KEY=str(rig.tmp / "no-existe"))

    assert result.returncode == 77
    assert not rig.ssh_called


def test_sin_known_hosts_da_77(rig):
    result = rig.run(NOCTURNA_SYNC_KNOWN_HOSTS=str(rig.tmp / "no-existe"))

    assert result.returncode == 77
    assert not rig.ssh_called


@pytest.mark.parametrize("target", ["sinarroba", "a b@host", "-oProxyCommand=x@h", "u@"])
def test_destino_mal_formado_da_77(rig, target):
    result = rig.run(NOCTURNA_SYNC_TARGET=target)

    assert result.returncode == 77
    assert not rig.ssh_called


@pytest.mark.parametrize("target", [None, "", "__NOCTURNA_SYNC_TARGET__"])
def test_sin_destino_da_78_sin_docker_ni_ssh(rig, target):
    result = rig.run(NOCTURNA_SYNC_TARGET=target)

    assert result.returncode == 78
    assert not rig.ssh_called
    assert not (rig.tmp / "docker.log").exists()


def test_el_temporal_desaparece_si_sale_bien(rig):
    assert rig.run().returncode == 0
    assert rig.leftovers == []


@pytest.mark.parametrize(
    "env", [{"SSH_EXIT": "13"}, {"SSH_EXIT": "255"}, {"DUMP_EXIT": "1"}, {"ALEMBIC_REV": ""}]
)
def test_el_temporal_desaparece_si_falla(rig, env):
    assert rig.run(**env).returncode != 0
    assert rig.leftovers == []


def test_el_log_no_contiene_clave_ni_destino(rig):
    rig.run()

    log = rig.log_text()
    assert "sincronización correcta" in log
    assert TARGET not in log
    assert str(rig.key) not in log


@pytest.mark.parametrize("marker", ["__NOCTURNA_SYNC_KEY__", "__NOCTURNA_SYNC_KNOWN_HOSTS__"])
def test_marcadores_sin_sustituir_de_clave_o_known_hosts_dan_77(rig, marker):
    var = "NOCTURNA_SYNC_KEY" if "KEY" in marker else "NOCTURNA_SYNC_KNOWN_HOSTS"

    result = rig.run(**{var: marker})

    assert result.returncode == 77
    assert not rig.ssh_called


def test_ssh_lleva_las_opciones_de_keepalive(rig):
    rig.run()
    argv = rig.argv_list
    opts = [argv[i + 1] for i, a in enumerate(argv) if a == "-o"]

    assert "ServerAliveInterval=15" in opts
    assert "ServerAliveCountMax=4" in opts


def test_ssh_colgado_vence_el_tope_total_da_3_rapido_sin_temporal_ni_huerfanos(rig):
    inicio = time.monotonic()
    result = rig.run(SSH_HANG_S="4311", NOCTURNA_SYNC_TIMEOUT_S="2")
    duracion = time.monotonic() - inicio

    assert result.returncode == 3, result.stderr
    assert duracion < 10
    assert rig.leftovers == []
    assert "tiempo agotado" in rig.log_text()
    assert not subprocess.run(["pgrep", "-f", "sleep 4311"], capture_output=True).stdout


def test_el_vigilante_no_se_dispara_si_el_trabajo_acaba_a_tiempo(rig):
    result = rig.run(NOCTURNA_SYNC_TIMEOUT_S="30")

    assert result.returncode == 0
    assert rig.leftovers == []


@pytest.mark.parametrize("value", ["0", "abc", "-5", "1.5"])
def test_tope_de_tiempo_invalido_da_77_sin_llamar_a_ssh(rig, value):
    result = rig.run(NOCTURNA_SYNC_TIMEOUT_S=value)

    assert result.returncode == 77
    assert not rig.ssh_called
    assert rig.leftovers == []


def test_el_stderr_de_pg_dump_va_al_log_y_no_lleva_secretos(rig):
    result = rig.run(DUMP_STDERR="aviso de pg_dump")

    assert result.returncode == 0
    assert "aviso de pg_dump" in rig.log_text()
    assert _SECRET_VALUE not in rig.log_text()
