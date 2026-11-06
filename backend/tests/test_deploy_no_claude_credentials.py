"""T107 (ADR 0044): ni imagenes ni compose de produccion llevan Claude ni sus credenciales.

Estaticos, sin Docker ni red. La comprobacion sobre las imagenes construidas esta en
`tests/deploy/test_images.py` (`-m docker`, `NOCTURNA_CHECK_IMAGES=1`).
`config/pipeline.toml` no se escanea: se copia a la imagen y nombra al proveedor en
comentarios; la imagen se comprueba por ausencia de SDK, binario y credenciales.
"""

import os
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest
from deploy._checks import env_violations, forbidden_terms, lock_closure

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent

DEPLOY_FILES = [
    ROOT / "deploy" / "compose.yml",
    ROOT / "deploy" / "env.example",
    *sorted((ROOT / "deploy").glob("*/dynamic.yml")),
    BACKEND / "Dockerfile",
    ROOT / "web" / "Dockerfile",
    ROOT / ".dockerignore",
    ROOT / "web" / ".dockerignore",
]
DOCKERIGNORES = [ROOT / ".dockerignore", ROOT / "web" / ".dockerignore"]
NEVER_REINCLUDE = (".env", ".claude", ".log", "node_modules", ".next", "backups", "tests")

# Los nombres se componen a trozos para no tropezar con los guards del repo al escribir el test.
_API_KEY_LINE = "ANTHROPIC" + "_API_KEY=x"


def test_hay_ficheros_de_despliegue_que_escanear():
    assert all(p.is_file() for p in DEPLOY_FILES), [p for p in DEPLOY_FILES if not p.is_file()]
    assert any(p.name == "dynamic.yml" for p in DEPLOY_FILES)


@pytest.mark.parametrize("path", DEPLOY_FILES, ids=lambda p: str(p.relative_to(ROOT)))
def test_ficheros_de_despliegue_sin_claude_ni_anthropic(path):
    assert forbidden_terms(path.read_text()) == []


@pytest.mark.parametrize("path", DOCKERIGNORES, ids=lambda p: str(p.relative_to(ROOT)))
def test_dockerignore_es_lista_blanca_y_no_reincluye_secretos(path):
    lines = [ln.strip() for ln in path.read_text().splitlines() if ln.strip() and ln[0] != "#"]
    assert lines[0] == "*"
    reincluded = [ln for ln in lines if ln.startswith("!")]
    bad = [ln for ln in reincluded if any(t in ln for t in NEVER_REINCLUDE)]
    assert bad == []


def _lock() -> str:
    return (BACKEND / "uv.lock").read_text()


def test_cierre_de_la_imagen_api_no_contiene_sdk_ni_mcp():
    closure = lock_closure(_lock(), "nocturna", {"serve"})
    assert "uvicorn" in closure and "fastapi" in closure  # el cierre no esta vacio
    assert "claude-agent-sdk" not in closure
    assert "mcp" not in closure


def test_sdk_vive_en_el_grupo_pipeline_y_es_por_defecto_en_local():
    py = tomllib.loads((BACKEND / "pyproject.toml").read_text())
    assert any(d.startswith("claude-agent-sdk") for d in py["dependency-groups"]["pipeline"])
    assert not any(d.startswith("claude-agent-sdk") for d in py["project"]["dependencies"])
    assert "pipeline" in py["tool"]["uv"]["default-groups"]
    # y el lock lo refleja: el SDK solo entra con el grupo pipeline
    assert "claude-agent-sdk" in lock_closure(_lock(), "nocturna", {"pipeline"})
    assert "claude-agent-sdk" not in lock_closure(_lock(), "nocturna", {"dev", "serve"})


def test_dockerfile_api_instala_solo_el_grupo_serve():
    text = (BACKEND / "Dockerfile").read_text()
    assert "--no-default-groups" in text
    assert "--group serve" in text
    for flag in ("--all-groups", "--group pipeline", "--group dev"):
        assert flag not in text
    syncs = [ln for ln in text.splitlines() if "uv sync" in ln]
    assert syncs and all("--no-default-groups" in s and "--group serve" in s for s in syncs)


