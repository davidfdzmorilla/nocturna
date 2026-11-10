"""T108 (G): integracion de la sincronizacion con dos PostgreSQL reales y desechables.

Va tras `NOCTURNA_CHECK_SYNC=1` y el marcador `docker`:

    NOCTURNA_CHECK_SYNC=1 uv run pytest -m docker -q tests/deploy/test_sync_integration_t108.py

- Origen: `postgres:16-alpine` desechable con nombre unico (`nocturna-t108-src-<hex>`), migrado
  a head con `alembic upgrade head` y con datos.
- Destino: el servicio `postgres` de `deploy/compose.yml` en el proyecto `nocturna-t108-test`,
  con contrasenas generadas aqui y pasadas por entorno; nunca por fichero.
- `ssh` es un script falso que ejecuta `deploy/sync/restore.sh` en local con
  `SSH_ORIGINAL_COMMAND`; `flock` tambien es falso (el Mac no lo trae).

No toca `nocturna-postgres` ni su volumen, ni la base `nocturna`. Al terminar baja el destino con
`down -v` y borra el origen, en un `finally`. Ningun test llama a Claude.
"""

from __future__ import annotations

import difflib
import hashlib
import os
import re
import secrets
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[2]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND / "tests" / "db"))
sys.path.insert(0, str(BACKEND / "tests"))

from helpers import sync_fakes as fk  # noqa: E402

COMPOSE = ROOT / "deploy" / "compose.yml"
SYNC_SH = BACKEND / "scripts" / "sync-to-vps.sh"
RESTORE_SH = ROOT / "deploy" / "sync" / "restore.sh"
PROJECT = "nocturna-t108-test"


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    r = subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=60)
    return r.returncode == 0


pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("NOCTURNA_CHECK_SYNC") != "1",
        reason="levanta dos PostgreSQL con Docker; activar con NOCTURNA_CHECK_SYNC=1",
    ),
]

_FAKE_SSH_TO_RESTORE = """#!/usr/bin/env bash
# ssh falso: el ultimo argumento es la orden remota; se ejecuta restore.sh con ella como
# SSH_ORIGINAL_COMMAND, igual que haria sshd con una orden forzada.
last="${@: -1}"
SSH_ORIGINAL_COMMAND="$last" exec "$RESTORE_SH"
"""


def _run(cmd, *, env=None, input=None, timeout=300, check=True):
    r = subprocess.run(cmd, capture_output=True, text=True, env=env, input=input, timeout=timeout)
    if check and r.returncode != 0:
        raise AssertionError(
            f"{' '.join(map(str, cmd))[:300]}\nrc={r.returncode}\n"
            f"{r.stdout[-2000:]}\n{r.stderr[-2000:]}"
        )
    return r


