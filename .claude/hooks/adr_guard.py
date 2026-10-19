#!/usr/bin/env python3
"""Guard de ADR publicados (T97). Solo biblioteca estandar.

Uso:
    python3 adr_guard.py bash  [--cwd DIR]          # comando Bash por stdin
    python3 adr_guard.py write [--cwd DIR] -- PATH  # ruta de Edit/Write

Salida: exit 0 pasa; exit 2 bloquea con el motivo en stderr. Falla cerrado: cualquier
error inesperado bloquea.

Un ADR esta "publicado" si git lo conoce (commit o indice) en el repo/worktree del propio
fichero: `git -C <dir padre> rev-parse --show-toplevel` y, en bloque por repo y en lotes,
`git ls-files -z -- ":(literal,icase)<ruta>" ...` (publicado si devuelve la ruta; rc distinto
de 0 o salida que no casa: bloquea), con el entorno sin GIT_DIR/GIT_WORK_TREE/GIT_INDEX_FILE.
Un borrador sin rastrear se puede tocar. Un symlink dentro de docs/adr bloquea siempre.
Las consultas se agrupan: un `git ls-files -z -- <pathspecs>` por repo (por lotes de 400).
Plazo global de 6 s (el hook tiene timeout=10; pasado, Claude Code ejecuta la herramienta):
al agotarse se bloquea, "Falla cerrado". NOCTURNA_ADR_GUARD_DEADLINE_S lo cambia, solo para
tests.

Analisis de Bash: se quitan los cuerpos de here-docs, se trocea el comando con un
tokenizador propio (respeta comillas, que shlex no expone: un ";" entre comillas no es
un separador) por ; && || | & saltos de linea y parentesis, se sigue `cd` literal y se
extraen los destinos de escritura de cada segmento (ver `Guard.collect`).

Limites conocidos (no se cubren; se aceptan):
  - Rutas construidas en tiempo de ejecucion: variables ($X), concatenaciones, `docs/` +
    `adr` en Python, f-strings con la ruta partida, `for f in ...; do rm "$f"`. Solo se
    bloquea si el propio argumento o el texto del comando menciona docs/adr.
  - `cd` con variable, `cd -`, pushd/popd: base desconocida; solo bloquea si el texto
    contiene docs/adr.
  - Comandos que escriben y no estan en la lista: patch, ed, ex, vim, awk -i inplace,
    sponge, rsync, git apply/am/checkout/restore/reset/stash/clean, touch, chmod. Lo de
    git checkout/restore/touch/chmod se acepta a proposito (no cambian el contenido
    commiteado de forma persistente o el autor los usa a mano).
  - Directorios que contienen ADR: se mira lo que git tiene en el indice bajo el
    directorio; un directorio fuera de cualquier repo (que podria contener repos) pasa.
    `cp -r X docs/` que pisaria docs/adr no se detecta.
  - Interpretes (python, perl, ruby, node): se reconocen en posicion de comando, tras
    sudo/env/timeout/command... y lanzadores `uv run`, `pnpm exec|dlx`, `npx`; `grep node
    docs/adr/x` no cuenta. Solo literales de ruta ADR en -c/-e, en
    here-docs y en el fichero de script (si existe y pesa < 256 KB). No distinguen
    lectura de escritura: un python que solo lee un ADR publicado tambien bloquea (usa
    cat/grep/Read). Un literal que apunta a un directorio no bloquea (shutil.rmtree no
    se detecta). Scripts de shell por fichero (`bash script.sh`) no se analizan.
  - Pipes: `xargs` solo se resuelve si otro segmento del mismo pipeline menciona un ADR
    publicado o un directorio que los contiene (`ls docs/adr | xargs rm`); la entrada
    que viene de `cat lista | xargs rm` o `git ls-files | xargs rm` pasa.
  - `find -exec`: solo si el comando ejecutado escribe sobre `{}`; `find` con `-delete`
    siempre se mira por sus puntos de partida.
  - Sustituciones `$(...)` y `` `...` `` se analizan con el cwd inicial del texto, y
    `cd` dentro de un pipeline no se aisla. Los scopes de subshell `( ... )` si.
  - Comillas: tras quitarlas, un glob entrecomillado se expande igual (conservador).
    Expansion de llaves solo con coma, sin anidar entre grupos distintos de forma
    exhaustiva (tope de 256 variantes).
  - Globs se expanden con el sistema de ficheros actual; un glob sin coincidencias se
    toma literal.
  - Si el comando no se puede trocear (comillas sin cerrar), bloquea solo si contiene la
    palabra "adr" suelta (no "padre", "madrugada"); `cd docs; rm adr/x` sigue bloqueando.
  - Un cwd distinto entre la herramienta y el JSON se resuelve con el del JSON.
"""

