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
    "NOCTURNA_API_DB_PASSWORD=t109apitestpassword\n"
)
OVERRIDE = (
    f"services:\n  api:\n    image: {IMG_API}\n  migrate:\n    image: {IMG_API}\n"
    f"  web:\n    image: {IMG_WEB}\n"
)


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


@pytest.fixture(scope="module")
def stack(dc, built):
    """postgres (initdb con el rol de lectura) -> `migrate` con el propietario -> api y web."""
    dc("up", "-d", "--wait", "--wait-timeout", "120", "postgres", timeout=300)
    dc("run", "--rm", "migrate", timeout=300)
    dc("up", "-d", "--wait", "--wait-timeout", "180", "api", "web", timeout=400)
    return True


_BANNER = "Análisis generado automáticamente por IA"


def _web_get(dc, path: str, ua: str | None = None) -> tuple[int, str]:
    """GET desde dentro del contenedor web a su propio servidor; devuelve (estado, html)."""
    script = (
        "const h=process.argv[1]?{'user-agent':process.argv[1]}:{};"
        "fetch('http://localhost:3000'+process.argv[2],{headers:h,redirect:'manual'})"
        ".then(async r=>console.log(JSON.stringify([r.status,await r.text()])))"
        ".catch(e=>{console.error(e);process.exit(1)})"
    )
    out = dc("exec", "-T", "web", "node", "-e", script, ua or "", path).stdout
    status, html = json.loads(out.strip().splitlines()[-1])
    return status, html


def _web_status(dc, url: str) -> int:
    script = (
        "fetch(process.argv[1]).then(r=>console.log(r.status))"
        ".catch(e=>{console.error(e);process.exit(1)})"
    )
    return int(dc("exec", "-T", "web", "node", "-e", script, url).stdout.strip())


def test_humo_postgres_api_y_web_arrancan_sanos_y_la_web_sirve_200(dc, stack):
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
    assert {p["Service"] for p in ps} == {"postgres", "api", "web"}  # ni proxy ni migrate


_API_PROBE = """
import json, os, urllib.request
import sqlalchemy as sa
e = sa.create_engine(os.environ["NOCTURNA_DATABASE_URL"])
out = {}
with e.connect() as c:
    out["user"] = c.execute(sa.text("select current_user")).scalar_one()
    out["ro"] = c.execute(sa.text("show default_transaction_read_only")).scalar_one()
    out["select"] = c.execute(sa.text("select count(*) from items")).scalar_one()
try:
    with e.begin() as c:
        c.execute(sa.text("insert into items (source, external_id, title, abstract) "
                          "values ('arxiv','x','t','a')"))
    out["insert"] = "ok"
except Exception as exc:
    out["insert"] = type(exc.orig).__name__
out["health"] = urllib.request.urlopen("http://127.0.0.1:8000/health", timeout=4).status
print(json.dumps(out))
"""


def test_la_api_se_conecta_con_el_rol_de_lectura_y_rechaza_insert(dc, stack):
    r = dc("exec", "-T", "api", "python", "-c", _API_PROBE)
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["user"] == "nocturna_ro"
    assert out["ro"] == "on"
    assert out["select"] == 0
    assert out["insert"] in ("ReadOnlySqlTransaction", "InsufficientPrivilege")
    assert out["health"] == 200


def test_la_api_no_expone_docs_ni_openapi_a_la_red_interna(dc, stack):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert _web_status(dc, f"http://api:8000{path}") == 404, path
    assert _web_status(dc, "http://api:8000/health") == 200


def test_contenedor_solo_en_la_red_edge_no_alcanza_la_api(dc, stack):
    r = _run(
        [
            "docker", "run", "--rm", "--network", f"{PROJECT}_edge", "--entrypoint", "node",
            IMG_WEB, "-e",
            "fetch('http://api:8000/health',{signal:AbortSignal.timeout(4000)})"
            ".then(()=>process.exit(1)).catch(()=>process.exit(0))",
        ],
        check=False,
    )  # fmt: skip
    assert r.returncode == 0, r.stdout + r.stderr


@pytest.mark.parametrize(
    "path",
    [
        "/hallazgo/00000000-0000-4000-8000-000000000000",
        "/hallazgo/no-uuid",
        "/archivo/2026-W99",
        "/ruta-inexistente",
    ],
)
def test_matriz_con_base_vacia_not_found_es_404(dc, stack, path):
    assert _web_get(dc, path)[0] == 404


def test_matriz_con_base_vacia_la_portada_es_200_y_se_indexa(dc, stack):
    status, html = _web_get(dc, "/")
    assert status == 200
    assert _BANNER in html
    assert "noindex" not in html


@pytest.mark.parametrize("ua", [None, "Mozilla/5.0 (compatible; Googlebot/2.1)"])
def test_con_la_api_parada_las_paginas_dan_200_con_banner_y_noindex(dc, stack, ua):
    dc("stop", "api")
    try:
        for path in (
            "/",
            "/archivo",
            "/hallazgo/00000000-0000-4000-8000-000000000000",
            "/archivo/2026-W41",
        ):
            status, html = _web_get(dc, path, ua)
            assert status == 200, path
            assert _BANNER in html, path
            assert "noindex, nofollow" in html, path
    finally:
        dc("up", "-d", "--wait", "--wait-timeout", "120", "api", timeout=200)