class Lab:
    """Origen y destino desechables, con todo lo necesario para sincronizar y comparar."""

    def __init__(self, tmp: Path) -> None:
        self.tmp = tmp
        uid = secrets.token_hex(4)
        self.src = f"nocturna-t108-src-{uid}"
        self.pg_pw = secrets.token_hex(12)
        self.ro_pw = secrets.token_hex(12)
        self.src_pw = secrets.token_hex(12)
        self.base_env = {
            k: os.environ[k]
            for k in ("HOME", "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG")
            if k in os.environ
        }
        self.dst_env = {
            **self.base_env,
            # restore.sh usa `timeout` (coreutils; en el Mac, el de Homebrew).
            "PATH": (
                f"{Path(shutil.which('docker') or 'docker').parent}:"
                f"{Path(shutil.which('timeout') or 'timeout').parent}:{fk.SYSTEM_PATH}"
            ),
            "NOCTURNA_DOMAIN": "nocturna.test",
            "NOCTURNA_ACME_EMAIL": "test@nocturna.test",
            "POSTGRES_PASSWORD": self.pg_pw,
            "NOCTURNA_API_DB_PASSWORD": self.ro_pw,
        }
        self.port = 0
        self.head = ""

    # --- docker auxiliares ---------------------------------------------------------------
    def dst(self, *args, input=None, check=True, timeout=300):
        cmd = ["docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), *args]
        return _run(cmd, env=self.dst_env, input=input, check=check, timeout=timeout)

    def src_psql(self, sql: str) -> str:
        r = _run(
            ["docker", "exec", self.src, "psql", "-U", "nocturna", "-d", "nocturna", "-At",
             "-v", "ON_ERROR_STOP=1", "-c", sql]
        )  # fmt: skip
        return r.stdout.strip()

    def dst_psql(self, sql: str) -> str:
        r = self.dst("exec", "-T", "postgres", "psql", "-U", "nocturna", "-d", "nocturna", "-At",
                     "-v", "ON_ERROR_STOP=1", "-c", sql)  # fmt: skip
        return r.stdout.strip()

    def ro(self, *sqls: str, check=True) -> subprocess.CompletedProcess:
        """`nocturna_ro` por TCP (127.0.0.1 dentro del contenedor: exige contrasena).

        Cada SQL va en su propio `-c`, es decir, en su propia transaccion."""
        flags = [a for sql in sqls for a in ("-c", sql)]
        return self.dst(
            "exec", "-T", "-e", f"PGPASSWORD={self.ro_pw}", "postgres",
            "psql", "-h", "127.0.0.1", "-U", "nocturna_ro", "-d", "nocturna", "-At",
            "-v", "ON_ERROR_STOP=1", *flags, check=check,
        )  # fmt: skip

    # --- ciclo de vida ----------------------------------------------------------------------
    def up(self) -> None:
        _run(
            ["docker", "run", "-d", "--name", self.src,
             "-e", "POSTGRES_USER=nocturna", "-e", "POSTGRES_DB=nocturna",
             "-e", f"POSTGRES_PASSWORD={self.src_pw}",
             "--health-cmd", "pg_isready -h 127.0.0.1 -U nocturna -d nocturna",
             "--health-interval", "2s", "--health-retries", "30",
             "-p", "127.0.0.1::5432", "postgres:16-alpine"]
        )  # fmt: skip
        # Sin `--wait`: el healthcheck del compose no tiene `start_period` y, con un initdb lento,
        # agota sus 5 reintentos (50 s) antes de que el servidor acepte conexiones. Se sondea aqui.
        self.dst("up", "-d", "postgres", timeout=300)
        deadline = time.monotonic() + 240
        while time.monotonic() < deadline:
            ready = self.dst(
                "exec", "-T", "postgres", "pg_isready", "-h", "127.0.0.1", "-U", "nocturna",
                "-d", "nocturna", check=False,
            )  # fmt: skip
            if ready.returncode == 0:
                break
            time.sleep(2)
        else:
            logs = self.dst("logs", "--no-color", "postgres", check=False).stdout[-3000:]
            raise AssertionError(f"el destino no acepto conexiones\n{logs}")
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            health = _run(
                ["docker", "inspect", "-f", "{{.State.Health.Status}}", self.src], check=False
            ).stdout.strip()
            if health == "healthy":
                break
            time.sleep(1)
        else:
            raise AssertionError("el origen no llego a healthy")
        mapping = _run(["docker", "port", self.src, "5432/tcp"]).stdout.split()[0]
        self.port = int(mapping.rsplit(":", 1)[1])

    def down(self) -> None:
        _run(["docker", "rm", "-f", "-v", self.src], check=False)
        self.dst("down", "-v", "--remove-orphans", check=False)
        vols = _run(
            [
                "docker",
                "volume",
                "ls",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={PROJECT}",
            ],
            check=False,
        ).stdout.split()
        for v in vols:
            assert v.startswith(PROJECT), v  # nunca un volumen ajeno
            _run(["docker", "volume", "rm", v], check=False)

    # --- origen: migracion y datos -----------------------------------------------------------
    @property
    def src_url(self) -> str:
        return f"postgresql+psycopg://nocturna:{self.src_pw}@127.0.0.1:{self.port}/nocturna"

    def migrate_and_seed(self) -> None:
        env = {**self.base_env, "PATH": os.environ["PATH"], "NOCTURNA_DATABASE_URL": self.src_url}
        _run([sys.executable, "-m", "alembic", "upgrade", "head"], env={**env}, timeout=300)  # noqa: S603
        self.head = self.src_psql("select version_num from alembic_version")
        self._seed()

    def _seed(self) -> None:
        import sqlalchemy as sa
        from factories import make_agent_call, make_finding, make_item, make_reading, make_run
        from helpers.archive_digest import digest_text
        from sqlalchemy.orm import sessionmaker
        from test_archive_repository import save, snapshot

        from nocturna.domain.entities import RunStatus
        from nocturna.infrastructure.db.repositories import (
            SqlAlchemyAgentCallRepository,
            SqlAlchemyArchiveDigestTextRepository,
            SqlAlchemyArchiveRepository,
            SqlAlchemyFindingRepository,
            SqlAlchemyItemRepository,
            SqlAlchemyReadingRepository,
            SqlAlchemyRunRepository,
        )
        from nocturna.infrastructure.db.session import unit_of_work

        engine = sa.create_engine(self.src_url)
        try:
            factory = sessionmaker(engine, expire_on_commit=False)
            now = datetime(2026, 11, 8, 2, 30, tzinfo=UTC)
            with unit_of_work(factory) as s:
                run = make_run(started_at=now - timedelta(hours=3))
                SqlAlchemyRunRepository(s).add(run)
                item = make_item(external_id="2611.00001")
                SqlAlchemyItemRepository(s).add_many([item])
                SqlAlchemyReadingRepository(s).add(make_reading(item.id))
                SqlAlchemyAgentCallRepository(s).add(make_agent_call(run.id))
                finding = make_finding(item.id, run.id, title="Hallazgo sincronizado ñá")
                SqlAlchemyFindingRepository(s).add(finding)
                finding.publish(confidence=0.8, at=now)
                SqlAlchemyFindingRepository(s).save(finding)
                run.finish(RunStatus.COMPLETED, now)
                SqlAlchemyRunRepository(s).save(run)
                save(SqlAlchemyArchiveRepository(s), snapshot(offset_days=3), [])
                s.flush()
                SqlAlchemyArchiveDigestTextRepository(s).add(
                    digest_text("2026-W45", run.id, status="failed")
                )
        finally:
            engine.dispose()

    def src_freshness(self):
        import sqlalchemy as sa
        from sqlalchemy.orm import sessionmaker

        from nocturna.infrastructure.db.repositories import SqlAlchemyDataFreshnessReader

        engine = sa.create_engine(self.src_url)
        try:
            with sessionmaker(engine)() as s:
                return SqlAlchemyDataFreshnessReader(s).last_update()
        finally:
            engine.dispose()

    # --- huella ---------------------------------------------------------------------------------
    @staticmethod
    def _norm(schema_sql: str) -> str:
        """Quita comentarios y metacomandos y unifica el deparse de los CHECK con `ANY`.

        pg_dump imprime `= ANY ((ARRAY[...])::text[])` en la base creada con Alembic y
        `= ANY (ARRAY[(...)::text, ...])` en la restaurada: la misma restriccion reinterpretada al
        recargar el texto. Se compara la forma canonica (la primera).
        """
        keep = [
            ln
            for ln in schema_sql.splitlines()
            if ln.strip() and not ln.startswith("--") and not ln.startswith("\\")
        ]
        text = "\n".join(keep)
        text = re.sub(
            r"\('([^']*)'::character varying\)::text",
            lambda m: f"'{m[1]}'::character varying",
            text,
        )
        return re.sub(r"ANY \(ARRAY\[(.*?)\]\)", r"ANY ((ARRAY[\1])::text[])", text)

    def _fingerprint(self, psql, dump_schema) -> dict:
        tables = psql(
            "select tablename from pg_tables where schemaname='public' order by 1"
        ).split()
        assert "findings" in tables and "alembic_version" in tables
        rows = {}
        for t in tables:
            rows[t] = psql(
                "select count(*) || ':' || "
                f"md5(coalesce(string_agg(x::text, E'\\n' order by x::text), '')) "
                f'from public."{t}" x'
            )
        return {"schema": self._norm(dump_schema()), "rows": rows}

    def src_fingerprint(self) -> dict:
        return self._fingerprint(
            self.src_psql,
            lambda: (
                _run(
                    [
                        "docker",
                        "exec",
                        self.src,
                        "pg_dump",
                        "-U",
                        "nocturna",
                        "-d",
                        "nocturna",
                        "--schema-only",
                        "--no-owner",
                        "--no-privileges",
                    ]
                ).stdout
            ),  # fmt: skip
        )

    def dst_fingerprint(self) -> dict:
        return self._fingerprint(
            self.dst_psql,
            lambda: (
                self.dst(
                    "exec",
                    "-T",
                    "postgres",
                    "pg_dump",
                    "-U",
                    "nocturna",
                    "-d",
                    "nocturna",
                    "--schema-only",
                    "--no-owner",
                    "--no-privileges",
                ).stdout
            ),  # fmt: skip
        )

    # --- sincronizacion ---------------------------------------------------------------------------
    def _bin(self) -> Path:
        bin_dir = self.tmp / "bin"
        if not bin_dir.exists():
            fk.install_all(bin_dir, ssh=_FAKE_SSH_TO_RESTORE, flock=fk.FAKE_FLOCK)
        return bin_dir

    def _sync_env(self) -> dict:
        key = self.tmp / "key"
        key.write_text("k")
        known = self.tmp / "known_hosts"
        known.write_text("h")
        docker_dir = str(Path(shutil.which("docker")).parent)
        return {
            **self.dst_env,
            "NOCTURNA_PATH": f"{self._bin()}:{docker_dir}:{fk.SYSTEM_PATH}",
            "NOCTURNA_SYNC_TARGET": "sync@vps.test",
            "NOCTURNA_SYNC_KEY": str(key),
            "NOCTURNA_SYNC_KNOWN_HOSTS": str(known),
            "NOCTURNA_SYNC_SOURCE_CONTAINER": self.src,
            "NOCTURNA_LOG_DIR": str(self.tmp / "logs"),
            "TMPDIR": str(self.tmp),
            "RESTORE_SH": str(RESTORE_SH),
            "NOCTURNA_SYNC_LOCK": str(self.tmp / "restore.lock"),
            "NOCTURNA_SYNC_EXPECTED_HEAD": self.head,
            "NOCTURNA_COMPOSE_PROJECT": PROJECT,
        }

    def sync(self) -> subprocess.CompletedProcess:
        return _run([str(SYNC_SH)], env=self._sync_env(), check=False, timeout=600)

    def restore_raw(self, dump: bytes, *, sha: str | None = None) -> subprocess.CompletedProcess:
        """`restore.sh` directo con un volcado y sha dados (para volcados corruptos)."""
        env = self._sync_env()
        env["PATH"] = env["NOCTURNA_PATH"]  # sync-to-vps.sh lo hace por su cuenta
        sha = sha or hashlib.sha256(dump).hexdigest()
        env["SSH_ORIGINAL_COMMAND"] = f"restore {sha} {self.head}"
        r = subprocess.run([str(RESTORE_SH)], env=env, input=dump, capture_output=True, timeout=600)
        return r

    def src_dump(self) -> bytes:
        r = subprocess.run(
            ["docker", "exec", self.src, "pg_dump", "-U", "nocturna", "-d", "nocturna", "-Fc",
             "--no-owner", "--no-privileges"],
            capture_output=True, timeout=300, check=True,
        )  # fmt: skip
        return r.stdout


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    if not _docker_available():
        pytest.skip("Docker no disponible")
    lab = Lab(tmp_path_factory.mktemp("t108"))
    try:
        lab.up()
        lab.migrate_and_seed()
        yield lab
    finally:
        lab.down()


def _same(actual: dict, expected: dict, label: str = "") -> None:
    assert actual["rows"] == expected["rows"], f"{label}: filas distintas"
    diff = "\n".join(
        difflib.unified_diff(
            expected["schema"].splitlines(),
            actual["schema"].splitlines(),
            "origen",
            "destino",
            lineterm="",
        )
    )
    assert actual["schema"] == expected["schema"], f"{label}: esquema distinto\n{diff}"


def _check_readonly_role(lab: Lab) -> None:
    tables = lab.dst_psql(
        "select tablename from pg_tables where schemaname='public' order by 1"
    ).split()
    assert tables
    for t in tables:  # SELECT en todas las tablas de public
        lab.ro(f'select count(*) from public."{t}"')
    # Sin escritura: la sesion es de solo lectura por defecto...
    for sql in (
        "insert into runs default values",
        "update items set title = title where false",
        "delete from findings where false",
    ):
        r = lab.ro(sql, check=False)
        assert r.returncode != 0, sql
        # ...y aunque se levante esa guarda, el rol no tiene privilegios de escritura.
        r2 = lab.ro("set default_transaction_read_only = off", sql, check=False)
        assert r2.returncode != 0 and "permission denied" in r2.stderr, (sql, r2.stderr)
    assert lab.ro("create temp table t108_tmp (a int)", check=False).returncode != 0
    flags = lab.ro(
        "select rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
        "from pg_roles where rolname = 'nocturna_ro'"
    ).stdout.strip()
    assert flags == "f|f|f|f|f"
    assert lab.ro("show default_transaction_read_only").stdout.strip() == "on"
    assert (
        "default_transaction_read_only=on"
        in lab.ro("select rolconfig from pg_roles where rolname = 'nocturna_ro'").stdout
    )
    priv = lab.ro(
        "select has_database_privilege('nocturna_ro', 'nocturna', 'TEMPORARY'), "
        "has_database_privilege('nocturna_ro', 'nocturna', 'CONNECT')"
    ).stdout.strip()
    assert priv == "f|t"


_FRESHNESS_SQL = (
    "select to_char(greatest((select max(finished_at) from runs), "
    "(select max(taken_at) from archive_snapshot)) at time zone 'UTC', "
    "'YYYY-MM-DD\"T\"HH24:MI:SS.US')"
)


def test_origen_tiene_datos_y_esta_migrado(lab):
    assert lab.head
    assert lab.src_psql("select count(*) from findings where published_at is not null") == "1"
    assert lab.src_psql("select count(*) from runs") == "1"
    assert lab.src_psql("select count(*) from agent_calls") == "1"
    assert lab.src_psql("select count(*) from archive_snapshot") == "1"
    assert lab.src_psql("select status from archive_digest_text") == "failed"


def test_restauraciones_encadenadas(lab):
    """1) igual al origen; 2) idempotente; 3) la deriva desaparece; 4) un volcado truncado no
    cambia nada; y tras cada paso, `nocturna_ro` conserva su configuracion."""
    source = lab.src_fingerprint()

    first = lab.sync()
    assert first.returncode == 0, (first.stdout, first.stderr)
    after_first = lab.dst_fingerprint()
    _same(after_first, source, "primera restauracion")
    _check_readonly_role(lab)

    second = lab.sync()
    assert second.returncode == 0, (second.stdout, second.stderr)
    assert lab.dst_fingerprint() == after_first == source
    _check_readonly_role(lab)

    # Deriva en el destino: una fila de mas, una fila cambiada y filas borradas.
    lab.dst_psql(
        "insert into archive_snapshot (id, taken_at, kind, max_releasedate, rows_total, "
        "defaults_total, duplicate_rows, payload_sha256, requests, duration_ms) values "
        "(gen_random_uuid(), now(), 'incremental', null, 0, 0, 0, repeat('b', 64), 0, 0)"
    )
    lab.dst_psql("update items set title = 'DERIVA'")
    lab.dst_psql("delete from agent_calls")
    assert lab.dst_fingerprint() != source

    third = lab.sync()
    assert third.returncode == 0, (third.stdout, third.stderr)
    assert lab.dst_psql("select count(*) from archive_snapshot") == "1"
    assert lab.dst_fingerprint() == source
    _check_readonly_role(lab)

    # Volcado truncado con sha coherente: llega a pg_restore, que falla (13) y la transaccion unica
    # deja el destino como estaba.
    good = lab.src_dump()
    # Cortes en la mitad (dentro del TOC) y casi al final (TOC entero, datos a medias): este
    # segundo es el que deja el destino a medias si falta `--single-transaction`.
    for cut in (len(good) // 2, len(good) - 400):
        broken = lab.restore_raw(good[:cut])
        assert broken.returncode == 13, (cut, broken.stderr)
        _same(lab.dst_fingerprint(), source, f"tras volcado truncado en {cut}")
        _check_readonly_role(lab)

    # Volcado cuyo sha no coincide: 11 y nada cambia.
    bad_sha = lab.restore_raw(good, sha="0" * 64)
    assert bad_sha.returncode == 11
    assert lab.dst_fingerprint() == source


def test_data_status_con_nocturna_ro_en_el_destino_coincide_con_el_origen(lab):
    sync = lab.sync()
    assert sync.returncode == 0, (sync.stdout, sync.stderr)

    expected = lab.src_freshness()
    assert expected is not None
    shown = lab.ro(_FRESHNESS_SQL).stdout.strip()
    assert shown == expected.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")
    # y es el maximo entre el Run cerrado (2026-11-08 02:30) y el snapshot (2026-10-01 + 3 dias)
    assert shown.startswith("2026-11-08T02:30:00")


def test_la_sincronizacion_no_deja_temporales(lab):
    lab.sync()

    assert [p.name for p in lab.tmp.glob("nocturna-sync.*")] == []
    assert [p.name for p in lab.tmp.glob("nocturna-restore.*")] == []