from __future__ import annotations

import glob
import math
import os
import re
import subprocess
import sys
import time

SENT = "\x00STDIN\x00"  # marca "los argumentos llegan por stdin" (xargs, find -exec {})
ADR_TEXT_RE = re.compile(r"docs/+(?:\./+)*adr", re.I)
ADR_LIT_RE = re.compile(
    r"""(?P<pre>[^\s'"()=,;]*?)(?P<tail>docs/+(?:\./+)*adr(?:/+[^\s'"()\\,;]*)?)""", re.I
)
ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
INTERP_RE = re.compile(r"^(python|python\d+(\.\d+)*|perl|ruby|node|nodejs)$")
SHELLS = {"bash", "sh", "zsh", "dash", "ksh"}
KEYWORDS = {"then", "do", "else", "elif", "if", "while", "until", "{", "}", "!", "fi", "done"}
WRAPPERS = {"sudo", "doas", "command", "builtin", "exec", "nohup", "time", "nice", "ionice",
            "stdbuf", "timeout", "env"}  # fmt: skip
MAX_SCRIPT = 256 * 1024
DEADLINE_S = 6.0  # el hook tiene timeout=10 en settings.json; pasado, Claude Code ejecuta (abierto)
DEADLINE_ENV = "NOCTURNA_ADR_GUARD_DEADLINE_S"  # solo para tests: fuerza otro plazo global
GIT_BATCH = 400  # pathspecs por `git ls-files`
FD_RE = re.compile(r"^(\d+|\{[A-Za-z_][A-Za-z0-9_]*\})$")
ADR_WORD_RE = re.compile(r"(?<![a-z])adr(?![a-z])", re.I)
LAUNCHERS = {"uv": ("run",), "pnpm": ("exec", "dlx"), "npx": (), "pnpx": (), "bunx": ()}
LAUNCHER_ARGOPTS = {"--with", "-w", "--python", "-p", "--project", "--directory", "--env-file",
                    "--extra", "--group", "--with-requirements", "-C", "-c",
                    "--package", "--prefix", "--workspace"}  # fmt: skip
OPS = sorted(
    ["&>>", "<<<", "<<-", "&&", "||", ";;", ";&", "|&", ">>", ">|", ">&", "&>", "<<", "<&",
     "<>", ">", "<", "|", "&", ";"],
    key=len,
    reverse=True,
)  # fmt: skip


class Block(Exception):
    """Bloqueo con mensaje listo para stderr."""


class ParseError(Exception):
    """El comando no se puede trocear (comillas sin cerrar, etc.)."""


def published_msg(path: str) -> str:
    return (
        f"BLOQUEADO: {path} es un ADR que ya está en git (commit o índice); no se modifica, "
        "mueve ni borra por Bash, se supersede con uno nuevo. Para leerlo, Read/cat/grep. (T97)"
    )


def tree_msg(directory: str, example: str) -> str:
    return (
        f"BLOQUEADO: el directorio {directory} contiene ADR publicados (p. ej. {example}); "
        "acota la ruta (p. ej. find backend …) o usa git worktree remove para un worktree. (T97)"
    )


def unknown_msg(path: str, why: str) -> str:
    return f"BLOQUEADO: no se pudo comprobar si {path} es un ADR publicado ({why}). Falla cerrado."


def has_pair(path: str) -> bool:
    """True si la ruta pasa por docs/adr (sin distinguir mayusculas) y hay algo debajo."""
    comps = [c.lower() for c in path.split("/") if c not in ("", ".")]
    return any(comps[i] == "docs" and comps[i + 1] == "adr" and i + 2 < len(comps)
               for i in range(len(comps) - 2))  # fmt: skip


def brace_expand(s: str, limit: int = 256) -> list[str]:
    m = re.search(r"\{([^{}]*,[^{}]*)\}", s)
    if not m:
        return [s]
    out: list[str] = []
    for alt in m.group(1).split(","):
        out.extend(brace_expand(s[: m.start()] + alt + s[m.end() :], limit))
        if len(out) > limit:
            break
    return out[:limit]


# ---------------------------------------------------------------- tokenizador


