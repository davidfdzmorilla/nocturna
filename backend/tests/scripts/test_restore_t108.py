"""T108 (C): `deploy/sync/restore.sh` con `docker` y `flock` falsos.

En este Mac no existe `flock`: el falso sale con `FLOCK_EXIT`. Sin Docker real, sin red, sin
Claude. Nombres en espanol.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from helpers import sync_fakes as fk

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy" / "sync" / "restore.sh"
REV = "b7e3a1c9d482"
DUMP = b"PGDMP\x00\x01binario\xff\n" * 3
PG_RESTORE_FLAGS = (
    "pg_restore -U nocturna -d nocturna --clean --if-exists --no-owner --no-privileges "
    "--single-transaction --exit-on-error"
)
PG_RESTORE_PREFIX = "exec -T -e PGOPTIONS=-c lock_timeout=60s postgres "
ROLE_CALL = "exec -T postgres sh /docker-entrypoint-initdb.d/10-api-readonly-role.sh"


def _cmd(dump: bytes = DUMP, rev: str = REV) -> str:
    return f"restore {hashlib.sha256(dump).hexdigest()} {rev}"


class Rig:
    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        fk.install_all(
            tmp / "bin", docker=fk.FAKE_DOCKER_VPS, flock=fk.FAKE_FLOCK, timeout=fk.FAKE_TIMEOUT
        )
        self.tmpdir = tmp / "tmpdir"
        self.tmpdir.mkdir()
        self.order_file = tmp / "order.log"
        self.stdin_file = tmp / "pg-restore-stdin.bin"

    def run(self, command: str | None, *, stdin: bytes = DUMP, **env):
        full = {
            "HOME": str(self.tmp),
            "PATH": f"{self.tmp / 'bin'}:{fk.SYSTEM_PATH}",
            "NOCTURNA_SYNC_LOCK": str(self.tmp / "restore.lock"),
            "NOCTURNA_SYNC_EXPECTED_HEAD": REV,
            "TMPDIR": str(self.tmpdir),
            "ORDER_LOG": str(self.order_file),
            "DOCKER_CALL_LOG_FILE": str(self.tmp / "docker.log"),
            "PG_RESTORE_STDIN_FILE": str(self.stdin_file),
            "TIMEOUT_LOG_FILE": str(self.tmp / "timeout.log"),
            "DOCKER_ENV_SEEN_FILE": str(self.tmp / "env-seen.log"),
        }
        if command is not None:
            full["SSH_ORIGINAL_COMMAND"] = command
        full.update(env)
        full = {k: v for k, v in full.items() if v is not None}
        return fk.run([str(SCRIPT)], env=full, cwd=self.tmp, stdin=stdin)

    @property
    def timeouts(self) -> list[str]:
        f = self.tmp / "timeout.log"
        return f.read_text().splitlines() if f.exists() else []

    @property
    def order(self) -> list[str]:
        return self.order_file.read_text().splitlines() if self.order_file.exists() else []

    @property
    def docker_calls(self) -> list[str]:
        return [ln for ln in self.order if ln.startswith("docker ")]

    def pg_restore_calls(self) -> list[str]:
        return [ln for ln in self.docker_calls if "pg_restore" in ln]

    def role_calls(self) -> list[str]:
        return [ln for ln in self.docker_calls if "10-api-readonly-role.sh" in ln]


@pytest.fixture
def rig(tmp_path) -> Rig:
    return Rig(tmp_path)


def test_el_script_es_ejecutable():
    assert SCRIPT.stat().st_mode & 0o111


def test_restauracion_correcta_sale_0_y_pasa_el_volcado_a_pg_restore(rig):
    result = rig.run(_cmd())

    assert result.returncode == 0, result.stderr
    assert rig.stdin_file.read_bytes() == DUMP
    assert len(rig.pg_restore_calls()) == 1 and len(rig.role_calls()) == 1


def test_los_flags_de_pg_restore_son_exactamente_los_acordados(rig):
    rig.run(_cmd())

    (call,) = rig.pg_restore_calls()
    assert call.endswith(f"{PG_RESTORE_PREFIX}{PG_RESTORE_FLAGS}")
    assert "-f " in call and "deploy/compose.yml" in call


def test_el_rol_se_aplica_siempre_despues_de_pg_restore(rig):
    rig.run(_cmd())

    docker = rig.docker_calls
    restore_idx = next(i for i, c in enumerate(docker) if "pg_restore" in c)
    role_idx = next(i for i, c in enumerate(docker) if "10-api-readonly-role.sh" in c)
    assert restore_idx < role_idx
    assert docker[role_idx].endswith(ROLE_CALL)


@pytest.mark.parametrize(
    "command",
    [
        None,
        "",
        "restore",
        f"restore {'a' * 64}",
        f"restore {'a' * 64} {REV} extra",
        f"restore {'a' * 64} {REV};rm -rf /",
        f"restore {'a' * 64} {REV}; rm -rf /",
        "restore x; rm -rf /",
        f"restore {'A' * 64} {REV}",
        f"restore {'a' * 63} {REV}",
        f"restore {'a' * 65} {REV}",
        f"restore {'a' * 64} {REV.upper()}",
        f"restore {'a' * 64} {'x' * 65}",
        f"restore {'a' * 64} $(id)",
        f"restore {'a' * 64} `id`",
        f"restore {'a' * 64} {REV}\n",
        f"restore {'a' * 64} {REV}\nrm -rf /",
        f"restore {'a' * 64}\n{REV}",
        f"x\nrestore {'a' * 64} {REV}",
        f" restore {'a' * 64} {REV}",
        f"restore  {'a' * 64} {REV}",
        f"RESTORE {'a' * 64} {REV}",
        f"restore {'a' * 64} {REV}-x",
        f"scp {'a' * 64} {REV}",
    ],
    ids=lambda c: repr(c)[:50],
)
def test_orden_invalida_da_10_sin_llamar_a_docker(rig, command):
    result = rig.run(command)

    assert result.returncode == 10
    assert rig.docker_calls == []
    assert not rig.stdin_file.exists()


def test_sha_distinto_da_11_sin_pg_restore(rig):
    result = rig.run(_cmd(b"otro volcado"))

    assert result.returncode == 11
    assert rig.pg_restore_calls() == []
    assert rig.role_calls() == []


def test_revision_distinta_da_12_sin_pg_restore(rig):
    result = rig.run(_cmd(rev="0000000000aa"))

    assert result.returncode == 12
    assert rig.pg_restore_calls() == []
    assert rig.role_calls() == []


def test_sin_expected_head_se_pregunta_a_alembic_con_el_servicio_migrate(rig):
    result = rig.run(
        _cmd(), NOCTURNA_SYNC_EXPECTED_HEAD=None, HEADS_OUTPUT=f"INFO noise\n{REV} (head)\n"
    )

    assert result.returncode == 0, result.stderr
    heads = [c for c in rig.docker_calls if "alembic heads" in c]
    assert len(heads) == 1 and "--profile ops run --rm -T --no-deps migrate" in heads[0]


@pytest.mark.parametrize(
    "heads_output", ["", "garbage\n", f"{REV} (head)\naaaaaaaaaaaa (head)\n", "b7e3a1c9d482\n"]
)
def test_sin_una_unica_head_de_alembic_da_12(rig, heads_output):
    result = rig.run(_cmd(), NOCTURNA_SYNC_EXPECTED_HEAD=None, HEADS_OUTPUT=heads_output)

    assert result.returncode == 12
    assert rig.pg_restore_calls() == []


def test_pg_restore_fallido_da_13_sin_aplicar_el_rol(rig):
    result = rig.run(_cmd(), PG_RESTORE_EXIT="1")

    assert result.returncode == 13
    assert len(rig.pg_restore_calls()) == 1
    assert rig.role_calls() == []


def test_rol_fallido_da_14(rig):
    result = rig.run(_cmd(), ROLE_EXIT="1")

    assert result.returncode == 14
    assert len(rig.role_calls()) == 1


def test_lock_ocupado_da_15_sin_llamar_a_docker(rig):
    result = rig.run(_cmd(), FLOCK_EXIT="1")

    assert result.returncode == 15
    assert rig.docker_calls == []
    assert any(ln.startswith("flock -n") for ln in rig.order)


def test_el_lock_se_comprueba_antes_que_la_orden(rig):
    result = rig.run("basura", FLOCK_EXIT="1")

    assert result.returncode == 15


def test_el_lock_por_defecto_vive_en_deploy_sync(rig):
    text = SCRIPT.read_text()

    assert "${NOCTURNA_SYNC_LOCK:-${script_dir}/.restore.lock}" in text


def test_por_encima_del_tope_da_16_sin_llamar_a_docker(rig):
    result = rig.run(_cmd(), NOCTURNA_SYNC_MAX_BYTES=str(len(DUMP) - 1))

    assert result.returncode == 16
    assert rig.docker_calls == []


def test_justo_en_el_tope_se_acepta(rig):
    result = rig.run(_cmd(), NOCTURNA_SYNC_MAX_BYTES=str(len(DUMP)))

    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("value", ["abc", "-1", "1.5"])
def test_tope_invalido_da_16(rig, value):
    result = rig.run(_cmd(), NOCTURNA_SYNC_MAX_BYTES=value)

    assert result.returncode == 16
    assert rig.docker_calls == []


@pytest.mark.parametrize(
    "env",
    [
        {},
        {"PG_RESTORE_EXIT": "1"},
        {"ROLE_EXIT": "1"},
        {"NOCTURNA_SYNC_MAX_BYTES": "1"},
    ],
)
def test_el_temporal_del_volcado_desaparece(rig, env):
    rig.run(_cmd(), **env)

    assert list(rig.tmpdir.iterdir()) == []


def test_la_orden_no_se_evalua(rig, tmp_path):
    marker = tmp_path / "pwned"
    rig.run(f"restore {'a' * 64} $(touch {marker})")

    assert not marker.exists()


# --- Topes de tiempo (coreutils `timeout`) y fallo interno -------------------------------


def test_pg_restore_va_con_timeout_de_600_por_defecto_y_el_rol_con_120(rig):
    assert rig.run(_cmd()).returncode == 0

    pg = [t for t in rig.timeouts if "pg_restore" in t]
    role = [t for t in rig.timeouts if "10-api-readonly-role.sh" in t]
    assert len(pg) == 1 and pg[0].startswith("timeout 600 docker compose")
    assert len(role) == 1 and role[0].startswith("timeout 120 docker compose")


def test_el_tope_de_pg_restore_se_configura(rig):
    rig.run(_cmd(), NOCTURNA_SYNC_RESTORE_TIMEOUT_S="45")

    assert [t for t in rig.timeouts if "pg_restore" in t][0].startswith("timeout 45 ")


def test_alembic_heads_va_con_timeout_de_120(rig):
    rig.run(_cmd(), NOCTURNA_SYNC_EXPECTED_HEAD=None, HEADS_OUTPUT=f"{REV} (head)\n")

    heads = [t for t in rig.timeouts if "alembic heads" in t]
    assert len(heads) == 1 and heads[0].startswith("timeout 120 docker compose")


def test_pg_restore_recibe_lock_timeout_dentro_del_contenedor(rig):
    rig.run(_cmd())

    seen = (rig.tmp / "env-seen.log").read_text().splitlines()
    assert seen == ["PGOPTIONS=-c lock_timeout=60s"]


@pytest.mark.parametrize(
    ("fires", "code"),
    [("alembic heads", 12), ("pg_restore", 13), ("10-api-readonly-role.sh", 14)],
)
def test_un_vencimiento_de_timeout_da_12_13_o_14_segun_el_paso(rig, fires, code):
    env = {"TIMEOUT_FIRES": fires}
    if fires == "alembic heads":
        env.update(NOCTURNA_SYNC_EXPECTED_HEAD=None, HEADS_OUTPUT=f"{REV} (head)\n")

    result = rig.run(_cmd(), **env)

    assert result.returncode == code
    assert b"tope" in result.stderr
    if code != 14:
        assert rig.role_calls() == []
    if code == 12:
        assert rig.pg_restore_calls() == []


@pytest.mark.parametrize("value", ["0", "abc", "-1", "1.5"])
def test_tope_de_pg_restore_invalido_da_17_sin_llamar_a_docker(rig, value):
    result = rig.run(_cmd(), NOCTURNA_SYNC_RESTORE_TIMEOUT_S=value)

    assert result.returncode == 17
    assert rig.docker_calls == []


def test_fallo_interno_al_abrir_el_lock_da_17(rig):
    result = rig.run(_cmd(), NOCTURNA_SYNC_LOCK=str(rig.tmp / "no-existe" / "restore.lock"))

    assert result.returncode == 17
    assert rig.docker_calls == []


def test_fallo_interno_al_crear_el_temporal_da_17(rig):
    result = rig.run(_cmd(), TMPDIR=str(rig.tmp / "no-existe"))

    assert result.returncode == 17
    assert rig.docker_calls == []
