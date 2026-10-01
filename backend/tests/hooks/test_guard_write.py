"""Tests de `.claude/hooks/guard-write.sh` (T80): inmutabilidad de ADR segun git.

Un ADR en git (indice o commit) del repo del propio fichero es inmutable; un
borrador sin rastrear se puede editar; si git no se puede consultar, falla
cerrado. Tambien se congelan las demas reglas del hook (regresion).

Los repos temporales y sus commits los crea esta suite desde Python con
`subprocess` dentro de `tmp_path`, con entorno explicito (HOME temporal, sin
config de sistema, sin firma GPG) y un PATH minimo hecho de symlinks. El hook
se invoca con `bash` por ruta absoluta resuelta respecto a este fichero.

Los literales que el propio hook bloquearia (asignacion de API key, atribucion
a IA) se construyen concatenando trozos; son datos de prueba, no evasiones.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "guard-write.sh"
BASH = shutil.which("bash") or "/bin/bash"
GIT = shutil.which("git") or "/usr/bin/git"

API_KEY_ASSIGN = "ANTHROPIC_API" + "_KEY=sk-test-no-usar"
CO_AUTHOR = "Co-authored" + "-by: Claude <noreply@anthropic.com>"
GENERATED = "Generated " + "with Claude Code"

_TOOLS = ("git", "bash", "env", "dirname", "basename", "grep")


def _mk_bin(root: Path, name: str = "bin", tools: tuple[str, ...] = _TOOLS) -> Path:
    d = root / name
    d.mkdir(exist_ok=True)
    for t in tools:
        src = shutil.which(t)
        assert src, t
        (d / t).symlink_to(src)
    (d / "python3").symlink_to(sys.executable)
    return d


@pytest.fixture
def env(tmp_path: Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    return {
        "HOME": str(home),
        "PATH": str(_mk_bin(tmp_path)),
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CEILING_DIRECTORIES": str(tmp_path),
    }


def _git(env: dict[str, str], cwd: Path, *args: str) -> None:
    subprocess.run(
        [
            GIT,
            "-c", "user.name=T",
            "-c", "user.email=t@example.com",
            "-c", "commit.gpgsign=false",
            *args,
        ],
        cwd=cwd,
        env=env,
        check=True,
        capture_output=True,
    )  # fmt: skip


def _repo(env: dict[str, str], root: Path, tracked: dict[str, str] | None = None) -> Path:
    root.mkdir(parents=True)
    _git(env, root, "init", "-q", "-b", "main")
    adr = root / "docs" / "adr"
    adr.mkdir(parents=True)
    for name, text in (tracked or {}).items():
        (adr / name).write_text(text)
    if tracked:
        _git(env, root, "add", "-A")
    _git(env, root, "commit", "-q", "--allow-empty", "-m", "init")
    return root


def _run(
    env: dict[str, str],
    file_path: object,
    *,
    cwd: Path,
    content: str = "x",
    payload: dict | None = None,
    extra_env: dict[str, str] | None = None,
    stdin: str | None = None,
) -> subprocess.CompletedProcess[str]:
    e = dict(env)
    e.update(extra_env or {})
    data = stdin
    if data is None:
        ti: dict = {"file_path": str(file_path)}
        ti.update(payload if payload is not None else {"content": content})
        data = json.dumps({"tool_input": ti})
    return subprocess.run(
        [BASH, str(HOOK)], input=data, text=True, capture_output=True, cwd=cwd, env=e
    )  # fmt: skip


def _draft(repo: Path, name: str = "0099-borrador.md") -> Path:
    p = repo / "docs" / "adr" / name
    p.write_text("borrador")
    return p


# ---- ADR segun git (1-16) ----


def test_01_borrador_sin_rastrear_existente_pasa(env, tmp_path):
    repo = _repo(env, tmp_path / "r", {"0001-a.md": "a"})
    r = _run(env, _draft(repo), cwd=repo)
    assert r.returncode == 0, r.stderr


def test_02_adr_inexistente_sin_rastrear_pasa(env, tmp_path):
    repo = _repo(env, tmp_path / "r")
    r = _run(env, repo / "docs" / "adr" / "0042-nuevo.md", cwd=repo)
    assert r.returncode == 0, r.stderr


def test_03_commiteado_sin_cambios_bloquea(env, tmp_path):
    repo = _repo(env, tmp_path / "r", {"0001-a.md": "a"})
    r = _run(env, repo / "docs/adr/0001-a.md", cwd=repo)
    assert r.returncode == 2
    assert "ya está en git" in r.stderr


def test_04_commiteado_con_cambios_bloquea(env, tmp_path):
    repo = _repo(env, tmp_path / "r", {"0001-a.md": "a"})
    (repo / "docs/adr/0001-a.md").write_text("modificado")
    assert _run(env, repo / "docs/adr/0001-a.md", cwd=repo).returncode == 2


def test_05_commiteado_y_borrado_del_disco_bloquea(env, tmp_path):
    repo = _repo(env, tmp_path / "r", {"0001-a.md": "a"})
    (repo / "docs/adr/0001-a.md").unlink()
    assert _run(env, repo / "docs/adr/0001-a.md", cwd=repo).returncode == 2


def test_06_git_add_sin_commit_bloquea(env, tmp_path):
    repo = _repo(env, tmp_path / "r")
    p = _draft(repo, "0010-x.md")
    _git(env, repo, "add", str(p))
    assert _run(env, p, cwd=repo).returncode == 2


def test_07_git_mv_en_indice_bloquea_destino_y_libera_origen(env, tmp_path):
    repo = _repo(env, tmp_path / "r", {"0001-a.md": "a"})
    _git(env, repo, "mv", "docs/adr/0001-a.md", "docs/adr/0002-a.md")
    assert _run(env, repo / "docs/adr/0002-a.md", cwd=repo).returncode == 2
    assert _run(env, repo / "docs/adr/0001-a.md", cwd=repo).returncode == 0


def test_08_fuera_de_repo_falla_cerrado(env, tmp_path):
    d = tmp_path / "nogit" / "docs" / "adr"
    d.mkdir(parents=True)
    (d / "0001.md").write_text("x")
    r = _run(env, d / "0001.md", cwd=tmp_path)
    assert r.returncode == 2
    assert "Falla cerrado" in r.stderr


def test_09_path_sin_git_falla_cerrado(env, tmp_path):
    repo = _repo(env, tmp_path / "r")
    nogit = _mk_bin(tmp_path, "nogit", tools=("env", "dirname", "basename", "grep"))
    r = _run(env, _draft(repo), cwd=repo, extra_env={"PATH": str(nogit)})
    assert r.returncode == 2
    assert "Falla cerrado" in r.stderr


def test_10_directorio_padre_inexistente_falla_cerrado(env, tmp_path):
    repo = _repo(env, tmp_path / "r")
    r = _run(env, repo / "no" / "docs" / "adr" / "0001.md", cwd=repo)
    assert r.returncode == 2


def test_11_worktree_se_consulta_en_el_repo_del_fichero(env, tmp_path):
    a = _repo(env, tmp_path / "A", {"0001-a.md": "a"})
    b = tmp_path / "B"
    _git(env, a, "worktree", "add", "-q", "-b", "otra", str(b))
    extra = {"CLAUDE_PROJECT_DIR": str(a)}
    r = _run(env, _draft(b), cwd=a, extra_env=extra)
    assert r.returncode == 0, r.stderr
    r = _run(env, b / "docs/adr/0001-a.md", cwd=a, extra_env=extra)
    assert r.returncode == 2


def test_12_repo_del_fichero_manda_no_el_del_proyecto(env, tmp_path):
    p = _repo(env, tmp_path / "P", {"0050-p.md": "p"})
    q = _repo(env, tmp_path / "Q", {"0060-q.md": "q"})
    extra = {"CLAUDE_PROJECT_DIR": str(p)}
    # sin rastrear en Q, aunque P tenga un ADR: pasa
    assert _run(env, _draft(q), cwd=p, extra_env=extra).returncode == 0
    # rastreado en Q, aunque cwd/proyecto sea P: bloquea
    assert _run(env, q / "docs/adr/0060-q.md", cwd=p, extra_env=extra).returncode == 2


def test_13_git_dir_heredado_no_desvia_la_consulta(env, tmp_path):
    p = _repo(env, tmp_path / "P", {"0050-p.md": "p"})
    q = _repo(env, tmp_path / "Q", {"0060-q.md": "q"})
    extra = {"GIT_DIR": str(p / ".git")}
    assert _run(env, q / "docs/adr/0060-q.md", cwd=p, extra_env=extra).returncode == 2
    # mismo nombre rastreado en P pero sin rastrear en Q: resuelve Q
    (q / "docs/adr/0050-p.md").write_text("borrador")
    assert _run(env, q / "docs/adr/0050-p.md", cwd=p, extra_env=extra).returncode == 0


def test_14_glob_literal_no_casa_con_rastreados(env, tmp_path):
    repo = _repo(env, tmp_path / "r", {"0001-a.md": "a"})
    p = repo / "docs/adr/00*.md"
    p.write_text("x")
    assert _run(env, p, cwd=repo).returncode == 0


def test_15_rutas_con_espacios(env, tmp_path):
    repo = _repo(env, tmp_path / "mi repo con espacios", {"0001 a.md": "a"})
    assert _run(env, _draft(repo, "0099 b.md"), cwd=repo).returncode == 0
    r = _run(env, repo / "docs/adr/0001 a.md", cwd=repo)
    assert r.returncode == 2
    assert "ya está en git" in r.stderr


def test_16_borrador_sin_rastrear_con_api_key_en_contenido_bloquea(env, tmp_path):
    repo = _repo(env, tmp_path / "r")
    r = _run(env, _draft(repo), cwd=repo, content="export " + API_KEY_ASSIGN)
    assert r.returncode == 2
    assert "ANTHROPIC" in r.stderr


# ---- regresion (17-22) ----


@pytest.mark.parametrize("name", [".env", ".env.local"])
def test_17_ficheros_env_bloquean(env, tmp_path, name):
    assert _run(env, tmp_path / name, cwd=tmp_path).returncode == 2


@pytest.mark.parametrize(
    "payload",
    [
        {"content": API_KEY_ASSIGN},
        {"new_string": API_KEY_ASSIGN},
        {"edits": [{"new_string": "ok"}, {"new_string": API_KEY_ASSIGN}]},
    ],
    ids=["content", "new_string", "edits"],
)
def test_18_api_key_en_cualquier_campo_bloquea(env, tmp_path, payload):
    r = _run(env, tmp_path / "f.txt", cwd=tmp_path, payload=payload)
    assert r.returncode == 2


@pytest.mark.parametrize("text", [CO_AUTHOR, GENERATED])
def test_19_atribucion_a_ia_bloquea(env, tmp_path, text):
    assert _run(env, tmp_path / "f.txt", cwd=tmp_path, content=text).returncode == 2


def test_20_dominio_con_import_de_infraestructura_bloquea(env, tmp_path):
    f = tmp_path / "backend/src/nocturna/domain/x.py"
    f.parent.mkdir(parents=True)
    assert _run(env, f, cwd=tmp_path, content="import sqlalchemy\n").returncode == 2
    assert _run(env, f, cwd=tmp_path, content="import dataclasses\n").returncode == 0


def test_21_fichero_corriente_pasa(env, tmp_path):
    assert _run(env, tmp_path / "notas.md", cwd=tmp_path, content="hola").returncode == 0


@pytest.mark.parametrize("raw", ["no es json", ""])
def test_22_json_invalido_falla_cerrado(env, tmp_path, raw):
    assert _run(env, "x", cwd=tmp_path, stdin=raw).returncode == 2


# ---- Correcciones de la revisión de T80 ----


def test_23_mayusculas_en_el_nombre_de_un_adr_rastreado_bloquea(env, tmp_path):
    # En APFS "0001-A.md" es el mismo fichero que el ADR rastreado "0001-a.md".
    repo = _repo(env, tmp_path / "r", {"0001-a.md": "a"})
    r = _run(env, repo / "docs/adr/0001-A.md", cwd=repo)
    assert r.returncode == 2
    assert "ya está en git" in r.stderr


def test_24_symlink_a_un_adr_rastreado_bloquea(env, tmp_path):
    repo = _repo(env, tmp_path / "r", {"0001-a.md": "a"})
    link = repo / "docs" / "adr" / "0099-link.md"
    link.symlink_to("0001-a.md")
    r = _run(env, link, cwd=repo)
    assert r.returncode == 2
    assert "symlink" in r.stderr


def test_25_git_index_file_heredado_no_desvia_la_consulta(env, tmp_path):
    p = _repo(env, tmp_path / "p", {"0050-x.md": "x"})
    q = _repo(env, tmp_path / "q")
    draft = _draft(q, "0050-x.md")
    r = _run(env, draft, cwd=p, extra_env={"GIT_INDEX_FILE": str(p / ".git" / "index")})
    assert r.returncode == 0, r.stderr
