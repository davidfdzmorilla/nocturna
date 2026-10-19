"""Tests de `.claude/hooks/guard-bash.sh` (T97): ADR publicados frente a Bash.

Un ADR que esta en git (commit o indice) del repo/worktree del propio fichero no
se modifica, mueve ni borra por Bash. Un borrador sin rastrear se puede tocar.
Si no se puede comprobar, falla cerrado. Tambien se congelan las reglas 1-4 del
hook (regresion).

Los repos temporales los crea esta suite con `subprocess` dentro de `tmp_path`,
con entorno explicito (HOME temporal, sin config de sistema, sin firma GPG) y un
PATH minimo de symlinks (incluye python3, lo necesita el hook). En los comandos,
`@R@` es el repo, `@T@` un directorio de salida fuera de cualquier repo, `@B@` un
worktree con un ADR publicado propio y `@S@` otro repo.

Los literales que el propio hook bloquearia (API key, endpoint, atribucion,
push forzado) se construyen concatenando trozos; son datos de prueba.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parents[3] / ".claude" / "hooks" / "guard-bash.sh"
BASH = shutil.which("bash") or "/bin/bash"
GIT = shutil.which("git") or "/usr/bin/git"

ADR = "docs/adr/0001-a.md"

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


class World:
    def __init__(self, tmp: Path, env: dict[str, str]):
        self.tmp = tmp
        self.env = env
        self.r = tmp / "r"
        self.b = tmp / "B"
        self.s = tmp / "s"
        self.out = tmp / "out"

    def sub(self, cmd: str) -> str:
        return (
            cmd.replace("@R@", str(self.r))
            .replace("@T@", str(self.out))
            .replace("@B@", str(self.b))
            .replace("@S@", str(self.s))
        )


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


@pytest.fixture
def w(tmp_path: Path, env: dict[str, str]) -> World:
    world = World(tmp_path, env)
    r = world.r
    r.mkdir()
    _git(env, r, "init", "-q", "-b", "main")
    adr = r / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / "0001-a.md").write_text("a")
    (adr / "0002-b.md").write_text("b")
    (r / "docs" / "otra").mkdir()
    (r / "docs" / "otra" / "x.md").write_text("x")
    (r / "notas.md").write_text("n")
    (r / "a").write_text("a")
    (r / "backend" / ".pytest_cache").mkdir(parents=True)
    (r / "backend" / ".pytest_cache" / "v").write_text("v")
    (r / ".claude").mkdir()
    _git(env, r, "add", "-A")
    _git(env, r, "commit", "-q", "-m", "init")
    # ADR solo en el indice
    (adr / "0010-x.md").write_text("x")
    _git(env, r, "add", "docs/adr/0010-x.md")
    # borradores sin rastrear
    (adr / "0036-borrador.md").write_text("b")
    (adr / "borrador.md").write_text("b")
    # worktree con un ADR publicado propio
    _git(env, r, "worktree", "add", "-q", "-b", "otra", str(world.b))
    (world.b / "docs" / "adr" / "0005-b.md").write_text("b5")
    _git(env, world.b, "add", "-A")
    _git(env, world.b, "commit", "-q", "-m", "adr 5")
    # otro repo, sin ADR
    world.s.mkdir()
    _git(env, world.s, "init", "-q", "-b", "main")
    (world.s / "docs" / "adr").mkdir(parents=True)
    _git(env, world.s, "commit", "-q", "--allow-empty", "-m", "init")
    # salida fuera de cualquier repo
    world.out.mkdir()
    (world.out / "x").write_text("x")
    (world.out / "0001-a.md").write_text("otro")
    return world


def _run(
    w: World,
    command: str,
    *,
    cwd: Path | None = None,
    json_cwd: Path | str | None = None,
    extra_env: dict[str, str] | None = None,
    stdin: str | None = None,
    approved: bool = False,
) -> subprocess.CompletedProcess[str]:
    cwd = cwd or w.r
    e = dict(w.env)
    e["CLAUDE_PROJECT_DIR"] = str(w.r)
    e.update(extra_env or {})
    flag = w.r / ".claude" / ".commit-approved"
    if approved:
        flag.write_text("ok")
    elif flag.exists():
        flag.unlink()
    data = stdin
    if data is None:
        data = json.dumps(
            {
                "tool_input": {"command": w.sub(command)},
                "cwd": str(json_cwd if json_cwd is not None else cwd),
            }
        )
    return subprocess.run(
        [BASH, str(HOOK)], input=data, text=True, capture_output=True, cwd=cwd, env=e
    )  # fmt: skip


def _blocks(w: World, command: str, **kw) -> subprocess.CompletedProcess[str]:
    r = _run(w, command, **kw)
    assert r.returncode == 2, f"debia bloquear: {command!r}\nstderr={r.stderr}"
    return r


def _passes(w: World, command: str, **kw) -> subprocess.CompletedProcess[str]:
    r = _run(w, command, **kw)
    assert r.returncode == 0, f"debia pasar: {command!r}\nstderr={r.stderr}"
    return r


# ---- Casos que deben bloquear (1-15) ----

BLOCK: dict[str, str] = {
    # 1 sed -i
    "sed-i-vacio": f"sed -i '' 's/a/b/' {ADR}",
    "sed-i": f"sed -i 's/a/b/' {ADR}",
    "sed-i-bak": f"sed -i.bak 's/a/b/' {ADR}",
    "sed-in-place": f"sed --in-place=.b 's/a/b/' {ADR}",
    "sed-Ei": f"sed -Ei '' 's/a/b/' {ADR}",
    # 2 rm
    "rm": f"rm {ADR}",
    "rm-f": f"rm -f {ADR}",
    "rm-guiones": f"rm -- {ADR}",
    # 3 mv
    "mv-origen": f"mv {ADR} @T@/out.md",
    "mv-pisa-destino": f"mv @T@/x {ADR}",
    # 4 git
    "git-mv": f"git mv {ADR} docs/adr/0003-a.md",
    "git-rm": f"git rm {ADR}",
    "git-rm-cached": f"git rm --cached {ADR}",
    "git-C-rm": f"git -C @R@ rm {ADR}",
    # 5 redirecciones
    "redir-mayor": f"echo x > {ADR}",
    "redir-doble": f"echo x >> {ADR}",
    "redir-pipe": f"echo x >| {ADR}",
    "redir-1": f"echo x 1> {ADR}",
    "redir-2": f"echo x 2> {ADR}",
    "redir-amp": f"echo x &> {ADR}",
    "redir-sin-espacio": f"echo x>{ADR}",
    "redir-heredoc": f"cat <<'EOF' > {ADR}\nhola\nEOF",
    # 6 otras escrituras
    "tee": f"echo x | tee {ADR}",
    "tee-a": f"echo x | tee -a {ADR}",
    "cp": f"cp @T@/x {ADR}",
    "cp-dir-pisa-por-basename": "cp @T@/0001-a.md docs/adr/",
    "install": f"install @T@/x {ADR}",
    "ln-sf": f"ln -sf @T@/x {ADR}",
    "truncate": f"truncate -s0 {ADR}",
    "dd-of": f"dd if=/dev/null of={ADR}",
    "perl-pi": f"perl -pi -e 's/a/b/' {ADR}",
    # 7 variantes de ruta
    "ruta-absoluta": "rm @R@/docs/adr/0001-a.md",
    "ruta-punto-barra": "rm ./docs/adr/0001-a.md",
    "ruta-doble-barra": "rm docs//adr/0001-a.md",
    "ruta-punto-medio": "rm docs/./adr/0001-a.md",
    "ruta-ADR-mayus": "rm docs/ADR/0001-a.md",
    "ruta-DOCS-mayus": "rm DOCS/adr/0001-a.md",
    "ruta-nombre-mayus": "rm docs/adr/0001-A.md",
    "ruta-comillas-dobles": 'rm "docs/adr/0001-a.md"',
    "ruta-comillas-simples": "rm 'docs/adr/0001-a.md'",
    # 8 cd
    "cd-y-rm": "cd docs/adr && rm 0001-a.md",
    "cd-docs-rm-adr": "cd docs; rm adr/0001-a.md",
    # 9 globs
    "glob-estrella": "rm docs/adr/*.md",
    "glob-interrogante": "rm docs/adr/000?-a.md",
    # 10 directorios
    "rm-r-adr": "rm -r docs/adr",
    "rm-r-docs": "rm -r docs",
    "mv-dir-adr": "mv docs/adr @T@/x2",
    "git-rm-r-docs": "git rm -r docs",
    "rm-r-worktree": "rm -r ../B",
    # 11 compuestos
    "and-rm": f"true && rm {ADR}",
    "punto-y-coma-rm": f"echo ok; rm {ADR}",
    "pipe-tee": f"ls | tee {ADR}",
    "subshell": f"(rm {ADR})",
    "bash-c": f"bash -c 'rm {ADR}'",
    "sh-c": f"sh -c 'rm {ADR}'",
    "eval": f"eval 'rm {ADR}'",
    # 12 interpretes
    "python-c": "python3 -c \"open('docs/adr/0001-a.md','w').write('x')\"",
    "python-heredoc": "python3 - <<'EOF'\nopen('docs/adr/0001-a.md','w').write('x')\nEOF",
    "perl-e": "perl -e 'unlink q(docs/adr/0001-a.md)'",
    # 13 find / xargs
    "find-delete": "find docs/adr -delete",
    "find-exec-rm": "find docs -exec rm {} +",
    "xargs-rm": "ls docs/adr/0001-a.md | xargs rm",
    # 14 otros ADR publicados
    "adr-solo-en-indice": "rm docs/adr/0010-x.md",
    "adr-publicado-en-otro-worktree": "rm @B@/docs/adr/0005-b.md",
    # 15 falla cerrado
    "comillas-sin-cerrar": "rm 'docs/adr/0001-a.md",
    "cd-variable-y-rm": f'cd "$X" && rm {ADR}',
}


@pytest.mark.parametrize("command", list(BLOCK.values()), ids=list(BLOCK))
def test_adr_publicado_bloquea(w, command):
    _blocks(w, command)


def test_mensaje_de_bloqueo_nombra_la_ruta_y_t97(w):
    r = _blocks(w, f"rm {ADR}")
    assert "ADR que ya está en git" in r.stderr
    assert "0001-a.md" in r.stderr
    assert "(T97)" in r.stderr


def test_ruta_con_tilde_y_home_en_el_padre(w):
    _blocks(w, "rm ~/r/docs/adr/0001-a.md", extra_env={"HOME": str(w.tmp)})


def test_ruta_relativa_desde_repo_hermano(w):
    _blocks(w, "rm ../r/docs/adr/0001-a.md", cwd=w.s)


def test_ruta_con_espacios(w, env):
    sp = w.tmp / "mi repo"
    sp.mkdir()
    _git(env, sp, "init", "-q", "-b", "main")
    (sp / "docs" / "adr").mkdir(parents=True)
    (sp / "docs" / "adr" / "0001 a.md").write_text("a")
    _git(env, sp, "add", "-A")
    _git(env, sp, "commit", "-q", "-m", "init")
    _blocks(w, 'rm "docs/adr/0001 a.md"', cwd=sp)
    _blocks(w, "rm docs/adr/0001\\ a.md", cwd=sp)


def test_cwd_del_json_dentro_de_docs_adr(w):
    _blocks(w, "rm 0001-a.md", json_cwd=w.r / "docs" / "adr")


def test_borrador_con_cwd_del_json_dentro_de_docs_adr_pasa(w):
    _passes(w, "rm borrador.md", json_cwd=w.r / "docs" / "adr")


def test_python_con_script_que_nombra_la_ruta(w):
    (w.out / "s.py").write_text("import os\nos.remove('docs/adr/0001-a.md')\n")
    _blocks(w, "python3 @T@/s.py")


def test_python_con_script_inocuo_pasa(w):
    (w.out / "ok.py").write_text("print('hola')\n")
    _passes(w, "python3 @T@/ok.py")


def test_symlink_en_docs_adr_como_destino_bloquea(w):
    (w.r / "docs" / "adr" / "0099-link.md").symlink_to("0001-a.md")
    _blocks(w, "echo x > docs/adr/0099-link.md")


def test_ruta_adr_fuera_de_repo_falla_cerrado(w):
    d = w.tmp / "nogit" / "docs" / "adr"
    d.mkdir(parents=True)
    (d / "0001.md").write_text("x")
    r = _blocks(w, f"rm {d}/0001.md", cwd=w.tmp)
    assert "Falla cerrado" in r.stderr


def test_path_sin_git_falla_cerrado(w):
    nogit = _mk_bin(w.tmp, "nogit", tools=("env", "dirname", "basename", "grep"))
    r = _blocks(w, "rm docs/adr/0036-borrador.md", extra_env={"PATH": str(nogit)})
    assert "Falla cerrado" in r.stderr


def test_git_dir_heredado_no_desvia(w):
    other = w.s / ".git"
    _blocks(w, f"rm {ADR}", extra_env={"GIT_DIR": str(other)})
    # un borrador sigue pasando aunque GIT_DIR apunte a otro repo
    _passes(w, "rm docs/adr/0036-borrador.md", extra_env={"GIT_DIR": str(other)})


def test_git_index_file_heredado_no_desvia(w):
    idx = w.s / ".git" / "index"
    _blocks(w, f"rm {ADR}", extra_env={"GIT_INDEX_FILE": str(idx)})
    _passes(w, "rm docs/adr/0036-borrador.md", extra_env={"GIT_INDEX_FILE": str(idx)})


def test_symlink_a_destino_sin_rastrear_bloquea(w):
    # El destino real no es un ADR publicado: bloquea por el symlink en si.
    link = w.r / "docs" / "adr" / "0099-link.md"
    link.symlink_to(w.out / "x")
    r = _blocks(w, "echo x > docs/adr/0099-link.md")
    assert "symlink" in r.stderr


def test_git_ls_files_con_rc_desconocido_falla_cerrado(w):
    (w.r / ".git" / "index").write_bytes(b"basura")
    r = _blocks(w, "rm docs/adr/0036-borrador.md")
    assert "Falla cerrado" in r.stderr


def test_git_C_se_resuelve_desde_otro_cwd(w):
    _blocks(w, "git -C @R@ rm docs/adr/0001-a.md", cwd=w.out)
    _blocks(w, "git -C ../r rm docs/adr/0001-a.md", cwd=w.out)
    _passes(w, "git -C @R@ rm docs/adr/0036-borrador.md", cwd=w.out)


def test_shell_con_script_por_heredoc_o_stdin_bloquea(w):
    _blocks(w, "bash <<'EOF'\nrm docs/adr/0001-a.md\nEOF")
    _blocks(w, "sh -s <<'EOF'\nrm docs/adr/0001-a.md\nEOF")
    _passes(w, "bash <<'EOF'\nrm docs/adr/0036-borrador.md\nEOF")


def test_glob_tras_normalizar_un_directorio_inexistente_bloquea(w):
    _blocks(w, "rm docs/nonexistente/../adr/*.md")


def test_error_inesperado_en_adr_guard_falla_cerrado(w):
    # stdin con bytes que no son UTF-8: la lectura lanza UnicodeDecodeError.
    guard = HOOK.parent / "adr_guard.py"
    e = dict(w.env, PYTHONIOENCODING="utf-8:strict")
    r = subprocess.run(
        [sys.executable, str(guard), "bash", "--cwd", str(w.r)],
        input=b"echo \xff\xfe",
        capture_output=True,
        env=e,
    )  # fmt: skip
    assert r.returncode == 2
    assert b"error inesperado" in r.stderr
    assert b"Falla cerrado" in r.stderr


# ---- Casos legitimos que deben pasar (16-23) ----

PASS: dict[str, str] = {
    # 16 lectura
    "cat": f"cat {ADR}",
    "grep-glob": "grep -n x docs/adr/*.md",
    "sed-n": f"sed -n '1,3p' {ADR}",
    "sed-sin-i": f"sed 's/a/b/' {ADR}",
    "head": f"head -n 2 {ADR}",
    "wc": f"wc -l {ADR}",
    "ls": "ls docs/adr",
    # 17 lectura con salida fuera
    "cat-a-fuera": f"cat {ADR} > @T@/c.md",
    "cp-a-fuera": f"cp {ADR} @T@/",
    "git-show-a-fuera": f"git show HEAD:{ADR} > @T@/x",
    # 18 git de lectura y add
    "git-diff": "git diff -- docs/adr",
    "git-log-follow": f"git log --follow -- {ADR}",
    "git-show": "git show HEAD -- docs/adr",
    "git-add-borrador": "git add docs/adr/0099-borrador.md",
    "git-add-A": "git add -A",
    # 19 borradores sin rastrear
    "borrador-rm": "rm docs/adr/borrador.md",
    "borrador-sed-i": "sed -i '' 's/b/c/' docs/adr/0036-borrador.md",
    "borrador-mv": "mv docs/adr/borrador.md docs/adr/0036-nuevo.md",
    "borrador-redir": "echo x > docs/adr/0036-nuevo.md",
    "borrador-python": "python3 -c \"open('docs/adr/0036-nuevo.md','w').write('x')\"",
    # 20 worktrees
    "worktree-add": "git worktree add @T@/w2 -b nueva",
    "worktree-remove": "git worktree remove --force @B@",
    "worktree-prune": "git worktree prune",
    # 21 corrientes
    "uv-pytest": "uv run pytest",
    "rm-f-fuera": "rm -f @T@/x",
    "rm-r-cache": "rm -r backend/.pytest_cache",
    "mv-a-b": "mv a b",
    "echo-notas": "echo x > notas.md",
    "rm-commit-approved": "rm .claude/.commit-approved",
    "rm-r-docs-otra": "rm -r docs/otra",
    # 22 mencion en texto
    "echo-menciona": 'echo "ver docs/adr/0001-a.md"',
    # 23 limites aceptados
    "git-checkout": f"git checkout -- {ADR}",
    "git-restore": f"git restore {ADR}",
    "touch": f"touch {ADR}",
}


@pytest.mark.parametrize("command", list(PASS.values()), ids=list(PASS))
def test_legitimo_pasa(w, command):
    _passes(w, command)


def test_commit_con_ruta_en_mensaje_y_aprobacion_pasa(w):
    _passes(w, 'git commit -m "docs: add docs/adr/0036-x.md"', approved=True)


def test_commit_con_heredoc_y_ruta_en_cuerpo_pasa(w):
    cmd = "git commit -F - <<'EOF'\ndocs: add adr\n\nver docs/adr/0036-x.md\nEOF"
    _passes(w, cmd, approved=True)


# ---- Regresion de las reglas 1-4 (sin tests hasta T97) ----

API_VAR = "ANTHROPIC_API" + "_KEY"
ENDPOINT = "https://api.anthropic" + ".com/v1/messages"
ATTRIB = "Co-authored" + "-by: Claude <noreply@anthropic.com>"
GEN = "Generated " + "with Claude Code"
NOVERIFY = "--no" + "-verify"
PUSH_F = "git push origin main --for" + "ce"
DOWN_V = "docker compose down " + "-v"
RM_ROOT = "rm -r" + "f /tmp/zz"
TRAEFIK = "echo traef" + "ik"


@pytest.mark.parametrize(
    "command",
    [
        f"export {API_VAR}=x",
        f"curl {ENDPOINT}",
    ],
    ids=["variable-api-key", "endpoint-v1-messages"],
)
def test_regla1_api_bloquea(w, command):
    r = _blocks(w, command)
    assert "suscripción" in r.stderr


@pytest.mark.parametrize("text", [ATTRIB, GEN], ids=["co-authored", "generated"])
def test_regla2_commit_con_atribucion_bloquea(w, text):
    r = _blocks(w, f'git commit -m "feat: x\n\n{text}"', approved=True)
    assert "atribución" in r.stderr


def test_regla2_commit_no_verify_bloquea(w):
    r = _blocks(w, f'git commit {NOVERIFY} -m "feat: x"', approved=True)
    assert NOVERIFY in r.stderr


def test_regla2_commit_sin_aprobacion_bloquea(w):
    r = _blocks(w, 'git commit -m "feat: x"')
    assert "aprobación" in r.stderr


def test_regla3_traefik_bloquea(w):
    _blocks(w, TRAEFIK)


@pytest.mark.parametrize(
    "command", [RM_ROOT, PUSH_F, DOWN_V], ids=["rm-rf-raiz", "push-forzado", "compose-down-v"]
)
def test_regla4_destructivos_bloquean(w, command):
    r = _blocks(w, command)
    assert "destructivo" in r.stderr


def test_comando_inocuo_pasa(w):
    _passes(w, "ls")


def test_comando_vacio_pasa(w):
    _passes(w, "")


@pytest.mark.parametrize("raw", ["no es json", ""], ids=["no-json", "vacio"])
def test_json_invalido_o_vacio_falla_cerrado(w, raw):
    _blocks(w, "x", stdin=raw)


# ---- T97, correcciones de revision ----


@pytest.mark.parametrize("redir", ["2>/dev/null", "2>&1", "{fd}>/dev/null", "1>/dev/null"])
@pytest.mark.parametrize("cmd", ["cp @T@/x {adr}", "install @T@/x {adr}", "ln -sf @T@/x {adr}"])
def test_descriptor_pegado_a_redireccion_no_oculta_destino(w, cmd, redir):
    _blocks(w, f"{cmd.format(adr=ADR)} {redir}")


@pytest.mark.parametrize("redir", ["2>/dev/null", "2>&1"])
def test_descriptor_pegado_mv_bloquea(w, redir):
    _blocks(w, f"mv {ADR} @T@/y.md {redir}")
    _blocks(w, f"mv @T@/x {ADR} {redir}")


def test_descriptor_pegado_lectura_pasa(w):
    _passes(w, f"cp {ADR} @T@/y 2>&1")
    _passes(w, f"cp {ADR} @T@/y 2>/dev/null")
    _passes(w, "uv run pytest -q 2>&1")
    _passes(w, "ls 2>/dev/null")


def test_numero_como_operando_sigue_siendo_palabra(w):
    _blocks(w, f"rm 2 {ADR}")


def test_muchas_rutas_terminan_rapido_y_bloquean(w):
    import time

    files = " ".join(f"docs/adr/n{i:04d}.md" for i in range(500))
    t = time.monotonic()
    _passes(w, f"rm {files}")
    assert time.monotonic() - t < 6
    t = time.monotonic()
    _blocks(w, f"rm {files} {ADR}")
    assert time.monotonic() - t < 6


def test_plazo_agotado_falla_cerrado(w):
    r = _blocks(
        w,
        "rm docs/adr/0036-borrador.md",
        extra_env={"NOCTURNA_ADR_GUARD_DEADLINE_S": "0.000001"},
    )
    assert "Falla cerrado" in r.stderr
    assert "plazo" in r.stderr


def test_directorio_con_adr_publicados_mensaje_de_arbol(w):
    r = _blocks(w, "find . -name '*.pyc' -delete")
    assert "contiene ADR publicados" in r.stderr
    assert "git worktree remove" in r.stderr
    assert "docs/adr/" in r.stderr
    r = _blocks(w, "rm -r docs")
    assert "contiene ADR publicados" in r.stderr


def test_find_acotado_pasa(w):
    _passes(w, "find backend -name '*.pyc' -delete")


def test_fichero_publicado_mantiene_su_mensaje(w):
    r = _blocks(w, f"rm {ADR}")
    assert "es un ADR que ya está en git" in r.stderr


@pytest.mark.parametrize("word", ["node", "python", "perl", "ruby"])
def test_interprete_como_argumento_no_es_interprete(w, word):
    _passes(w, f"grep -rn {word} {ADR}")
    _passes(w, f"grep -n {word} {ADR}")


def test_interprete_tras_lanzador_o_wrapper_sigue_bloqueando(w):
    _blocks(w, "uv run python -c \"open('docs/adr/0001-a.md','w')\"")
    _blocks(w, "python3 -c \"open('docs/adr/0001-a.md','w')\"")
    _blocks(w, "sudo env X=1 python3 -c \"open('docs/adr/0001-a.md','w')\"")
    _blocks(w, "npx node -e \"require('fs').rmSync('docs/adr/0001-a.md')\"")
    _blocks(w, "pnpm exec node -e \"require('fs').rmSync('docs/adr/0001-a.md')\"")


def test_fallback_de_analisis_no_bloquea_palabras_con_adr(w):
    _passes(w, "echo 'madrugada del padre y el cuadro")
    _blocks(w, "echo 'docs/adr")
    _blocks(w, "cd docs; rm adr/x 'sin cerrar")
    _blocks(w, "echo 'sin cerrar ADR")


@pytest.mark.parametrize("val", ["1000", "1e9", "inf", "nan"])
def test_plazo_por_entorno_tiene_tope(w, val):
    env = {"NOCTURNA_ADR_GUARD_DEADLINE_S": val}
    _passes(w, "rm docs/adr/0036-borrador.md", extra_env=env)
    r = _blocks(w, "rm docs/adr/0001-a.md", extra_env=env)
    assert "es un ADR que ya está en git" in r.stderr


def test_plazo_por_entorno_no_numerico_se_ignora(w):
    env = {"NOCTURNA_ADR_GUARD_DEADLINE_S": "abc"}
    _passes(w, "rm docs/adr/0036-borrador.md", extra_env=env)


def test_plazo_cero_o_negativo_bloquea(w):
    for val in ("0", "-5"):
        r = _blocks(
            w, "rm docs/adr/0036-borrador.md", extra_env={"NOCTURNA_ADR_GUARD_DEADLINE_S": val}
        )
        assert "plazo" in r.stderr


def test_agrupado_entre_repos(w):
    (w.s / "docs" / "adr" / "0001-borrador.md").write_text("b")
    draft_r = "@R@/docs/adr/0036-borrador.md"
    pub_b = "@B@/docs/adr/0005-b.md"
    _blocks(w, f"rm {draft_r} {pub_b}")
    _blocks(w, f"rm {pub_b} {draft_r}")
    _passes(w, f"rm {draft_r} @S@/docs/adr/0001-borrador.md")
