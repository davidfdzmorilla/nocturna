"""T109: endurecimiento del despliegue publico, comprobaciones estaticas.

Sin red ni Claude. Las que necesitan `docker compose config` se saltan sin Docker.
Nombres en espanol, como el resto de `tests/deploy/`.
"""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy"
COMPOSE = DEPLOY / "compose.yml"
SH = DEPLOY / "postgres" / "10-api-readonly-role.sh"
SQL = DEPLOY / "postgres" / "api-readonly-role.sql"
ENV_EXAMPLE = DEPLOY / "env.example"

needs_docker = pytest.mark.skipif(shutil.which("docker") is None, reason="docker no disponible")


def _service_block(name: str) -> str:
    """Texto del servicio `name` en compose.yml (indentacion de 2 espacios)."""
    text = COMPOSE.read_text()
    services = text.split("\nservices:\n", 1)[1].split("\nnetworks:\n", 1)[0]
    m = re.search(rf"(?ms)^  {re.escape(name)}:\n(.*?)(?=^  \S|\Z)", services)
    assert m, name
    return m.group(1)


def test_api_fija_docs_desactivadas_en_el_compose():
    assert 'NOCTURNA_API_DOCS_ENABLED: "false"' in _service_block("api")


def test_api_se_conecta_con_el_rol_de_solo_lectura_y_su_contrasena():
    block = _service_block("api")
    url = re.search(r"NOCTURNA_DATABASE_URL: (\S+)", block)
    assert url
    prefix = "postgresql+psycopg://nocturna_ro:${NOCTURNA_API_DB_PASSWORD:?}@"
    assert url.group(1).startswith(prefix)
    assert "POSTGRES_PASSWORD" not in block
    assert "://nocturna:" not in block


def test_migrate_esta_en_el_perfil_ops_y_usa_el_propietario():
    block = _service_block("migrate")
    assert 'profiles: ["ops"]' in block
    assert "postgresql+psycopg://nocturna:${POSTGRES_PASSWORD:?}@" in block
    assert "nocturna_ro" not in block
    assert '["alembic", "upgrade", "head"]' in block


def test_postgres_monta_el_script_y_recibe_la_contrasena_de_la_api():
    block = _service_block("postgres")
    assert "NOCTURNA_API_DB_PASSWORD: ${NOCTURNA_API_DB_PASSWORD:?}" in block
    assert "/docker-entrypoint-initdb.d/10-api-readonly-role.sh:ro" in block
    assert "/nocturna/api-readonly-role.sql:ro" in block


def test_env_example_declara_la_contrasena_de_la_api_vacia():
    lines = ENV_EXAMPLE.read_text().splitlines()
    assert "NOCTURNA_API_DB_PASSWORD=" in lines
    assert "POSTGRES_PASSWORD=" in lines


def test_script_del_rol_tiene_sintaxis_sh_valida():
    r = subprocess.run(["sh", "-n", str(SH)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_script_pasa_la_contrasena_por_guc_y_no_por_argv():
    sh = SH.read_text()
    psql_line = [ln for ln in sh.splitlines() if "psql" in ln and not ln.lstrip().startswith("#")]
    assert psql_line and "ro_password" not in psql_line[0]
    assert "-c nocturna.ro_password=$NOCTURNA_API_DB_PASSWORD" in sh  # va en PGOPTIONS
    assert "current_setting('nocturna.ro_password')" in SQL.read_text()


def _sh_con_password(password: str | None) -> subprocess.CompletedProcess[str]:
    env = {"PATH": os.environ["PATH"], "POSTGRES_USER": "u", "POSTGRES_DB": "d"}
    if password is not None:
        env["NOCTURNA_API_DB_PASSWORD"] = password
    return subprocess.run(["sh", str(SH)], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("password", [None, "", "con-guion", "con espacio", "a;b"])
def test_script_rechaza_contrasena_vacia_o_con_caracteres_no_permitidos(password):
    r = _sh_con_password(password)
    assert r.returncode == 1
    assert "NOCTURNA_API_DB_PASSWORD" in r.stderr


# --- compose resuelto ------------------------------------------------------------------


def _resolved(*extra: str) -> dict:
    env = {
        **os.environ,
        "NOCTURNA_DOMAIN": "example.test",
        "NOCTURNA_ACME_EMAIL": "ops@example.test",
        "POSTGRES_PASSWORD": "syntheticpassword",
        "NOCTURNA_API_DB_PASSWORD": "syntheticapipassword",
    }
    r = subprocess.run(
        ["docker", "compose", "-f", str(COMPOSE), *extra, "config", "--format", "json"],
        capture_output=True,
        text=True,
        env=env,
        cwd=COMPOSE.parent,
        timeout=60,
    )
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@needs_docker
def test_compose_resuelto_api_solo_en_internal_sin_puertos():
    api = _resolved()["services"]["api"]
    assert set(api["networks"]) == {"internal"}
    assert not api.get("ports")
    assert api["environment"]["NOCTURNA_API_DOCS_ENABLED"] == "false"
    url = api["environment"]["NOCTURNA_DATABASE_URL"]
    assert "nocturna_ro:syntheticapipassword@postgres" in url


@needs_docker
def test_compose_resuelto_el_proxy_no_esta_en_internal():
    cfg = _resolved()
    proxies = [n for n, s in cfg["services"].items() if s.get("ports")]
    assert proxies == ["traefik"]
    assert set(cfg["services"]["traefik"]["networks"]) == {"edge"}


@needs_docker
def test_compose_resuelto_migrate_solo_con_el_perfil_ops():
    assert "migrate" not in _resolved()["services"]
    with_ops = _resolved("--profile", "ops")["services"]
    assert "migrate" in with_ops
    url = with_ops["migrate"]["environment"]["NOCTURNA_DATABASE_URL"]
    assert "nocturna:syntheticpassword@postgres" in url