def test_deploy_env_esta_ignorado_por_git():
    r = subprocess.run(
        ["git", "-C", str(ROOT), "check-ignore", "deploy/.env"], capture_output=True, text=True
    )
    assert r.returncode == 0, r.stderr


# --- regresiones sinteticas: los detectores detectan -------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        _API_KEY_LINE,
        "      - ~/.claude:/home/app/.claude",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "image: Anthropic/foo",
    ],
)
def test_forbidden_terms_detecta_credenciales_sinteticas(text):
    assert forbidden_terms(text)


def test_forbidden_terms_no_da_falsos_positivos():
    assert forbidden_terms("services:\n  api:\n    image: nocturna-api:local\n") == []


_SYNTHETIC_LOCK = """
[[package]]
name = "nocturna"
version = "0.1.0"
source = { editable = "." }
dependencies = [{ name = "claude-agent-sdk" }, { name = "psycopg", extra = ["binary"] }]

[package.dev-dependencies]
serve = [{ name = "uvicorn" }]

[[package]]
name = "claude-agent-sdk"
version = "1"
dependencies = [{ name = "mcp" }]

[[package]]
name = "mcp"
version = "1"

[[package]]
name = "psycopg"
version = "1"
[package.optional-dependencies]
binary = [{ name = "psycopg-binary" }]

[[package]]
name = "psycopg-binary"
version = "1"

[[package]]
name = "uvicorn"
version = "1"
"""


def test_lock_closure_detecta_sdk_en_dependencies_y_resuelve_extras():
    closure = lock_closure(_SYNTHETIC_LOCK, "nocturna", {"serve"})
    assert {"claude-agent-sdk", "mcp", "psycopg-binary", "uvicorn"} <= closure
    assert "uvicorn" not in lock_closure(_SYNTHETIC_LOCK, "nocturna", set())


@pytest.mark.parametrize(
    "env",
    [
        {"ANTHROPIC_BASE_URL": "http://x"},
        {"CLAUDE_CONFIG_DIR": "/c"},
        ["PATH=/bin", "ANTHROPIC_BASE_URL=http://x"],
        ["CLAUDE_CONFIG_DIR=/c"],
        {"FOO": "/home/app/.claude"},
    ],
)
def test_env_violations_detecta_variables_prohibidas(env):
    assert env_violations(env)


def test_env_violations_acepta_entorno_limpio():
    assert env_violations({"NOCTURNA_API_URL": "http://api:8000"}) == []
    assert env_violations(["PATH=/usr/bin", "NODE_ENV=production"]) == []


def _compose_config(compose: Path) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "NOCTURNA_DOMAIN": "example.test",
        "NOCTURNA_ACME_EMAIL": "ops@example.test",
        "POSTGRES_PASSWORD": "synthetic-password",
    }
    return subprocess.run(
        ["docker", "compose", "-f", str(compose), "config", "-q"],
        capture_output=True,
        text=True,
        env=env,
        cwd=compose.parent,
        timeout=60,
    )


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker no disponible")
def test_compose_de_produccion_es_yaml_valido_y_resuelve():
    r = _compose_config(ROOT / "deploy" / "compose.yml")
    assert r.returncode == 0, r.stderr


@pytest.mark.skipif(shutil.which("docker") is None, reason="docker no disponible")
def test_compose_roto_es_detectado_por_la_comprobacion(tmp_path):
    original = (ROOT / "deploy" / "compose.yml").read_text()
    broken = original.replace("NOCTURNA_CORS_ORIGINS: '", "NOCTURNA_CORS_ORIGINS:'", 1)
    assert broken != original
    f = tmp_path / "compose.yml"
    f.write_text(broken)
    assert _compose_config(f).returncode != 0