def _match_paren(text: str, start: int) -> int:
    depth = 0
    for j in range(start, len(text)):
        if text[j] == "(":
            depth += 1
        elif text[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    raise ParseError("parentesis sin cerrar")


def tokenize(text: str) -> tuple[list[tuple[str, str]], list[str], dict[int, str]]:
    """Devuelve (tokens, sustituciones $(...), cuerpos de here-doc por indice).

    Tokens: ('w', palabra) o ('op', operador). Las comillas quedan quitadas en la palabra.
    """
    toks: list[tuple[str, str]] = []
    subs: list[str] = []
    bodies: dict[int, str] = {}
    pending: list[tuple[int, str, bool]] = []
    n = len(text)
    i = 0
    buf: list[str] = []
    inw = False
    counter = 0

    def flush() -> None:
        nonlocal buf, inw
        if inw:
            toks.append(("w", "".join(buf)))
        buf = []
        inw = False

    while i < n:
        c = text[i]
        if c in " \t\r":
            flush()
            i += 1
        elif c == "\n":
            flush()
            toks.append(("op", ";"))
            i += 1
            for idx, delim, strip in pending:
                lines: list[str] = []
                while i < n:
                    j = text.find("\n", i)
                    j = n if j == -1 else j
                    line = text[i:j]
                    i = j + 1
                    cmp = line.rstrip("\r")
                    if strip:
                        cmp = cmp.lstrip("\t")
                    if cmp == delim:
                        break
                    lines.append(line)
                bodies[idx] = "\n".join(lines)
            pending = []
        elif c == "\\":
            if i + 1 < n:
                if text[i + 1] != "\n":
                    buf.append(text[i + 1])
                    inw = True
                i += 2
            else:
                buf.append("\\")
                inw = True
                i += 1
        elif c == "'":
            j = text.find("'", i + 1)
            if j == -1:
                raise ParseError("comilla simple sin cerrar")
            buf.append(text[i + 1 : j])
            inw = True
            i = j + 1
        elif c == '"':
            i += 1
            inw = True
            closed = False
            while i < n:
                ch = text[i]
                if ch == '"':
                    closed = True
                    i += 1
                    break
                if ch == "\\" and i + 1 < n and text[i + 1] in '$`"\\\n':
                    if text[i + 1] != "\n":
                        buf.append(text[i + 1])
                    i += 2
                elif ch == "$" and text[i + 1 : i + 2] == "(":
                    j = _match_paren(text, i + 1)
                    subs.append(text[i + 2 : j])
                    buf.append(text[i : j + 1])
                    i = j + 1
                elif ch == "`":
                    j = text.find("`", i + 1)
                    if j == -1:
                        raise ParseError("backtick sin cerrar")
                    subs.append(text[i + 1 : j])
                    buf.append(text[i : j + 1])
                    i = j + 1
                else:
                    buf.append(ch)
                    i += 1
            if not closed:
                raise ParseError("comilla doble sin cerrar")
        elif c == "$" and text[i + 1 : i + 2] == "(":
            j = _match_paren(text, i + 1)
            subs.append(text[i + 2 : j])
            buf.append(text[i : j + 1])
            inw = True
            i = j + 1
        elif c == "`":
            j = text.find("`", i + 1)
            if j == -1:
                raise ParseError("backtick sin cerrar")
            subs.append(text[i + 1 : j])
            buf.append(text[i : j + 1])
            inw = True
            i = j + 1
        elif c == "#" and not inw:
            j = text.find("\n", i)
            i = n if j == -1 else j
        elif c in "()":
            flush()
            toks.append(("op", c))
            i += 1
        elif c in ";&|<>":
            if c in "<>" and inw and FD_RE.match("".join(buf)):
                buf, inw = [], False  # descriptor pegado (2>, {fd}>): no es una palabra
            flush()
            if c in "<>" and text[i + 1 : i + 2] == "(":
                j = _match_paren(text, i + 1)
                subs.append(text[i + 2 : j])
                toks.append(("w", "/dev/fd/subst"))
                i = j + 1
                continue
            op = next(o for o in OPS if text.startswith(o, i))
            i += len(op)
            if op in ("<<", "<<-"):
                while i < n and text[i] in " \t":
                    i += 1
                d: list[str] = []
                while i < n and text[i] not in " \t\r\n;&|<>()":
                    ch = text[i]
                    if ch in "'\"":
                        j = text.find(ch, i + 1)
                        if j == -1:
                            raise ParseError("delimitador sin cerrar")
                        d.append(text[i + 1 : j])
                        i = j + 1
                    elif ch == "\\":
                        i += 1
                    else:
                        d.append(ch)
                        i += 1
                pending.append((counter, "".join(d), op == "<<-"))
                toks.append(("op", "<<"))
                toks.append(("w", f"__HD{counter}__"))
                counter += 1
            else:
                toks.append(("op", op))
        else:
            buf.append(c)
            inw = True
            i += 1
    flush()
    return toks, subs, bodies


class Seg:
    def __init__(self) -> None:
        self.words: list[str] = []
        self.targets: list[str] = []
        self.heredocs: list[str] = []
        self.group = 0


def build(toks: list[tuple[str, str]], bodies: dict[int, str]) -> list[tuple[str, Seg | None]]:
    items: list[tuple[str, Seg | None]] = []
    cur = Seg()
    group = 0

    def finish() -> None:
        nonlocal cur
        if cur.words or cur.targets or cur.heredocs:
            cur.group = group
            items.append(("seg", cur))
        cur = Seg()

    i = 0
    n = len(toks)
    while i < n:
        kind, v = toks[i]
        i += 1
        if kind == "w":
            cur.words.append(v)
            continue
        nxt = toks[i][1] if i < n and toks[i][0] == "w" else None
        if v in ("|", "|&"):
            finish()
        elif v == "(":
            finish()
            group += 1
            items.append(("push", None))
        elif v == ")":
            finish()
            group += 1
            items.append(("pop", None))
        elif v in (";", ";;", ";&", "&&", "||", "&"):
            finish()
            group += 1
        elif v in (">", ">>", ">|", "&>", "&>>", ">&"):
            if nxt is not None:
                i += 1
                if not (v == ">&" and (nxt.isdigit() or nxt == "-")):
                    cur.targets.append(nxt)
        elif v in ("<", "<&", "<>"):
            if nxt is not None:
                i += 1
        elif v == "<<":
            if nxt is not None:
                i += 1
                m = re.fullmatch(r"__HD(\d+)__", nxt)
                if m:
                    cur.heredocs.append(bodies.get(int(m.group(1)), ""))
        elif v == "<<<":
            if nxt is not None:
                i += 1
                cur.heredocs.append(nxt)
    finish()
    return items


# ---------------------------------------------------------------------- guard


def parse_opts(
    args: list[str], argopts: tuple[str, ...] = ()
) -> tuple[list[str], dict[str, str], list[str]]:
    """Separa (flags, valores de opciones con argumento, operandos)."""
    flags: list[str] = []
    vals: dict[str, str] = {}
    ops: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        i += 1
        if a == "--":
            ops.extend(args[i:])
            break
        if a.startswith("--") and "=" in a:
            k, _, v = a.partition("=")
            vals[k] = v
            flags.append(k)
        elif a in argopts and i < len(args):
            vals[a] = args[i]
            flags.append(a)
            i += 1
        elif a.startswith("-") and len(a) > 1:
            flags.append(a)
        else:
            ops.append(a)
    return flags, vals, ops


def strip_prefix(words: list[str]) -> list[str]:
    w = list(words)
    while w:
        if ASSIGN_RE.match(w[0]):
            w.pop(0)
            continue
        b = os.path.basename(w[0])
        if b in KEYWORDS:
            w.pop(0)
            continue
        if b in WRAPPERS:
            w.pop(0)
            while w and w[0].startswith("-") and len(w[0]) > 1:
                o = w.pop(0)
                takes = {"sudo": "-u -g -h -p -C -D -R -T -U", "nice": "-n", "env": "-u -C -S",
                         "timeout": "-s -k"}.get(b, "")  # fmt: skip
                if o in takes.split() and w:
                    w.pop(0)
            if b == "timeout" and w and w[0][:1].isdigit():
                w.pop(0)
            continue
        break
    return w


def _sed_inplace(flags: list[str]) -> bool:
    for f in flags:
        if f.startswith("--"):
            if f.split("=")[0] == "--in-place":
                return True
            continue
        for ch in f[1:]:
            if ch == "i":
                return True
            if ch in "efl":
                break
    return False


def _perl_inplace(flags: list[str]) -> bool:
    for f in flags:
        if f.startswith("--"):
            continue
        for ch in f[1:]:
            if ch == "i":
                return True
            if ch not in "pnalsw0":
                break
    return False


class Guard:
    def __init__(self, cwd: str) -> None:
        self.cwd0 = cwd
        self.fulltext = ""
        self._top: dict[str, str | None] = {}
        self._pending: dict[str, dict[str, str]] = {}  # top -> {rel: ruta original}
        try:
            limit = float(os.environ.get(DEADLINE_ENV, DEADLINE_S))
        except ValueError:
            limit = DEADLINE_S
        if not math.isfinite(limit) or limit > DEADLINE_S:
            limit = DEADLINE_S
        self.deadline = time.monotonic() + limit

    def tick(self) -> None:
        if time.monotonic() >= self.deadline:
            raise Block(unknown_msg("el comando", "plazo agotado"))

    # ---- git

    def _git(self, args: list[str], what: str) -> subprocess.CompletedProcess[bytes]:
        env = {k: v for k, v in os.environ.items()
               if k not in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR",
                            "GIT_PREFIX")}  # fmt: skip
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            raise Block(unknown_msg(what, "plazo agotado"))
        try:
            return subprocess.run(["git", *args], env=env, capture_output=True, timeout=remaining)
        except FileNotFoundError:
            raise Block(unknown_msg(what, "git no está disponible")) from None
        except (subprocess.TimeoutExpired, OSError) as e:
            raise Block(unknown_msg(what, f"git falló: {type(e).__name__}")) from None

    def _toplevel(self, d: str, what: str) -> str | None:
        if d not in self._top:
            r = self._git(["-C", d, "rev-parse", "--show-toplevel"], what)
            self._top[d] = (
                os.path.realpath(os.fsdecode(r.stdout).rstrip("\n")) if r.returncode == 0 else None
            )
        return self._top[d]

    # ---- comprobaciones de ruta

    def check_file(self, c: str) -> None:
        parent, name = os.path.dirname(c), os.path.basename(c)
        if not name:
            return
        if not os.path.isdir(parent):
            raise Block(unknown_msg(c, "el directorio padre no existe"))
        if os.path.islink(c):
            raise Block(
                f"BLOQUEADO: {c} está en docs/adr y es un symlink; no se escribe en un ADR "
                "a través de un symlink. Falla cerrado."
            )
        rp = os.path.realpath(parent)
        top = self._toplevel(rp, c)
        if top is None:
            raise Block(unknown_msg(c, "no está en un repositorio git"))
        rel = os.path.relpath(os.path.join(rp, name), top)
        if rel.startswith(".."):
            raise Block(unknown_msg(c, "queda fuera de la raíz del repositorio"))
        self._pending.setdefault(top, {})[rel] = c  # se consulta en bloque en `flush`

    def flush(self) -> None:
        """Una consulta `git ls-files` (por lotes) por repo con todos los pathspecs pendientes."""
        pending, self._pending = self._pending, {}
        for top, rels in pending.items():
            items = list(rels.items())
            for k in range(0, len(items), GIT_BATCH):
                chunk = items[k : k + GIT_BATCH]
                specs = [f":(literal,icase){rel}" for rel, _ in chunk]
                r = self._git(["-C", top, "ls-files", "-z", "--", *specs], chunk[0][1])
                if r.returncode != 0:
                    raise Block(unknown_msg(chunk[0][1], f"git ls-files devolvió {r.returncode}"))
                tracked = [os.fsdecode(f) for f in r.stdout.split(b"\x00") if f]
                if not tracked:
                    continue
                low = {t.lower() for t in tracked}
                for rel, orig in chunk:
                    if rel.lower() in low:
                        raise Block(published_msg(orig))
                # Salida que no casa por nombre (normalizacion unicode): falla cerrado.
                raise Block(published_msg(os.path.join(top, tracked[0])))

    def check_tree(self, c: str) -> None:
        d = os.path.realpath(c)
        top = self._toplevel(d, c)
        if top is None:
            return
        r = self._git(["-C", d, "ls-files", "-z"], c)
        if r.returncode != 0:
            raise Block(unknown_msg(c, f"git ls-files devolvió {r.returncode}"))
        for f in r.stdout.split(b"\x00"):
            if f:
                full = os.path.join(d, os.fsdecode(f))
                if has_pair(full):
                    raise Block(tree_msg(c, full))

    def check(self, p: str, tree: bool) -> None:
        if p == SENT or "\x00" in p:
            return
        parent, base = os.path.dirname(p), os.path.basename(p)
        cands = [p]
        if base:
            cands.append(os.path.join(os.path.realpath(parent), base))
        cands.append(os.path.realpath(p))
        seen: set[str] = set()
        for c in cands:
            if c in seen:
                continue
            seen.add(c)
            if os.path.isdir(c):
                if tree:
                    self.check_tree(c)
            elif has_pair(c):
                self.check_file(c)

    # ---- resolucion de rutas

    def res(self, raw: str, cwd: str | None) -> list[str]:
        if raw == SENT:
            return [SENT]
        if not raw:
            return []
        if "$" in raw or "`" in raw:
            if re.search("adr", raw, re.I) or ADR_TEXT_RE.search(self.fulltext):
                raise Block(unknown_msg(raw, "la ruta usa variables o sustituciones"))
            return []
        out: list[str] = []
        for r in brace_expand(raw):
            p = os.path.expanduser(r)
            if not os.path.isabs(p):
                if cwd is None:
                    if ADR_TEXT_RE.search(self.fulltext):
                        raise Block(unknown_msg(raw, "directorio de trabajo desconocido"))
                    continue
                p = os.path.join(cwd, p)
            p = os.path.normpath(p)
            if any(ch in p for ch in "*?["):
                ms = sorted(glob.glob(p))
                out.extend(ms if ms else [p])
            else:
                out.append(p)
        return out

    def dest_paths(
        self, srcs: list[str], dest_raw: str, cwd: str | None, force_dir: bool = False
    ) -> list[str]:
        out: list[str] = []
        for d in self.res(dest_raw, cwd):
            if force_dir or dest_raw.endswith("/") or os.path.isdir(d):
                out.extend(os.path.join(d, os.path.basename(s.rstrip("/"))) for s in srcs)
            else:
                out.append(d)
        return out

    # ---- extraccion de escrituras de un comando

    def collect(self, words: list[str], cwd: str | None) -> list[tuple[str, bool]]:
        """Destinos de escritura (ruta absoluta, es_arbol) de un comando ya sin prefijos."""
        words = strip_prefix(words)
        if not words:
            return []
        cmd, args = os.path.basename(words[0]), words[1:]
        out: list[tuple[str, bool]] = []

        def res_all(raws: list[str], tree: bool) -> list[tuple[str, bool]]:
            return [(p, tree) for r in raws for p in self.res(r, cwd)]

        if cmd in ("rm", "unlink", "shred"):
            _, _, ops = parse_opts(args)
            out += res_all(ops, True)
        elif cmd in ("mv", "cp", "install", "ln"):
            flags, vals, ops = parse_opts(args, ("-t", "--target-directory", "-S", "--suffix"))
            tdir = vals.get("-t") or vals.get("--target-directory")
            if cmd == "ln" and not tdir and len(ops) == 1:
                srcs, dest = ops, os.path.basename(ops[0].rstrip("/"))
            elif tdir:
                srcs, dest = ops, tdir
            elif len(ops) >= 2:
                srcs, dest = ops[:-1], ops[-1]
            else:
                return out
            src_paths = [p for r in srcs for p in self.res(r, cwd)]
            if cmd == "mv":
                out += [(p, True) for p in src_paths]
            out += [(p, False) for p in self.dest_paths(src_paths, dest, cwd, bool(tdir))]
        elif cmd == "git":
            out += self._git_writes(args, cwd)
        elif cmd == "tee":
            _, _, ops = parse_opts(args)
            out += res_all(ops, False)
        elif cmd == "truncate":
            _, _, ops = parse_opts(args, ("-s", "-r", "--size", "--reference"))
            out += res_all(ops, False)
        elif cmd == "dd":
            out += res_all([a[3:] for a in args if a.startswith("of=")], False)
        elif cmd == "sed":
            flags, _, ops = parse_opts(args, ("-e", "-f", "--expression", "--file"))
            if _sed_inplace(flags):
                out += res_all(ops, False)
        elif cmd == "perl":
            flags, _, ops = parse_opts(args, ("-e", "-E"))
            if _perl_inplace(flags):
                out += res_all(ops, False)
        elif cmd == "find":
            out += self._find_writes(args, cwd)
        elif cmd == "xargs":
            out += self._xargs_writes(args, cwd)
        return out

    def _git_writes(self, args: list[str], cwd: str | None) -> list[tuple[str, bool]]:
        gcwd = cwd
        i = 0
        while i < len(args):
            a = args[i]
            if a == "-C" and i + 1 < len(args):
                nxt = args[i + 1]
                if gcwd is None or "$" in nxt or "`" in nxt:
                    gcwd = None
                else:
                    gcwd = os.path.normpath(os.path.join(gcwd, os.path.expanduser(nxt)))
                i += 2
            elif a == "-c" and i + 1 < len(args):
                i += 2
            elif a in ("--git-dir", "--work-tree", "--namespace", "--exec-path"):
                i += 2
            elif a.startswith("-"):
                i += 1
            else:
                break
        if i >= len(args):
            return []
        sub, rest = args[i], args[i + 1 :]
        if sub in ("rm", "mv"):
            return self.collect(["mv" if sub == "mv" else "rm", *rest], gcwd)
        return []

    def _find_writes(self, args: list[str], cwd: str | None) -> list[tuple[str, bool]]:
        start: list[str] = []
        i = 0
        while i < len(args) and args[i] in ("-H", "-L", "-P", "-O1", "-O2", "-O3"):
            i += 1
        while i < len(args) and not (
            args[i].startswith("-") or args[i] in ("(", "!") or args[i] == "__LP__"
        ):
            start.append(args[i])
            i += 1
        if not start:
            start = ["."]
        hit = "-delete" in args
        out: list[tuple[str, bool]] = []
        j = 0
        while j < len(args):
            if args[j] in ("-exec", "-execdir", "-ok", "-okdir"):
                k = j + 1
                sub: list[str] = []
                while k < len(args) and args[k] not in (";", "+"):
                    sub.append(SENT if args[k] == "{}" else args[k])
                    k += 1
                for p, t in self.collect(sub, cwd):
                    if p == SENT:
                        hit = True
                    else:
                        out.append((p, t))
                j = k
            j += 1
        if hit:
            out += [(p, True) for r in start for p in self.res(r, cwd)]
        return out

    def _xargs_writes(self, args: list[str], cwd: str | None) -> list[tuple[str, bool]]:
        i = 0
        repl: str | None = None
        while i < len(args) and args[i].startswith("-") and args[i] != "--":
            a = args[i]
            if a in ("-I", "-J") and i + 1 < len(args):
                repl = args[i + 1]
                i += 1
            elif a.startswith("-I") and len(a) > 2:
                repl = a[2:]
            elif a in ("-i", "--replace"):
                repl = "{}"
            elif a.startswith("--replace="):
                repl = a.split("=", 1)[1]
            elif a in ("-n", "-P", "-L", "-d", "-E", "-s", "-a") and i + 1 < len(args):
                i += 1
            i += 1
        if i < len(args) and args[i] == "--":
            i += 1
        sub = args[i:]
        if repl is not None:
            sub = [SENT if w == repl else w for w in sub]
        else:
            sub = [*sub, SENT]
        return self.collect(sub, cwd)

    # ---- analisis por segmento

    def literals(self, text: str, cwd: str | None) -> None:
        for m in ADR_LIT_RE.finditer(text):
            pre, tail = m.group("pre"), m.group("tail")
            raw = tail if re.search(r"[{$%]", pre) else pre + tail
            for p in self.res(raw, cwd):
                if not os.path.isdir(p):
                    self.check(p, False)

    @staticmethod
    def _interp_index(words: list[str]) -> int | None:
        """Indice del interprete si esta en posicion de comando (tras un lanzador conocido)."""
        words = strip_prefix(words)
        if not words:
            return None
        base = os.path.basename(words[0])
        if base in LAUNCHERS:
            i = 1
            subs = LAUNCHERS[base]
            if subs:
                if len(words) < 2 or words[1] not in subs:
                    return None
                i = 2
            while i < len(words) and words[i].startswith("-"):
                i += 2 if words[i] in LAUNCHER_ARGOPTS else 1
            words = words[i:]
            if not words:
                return None
            base = os.path.basename(words[0])
            if INTERP_RE.match(base):
                return i
            return None
        return 0 if INTERP_RE.match(base) else None

    def interpreter(self, words: list[str], seg: Seg, cwd: str | None) -> None:
        idx = self._interp_index(words)
        if idx is None:
            return
        rest = words[idx + 1 :]
        for src in [*rest, *seg.heredocs]:
            self.literals(src, cwd)
        if cwd is None:
            return
        for w in rest:
            if w.startswith("-") or not w or "\x00" in w:
                continue
            f = os.path.join(cwd, os.path.expanduser(w))
            try:
                if os.path.isfile(f) and os.path.getsize(f) <= MAX_SCRIPT:
                    with open(f, encoding="utf-8", errors="replace") as fh:
                        self.literals(fh.read(), cwd)
            except OSError:
                continue

    def process(
        self, seg: Seg, words: list[str], cwd: str | None, group: list[Seg], depth: int
    ) -> None:
        self.tick()
        for t in seg.targets:
            for p in self.res(t, cwd):
                self.check(p, False)
        writes = self.collect(words, cwd)
        sent = False
        for p, tree in writes:
            if p == SENT:
                sent = True
            else:
                self.check(p, tree)
        if sent:
            # xargs: la entrada llega de otros segmentos del mismo pipeline.
            for other in group:
                if other is seg:
                    continue
                for w in other.words[1:]:
                    for p in self.res(w, cwd):
                        self.check(p, True)
        self.interpreter(words, seg, cwd)
        if not words:
            return
        cmd, args = os.path.basename(words[0]), words[1:]
        if cmd in SHELLS:
            script = None
            for k, a in enumerate(args):
                if a.startswith("-") and not a.startswith("--") and "c" in a:
                    script = args[k + 1] if k + 1 < len(args) else None
                    break
                if not a.startswith("-"):
                    break
            if script is not None:
                self.analyze(script, cwd, depth + 1)
            else:
                for body in seg.heredocs:
                    self.analyze(body, cwd, depth + 1)
        elif cmd == "eval":
            self.analyze(" ".join(args), cwd, depth + 1)

    def _cd(self, words: list[str], cwd: str | None) -> str | None:
        cmd = os.path.basename(words[0])
        if cmd in ("pushd", "popd"):
            return None
        _, _, ops = parse_opts(words[1:])
        if not ops:
            return os.environ.get("HOME") or None
        t = ops[0]
        if t == "-" or "$" in t or "`" in t or cwd is None:
            return None
        return os.path.normpath(os.path.join(cwd, os.path.expanduser(t)))

    def analyze(self, text: str, cwd: str | None, depth: int = 0) -> None:
        if depth > 8:
            if ADR_WORD_RE.search(text):
                raise Block(unknown_msg("el comando", "anidamiento excesivo"))
            return
        try:
            toks, subs, bodies = tokenize(text)
        except ParseError as e:
            if ADR_WORD_RE.search(text):
                raise Block(unknown_msg("el comando", f"no se pudo analizar: {e}")) from None
            return
        items = build(toks, bodies)
        segs = [s for k, s in items if k == "seg" and s is not None]
        stack: list[str | None] = []
        for kind, seg in items:
            if kind == "push":
                stack.append(cwd)
            elif kind == "pop":
                if stack:
                    cwd = stack.pop()
            elif seg is not None:
                words = strip_prefix(seg.words)
                group = [s for s in segs if s.group == seg.group]
                self.process(seg, words, cwd, group, depth)
                if words and os.path.basename(words[0]) in ("cd", "chdir", "pushd", "popd"):
                    cwd = self._cd(words, cwd)
        for s in subs:
            self.analyze(s, self.cwd0 if depth == 0 else cwd, depth + 1)

    def run_bash(self, text: str) -> None:
        self.fulltext = text
        self.analyze(text, self.cwd0)
        self.flush()

    def run_write(self, path: str) -> None:
        p = os.path.normpath(os.path.join(self.cwd0, os.path.expanduser(path)))
        self.check(p, False)
        self.flush()


def main(argv: list[str]) -> int:
    try:
        if len(argv) < 2 or argv[1] not in ("bash", "write"):
            raise Block("BLOQUEADO: uso incorrecto de adr_guard.py. Falla cerrado.")
        mode, rest = argv[1], argv[2:]
        cwd = os.getcwd()
        if rest[:1] == ["--cwd"] and len(rest) >= 2:
            cwd = rest[1] or cwd
            rest = rest[2:]
        g = Guard(os.path.abspath(cwd))
        if mode == "bash":
            g.run_bash(sys.stdin.read())
        else:
            if rest[:1] == ["--"]:
                rest = rest[1:]
            if len(rest) != 1 or not rest[0]:
                raise Block("BLOQUEADO: falta la ruta a comprobar. Falla cerrado.")
            g.run_write(rest[0])
        return 0
    except Block as b:
        print(str(b), file=sys.stderr)
        return 2
    except BaseException as e:  # noqa: BLE001 - falla cerrado ante cualquier error
        print(
            f"BLOQUEADO: error inesperado al comprobar ADR ({type(e).__name__}: {e}). "
            "Falla cerrado.",
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv))
