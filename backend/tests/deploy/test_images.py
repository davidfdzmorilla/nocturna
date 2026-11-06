"""T107 (ADR 0044): las imagenes construidas y el compose resuelto no llevan Claude.

Construye con Docker de verdad, por eso va tras `NOCTURNA_CHECK_IMAGES=1` y el marcador
`docker`:

    NOCTURNA_CHECK_IMAGES=1 uv run pytest -m docker -q

No construye ni arranca el proxy. Usa un proyecto de compose aislado (`nocturna-t107-test`),
imagenes con etiqueta propia (para no pisar `nocturna-*:local`) y un env de prueba en un
directorio temporal. Al terminar baja el proyecto, borra SUS volumenes por nombre y sus
imagenes. No toca `nocturna-postgres` ni su volumen. Ningun test llama a Claude.
"""

import json
import os
import subprocess
from pathlib import Path

import pytest
from deploy._checks import env_violations

pytestmark = [
    pytest.mark.docker,
    pytest.mark.skipif(
        os.environ.get("NOCTURNA_CHECK_IMAGES") != "1",
        reason="construye imagenes con Docker; activar con NOCTURNA_CHECK_IMAGES=1",
    ),
]

ROOT = Path(__file__).resolve().parents[3]
COMPOSE = ROOT / "deploy" / "compose.yml"
PROJECT = "nocturna-t107-test"
IMG_API = "nocturna-t107-test-api:test"
IMG_WEB = "nocturna-t107-test-web:test"

ENV_FILE = (
    "NOCTURNA_DOMAIN=nocturna.test\n"
    "NOCTURNA_ACME_EMAIL=test@nocturna.test\n"
    "POSTGRES_PASSWORD=t107-test-password\n"
)
OVERRIDE = f"services:\n  api:\n    image: {IMG_API}\n  web:\n    image: {IMG_WEB}\n"


def _run(cmd, *, timeout=900, check=True, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, **kw)
    if check and r.returncode != 0:
        raise AssertionError(
            f"{' '.join(map(str, cmd))}\nrc={r.returncode}\n{r.stdout[-3000:]}\n{r.stderr[-3000:]}"
        )
    return r


@pytest.fixture(scope="module")
def dc(tmp_path_factory):
    """Devuelve una funcion que ejecuta `docker compose` con el proyecto y env de prueba."""
    tmp = tmp_path_factory.mktemp("t107")
    env = tmp / "test.env"
    env.write_text(ENV_FILE)
    override = tmp / "override.yml"
    override.write_text(OVERRIDE)
    base = [
        "docker", "compose", "-p", PROJECT, "-f", str(COMPOSE), "-f", str(override),
        "--env-file", str(env),
    ]  # fmt: skip

    def run(*args, **kw):
        return _run([*base, *args], **kw)

    try:
        yield run
    finally:
        run("down", "--remove-orphans", check=False)
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
        _run(["docker", "image", "rm", IMG_API, IMG_WEB], check=False)


@pytest.fixture(scope="module")
def built(dc):
    dc("build", "api", "web", timeout=1800)
    return True


def _find_claude(image: str) -> str:
    r = _run(
        [
            "docker", "run", "--rm", "--network", "none", "--entrypoint", "find", image,
            "/", "-xdev", "(", "-iname", "*claude*", "-o", "-iname", "*anthropic*", ")",
        ],
        check=False,
    )  # fmt: skip
    return r.stdout.strip()


_API_SCRIPT = """
import importlib.util, shutil, json
from nocturna.api.app import create_app
from nocturna.api.deps import get_digest_timezone
create_app()
print(json.dumps({
    "sdk": importlib.util.find_spec("claude_agent_sdk") is None,
    "mcp": importlib.util.find_spec("mcp") is None,
    "bin": shutil.which("claude") is None,
    "tz": str(get_digest_timezone()),
}))
"""


def test_imagen_api_sin_sdk_ni_binario_ni_ficheros_de_claude(built):
    r = _run(
        [
            "docker", "run", "--rm", "--network", "none", "--entrypoint", "python",
            "-e", "NOCTURNA_DATABASE_URL=postgresql+psycopg://u:p@localhost:5432/d",
            IMG_API, "-c", _API_SCRIPT,
        ]
    )  # fmt: skip
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out == {"sdk": True, "mcp": True, "bin": True, "tz": "Europe/Madrid"}
    assert _find_claude(IMG_API) == ""


def test_imagen_web_sin_ficheros_de_claude_ni_paquete_anthropic(built):
    assert _find_claude(IMG_WEB) == ""
    r = _run(
        [
            "docker", "run", "--rm", "--network", "none", "--entrypoint", "find", IMG_WEB,
            "/", "-xdev", "-path", "*node_modules/@anthropic-ai*",
        ],
        check=False,
    )  # fmt: skip
    assert r.stdout.strip() == ""


@pytest.mark.parametrize("image", [IMG_API, IMG_WEB])
def test_imagen_no_corre_como_root_y_su_entorno_esta_limpio(built, image):
    cfg = json.loads(_run(["docker", "image", "inspect", image]).stdout)[0]["Config"]
    assert cfg["User"] not in ("", "0", "root")
    assert env_violations(cfg["Env"]) == []


def test_compose_resuelto_solo_el_proxy_publica_y_la_red_interna_es_cerrada(dc, built):
    cfg = json.loads(dc("config", "--format", "json").stdout)
    services = cfg["services"]
    proxies = {n for n, s in services.items() if s.get("ports")}
    assert len(proxies) == 1, proxies
    assert set(services["api"].get("networks", {})) == {"internal"}
    assert set(services["postgres"].get("networks", {})) == {"internal"}
    assert cfg["networks"]["internal"].get("internal") is True
    assert "ports" not in services["api"] and "ports" not in services["postgres"]
    for name, svc in services.items():
        for vol in svc.get("volumes", []):
            # la ruta del checkout puede contener `.claude` (worktrees): se compara relativa
            src = str(vol.get("source", "")).replace(str(ROOT), "") + str(vol.get("target", ""))
            assert "docker.sock" not in src, (name, vol)
            assert ".claude" not in src, (name, vol)
        assert env_violations(svc.get("environment", {})) == [], name
        assert "env_file" not in svc, name


def test_humo_postgres_api_y_web_arrancan_sanos_y_la_web_sirve_200(dc, built):
    dc("run", "--rm", "api", "alembic", "upgrade", "head", timeout=300)
    dc("up", "-d", "--wait", "--wait-timeout", "180", "postgres", "api", "web", timeout=400)
    dc(
        "exec",
        "-T",
        "web",
        "node",
        "-e",
        "fetch('http://localhost:3000/').then(r=>process.exit(r.status===200?0:1))"
        ".catch(()=>process.exit(1))",
    )
    ps = json.loads("[" + ",".join(dc("ps", "--format", "json").stdout.strip().splitlines()) + "]")
    assert {p["Service"] for p in ps} == {"postgres", "api", "web"}  # el proxy no arranca
