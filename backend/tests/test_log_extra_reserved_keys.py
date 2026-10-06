"""T90: guarda estatica de las claves de `extra=` en las llamadas de logging.

`Logger.makeRecord` lanza `KeyError("Attempt to overwrite 'created' in
LogRecord")` si una clave de `extra=` coincide con un atributo reservado de
`LogRecord`. Eso solo ocurre con el nivel habilitado, de modo que los tests con
el log desactivado no lo ven (noche del 2026-10-06). Este fichero recorre el AST
de `backend/src/nocturna/**/*.py` y compara las claves con
`nocturna.infrastructure.logging._RESERVED_LOG_RECORD_ATTRS`.

Que claves se resuelven (todo lo demas es un fallo que pide usar un dict
literal o ampliar la guarda):

- un `ast.Dict` literal con claves `str` constantes;
- una llamada a una funcion o metodo definido en el mismo modulo: claves de los
  `Dict` literales que devuelve con `return`;
- un `ast.Name`: asignaciones del nombre en la funcion contenedora (a `Dict` o a
  una llamada resoluble) mas `nombre["k"] = ...` con clave constante.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from nocturna.infrastructure.logging import _RESERVED_LOG_RECORD_ATTRS

SRC = Path(__file__).resolve().parent.parent / "src" / "nocturna"
LOG_METHODS = frozenset(
    {"debug", "info", "warning", "warn", "error", "exception", "critical", "log"}
)
_MAX_DEPTH = 6
# Excepcion explicita y minima: claves de extra= que son una variable. Valores
# posibles por (fichero relativo a src/nocturna, nombre de la variable).
_KEY_VARIABLE_EXCEPTIONS: dict[tuple[str, str], frozenset[str]] = {
    ("infrastructure/arxiv/transport.py", "context_key"): frozenset({"start", "token", "set"}),
}
_HINT = "usa un dict literal en extra= o amplia la guarda de tests/test_log_extra_reserved_keys.py"


@dataclass
class Site:
    file: str
    lineno: int
    keys: set[str] = field(default_factory=set)
    problems: list[str] = field(default_factory=list)

    @property
    def where(self) -> str:
        return f"{self.file}:{self.lineno}"


class _Resolver:
    def __init__(self, tree: ast.Module) -> None:
        self.parents: dict[ast.AST, ast.AST] = {}
        self.functions: dict[str, list[ast.FunctionDef | ast.AsyncFunctionDef]] = {}
        for parent in ast.walk(tree):
            for child in ast.iter_child_nodes(parent):
                self.parents[child] = parent
            if isinstance(parent, ast.FunctionDef | ast.AsyncFunctionDef):
                self.functions.setdefault(parent.name, []).append(parent)
        self.tree = tree

    def enclosing_function(self, node: ast.AST):
        current = self.parents.get(node)
        while current is not None:
            if isinstance(current, ast.FunctionDef | ast.AsyncFunctionDef):
                return current
            current = self.parents.get(current)
        return None

    def resolve(self, node: ast.AST, scope, site: Site, depth: int = 0) -> None:
        if depth > _MAX_DEPTH:
            site.problems.append("resolucion demasiado profunda")
            return
        if isinstance(node, ast.Dict):
            for key in node.keys:
                if key is None:
                    site.problems.append("**spread en un dict")
                elif isinstance(key, ast.Constant) and isinstance(key.value, str):
                    site.keys.add(key.value)
                elif isinstance(key, ast.Name) and (site.file, key.id) in _KEY_VARIABLE_EXCEPTIONS:
                    site.keys |= _KEY_VARIABLE_EXCEPTIONS[(site.file, key.id)]
                else:
                    site.problems.append(f"clave no constante: {ast.unparse(key)}")
        elif isinstance(node, ast.Call):
            self._resolve_call(node, site, depth)
        elif isinstance(node, ast.Name):
            self._resolve_name(node, scope, site, depth)
        else:
            site.problems.append(f"expresion no resoluble: {ast.unparse(node)}")

    def _resolve_call(self, call: ast.Call, site: Site, depth: int) -> None:
        func = call.func
        name = (
            func.id
            if isinstance(func, ast.Name)
            else func.attr
            if isinstance(func, ast.Attribute)
            else None
        )
        defs = self.functions.get(name or "", [])
        if not defs:
            site.problems.append(
                f"llamada a una funcion no definida en el modulo: {ast.unparse(func)}"
            )
            return
        returns = [
            n
            for fn in defs
            for n in ast.walk(fn)
            if isinstance(n, ast.Return) and n.value is not None
        ]
        if not returns:
            site.problems.append(f"{name}() no devuelve ningun dict resoluble")
        for ret in returns:
            self.resolve(ret.value, self.enclosing_function(ret), site, depth + 1)

    def _resolve_name(self, name: ast.Name, scope, site: Site, depth: int) -> None:
        body_root = scope if scope is not None else self.tree
        if scope is not None:
            args = scope.args
            params = {a.arg for a in [*args.posonlyargs, *args.args, *args.kwonlyargs]}
            if args.vararg:
                params.add(args.vararg.arg)
            if args.kwarg:
                params.add(args.kwarg.arg)
            if name.id in params:
                site.problems.append(f"extra={name.id} es un parametro de la funcion")
                return
        found = False
        for node in ast.walk(body_root):
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == name.id:
                        found = True
                        self.resolve(node.value, scope, site, depth + 1)
                    elif (
                        isinstance(target, ast.Subscript)
                        and isinstance(target.value, ast.Name)
                        and target.value.id == name.id
                    ):
                        key = target.slice
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            site.keys.add(key.value)
                        else:
                            site.problems.append(
                                f"{name.id}[{ast.unparse(key)}] con clave no constante"
                            )
            elif (
                isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id == name.id
                and node.value is not None
            ):
                found = True
                self.resolve(node.value, scope, site, depth + 1)
        if not found:
            site.problems.append(f"no se encuentra la asignacion de {name.id}")


def scan_source(source: str, filename: str = "<synthetic>") -> list[Site]:
    tree = ast.parse(source)
    resolver = _Resolver(tree)
    sites: list[Site] = []
    for node in ast.walk(tree):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
        ):
            continue
        for kw in node.keywords:
            if kw.arg == "extra":
                site = Site(filename, node.lineno)
                resolver.resolve(kw.value, resolver.enclosing_function(node), site)
                sites.append(site)
            elif kw.arg is None:
                # log.info("x", **kwargs): podria esconder extra=
                site = Site(filename, node.lineno)
                site.problems.append("**kwargs en una llamada de logging")
                sites.append(site)
    return sites


def scan_src() -> list[Site]:
    sites: list[Site] = []
    for path in sorted(SRC.rglob("*.py")):
        rel = path.relative_to(SRC).as_posix()
        sites.extend(scan_source(path.read_text(encoding="utf-8"), rel))
    return sites


# --- guarda sobre el codigo real ------------------------------------------------


def test_ningun_extra_de_src_usa_atributos_reservados_de_logrecord():
    offenders = [
        f"{s.where}: {sorted(s.keys & _RESERVED_LOG_RECORD_ATTRS)}"
        for s in scan_src()
        if s.keys & _RESERVED_LOG_RECORD_ATTRS
    ]
    assert not offenders, (
        "claves de extra= que chocan con atributos reservados de LogRecord "
        "(KeyError en makeRecord con el nivel habilitado):\n" + "\n".join(offenders)
    )


def test_todo_extra_de_src_es_resoluble_estaticamente():
    unresolved = [f"{s.where}: {'; '.join(s.problems)}" for s in scan_src() if s.problems]
    assert not unresolved, (
        "extra= no resoluble estaticamente; " + _HINT + ":\n" + "\n".join(unresolved)
    )


def test_la_guarda_no_es_vacia():
    sites = scan_src()
    by_file: dict[str, list[Site]] = {}
    for s in sites:
        by_file.setdefault(Path(s.file).name, []).append(s)
    expected = ("run_night.py", "generate_measurement_findings.py", "read_item.py", "transport.py")
    for name in expected:
        assert by_file.get(name), f"la guarda no encuentra llamadas de logging con extra= en {name}"
    assert len(sites) >= 20
    run_night_keys = set().union(*(s.keys for s in by_file["run_night.py"]))
    # extra=self._item_log_fields(...)
    assert {"item_id", "external_id", "phase", "outcome", "attempts"} <= run_night_keys
    # extra=log_fields, con log_fields["interest_score"] = ... y ["finding_id"] = ...
    assert {"interest_score", "finding_id"} <= run_night_keys
    measurement_keys = set().union(*(s.keys for s in by_file["generate_measurement_findings.py"]))
    assert {"event", "run_id", "primera_medida"} <= measurement_keys


def test_los_context_key_de_src_son_constantes_de_la_excepcion():
    allowed = _KEY_VARIABLE_EXCEPTIONS[("infrastructure/arxiv/transport.py", "context_key")]
    found: list[str] = []
    for path in sorted(SRC.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                for kw in node.keywords:
                    if kw.arg == "context_key":
                        v = kw.value
                        assert isinstance(v, ast.Constant | ast.IfExp), (
                            f"{path.name}:{node.lineno}: context_key no constante"
                        )
                        branches = [v.body, v.orelse] if isinstance(v, ast.IfExp) else [v]
                        for b in branches:
                            assert isinstance(b, ast.Constant) and b.value in allowed, (
                                f"{path.name}:{node.lineno}: context_key fuera de {sorted(allowed)}"
                            )
                            found.append(b.value)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                args = node.args
                pos = [*args.posonlyargs, *args.args]
                defaults = [None] * (len(pos) - len(args.defaults)) + list(args.defaults)
                pairs = list(zip(pos, defaults, strict=True)) + list(
                    zip(args.kwonlyargs, args.kw_defaults, strict=True)
                )
                for a, d in pairs:
                    if a.arg == "context_key":
                        assert isinstance(d, ast.Constant) and d.value in allowed, (
                            f"{path.name}:{node.lineno}: default de context_key fuera de la lista"
                        )
    assert found, "no se encontro ningun context_key= en src/"


# --- autotests de la guarda sobre fuentes sinteticas ------------------------------


def _scan(body: str) -> list[Site]:
    return scan_source("import logging\nlog = logging.getLogger()\n" + body)


def test_detecta_created_en_un_dict_literal():
    (site,) = _scan('def f():\n    log.info("x", extra={"created": 1})\n')
    assert site.keys == {"created"} and not site.problems
    assert site.keys & _RESERVED_LOG_RECORD_ATTRS


def test_detecta_name_en_un_dict_literal():
    (site,) = _scan('def f():\n    log.warning("x", extra={"name": "n", "ok": 1})\n')
    assert site.keys & _RESERVED_LOG_RECORD_ATTRS == {"name"}


def test_un_extra_limpio_no_choca():
    (site,) = _scan('def f():\n    log.error("x", extra={"event": "e", "findings_created": 1})\n')
    assert not (site.keys & _RESERVED_LOG_RECORD_ATTRS) and not site.problems


def test_spread_en_extra_no_es_resoluble():
    (site,) = _scan('def f(x):\n    log.info("x", extra={**x})\n')
    assert site.problems


def test_extra_como_parametro_no_es_resoluble():
    (site,) = _scan('def f(param):\n    log.info("x", extra=param)\n')
    assert site.problems and "parametro" in site.problems[0]


def test_clave_no_constante_no_es_resoluble():
    (site,) = _scan('def f(k):\n    log.info("x", extra={k: 1})\n')
    assert site.problems


def test_resuelve_nombre_asignado_a_dict_y_subscript_constante_y_lo_detecta():
    src = 'def f():\n    d = {"event": "e"}\n    d["msg"] = 1\n    log.info("x", extra=d)\n'
    (site,) = _scan(src)
    assert site.keys == {"event", "msg"} and not site.problems
    assert site.keys & _RESERVED_LOG_RECORD_ATTRS == {"msg"}


def test_subscript_con_clave_no_constante_no_es_resoluble():
    src = 'def f(k):\n    d = {"a": 1}\n    d[k] = 2\n    log.info("x", extra=d)\n'
    (site,) = _scan(src)
    assert site.problems


def test_resuelve_llamada_a_metodo_del_mismo_modulo():
    src = (
        "class C:\n"
        "    def fields(self):\n"
        '        return {"event": "e", "created": 1}\n'
        "    def run(self):\n"
        '        log.info("x", extra=self.fields())\n'
    )
    (site,) = _scan(src)
    assert site.keys == {"event", "created"} and not site.problems


def test_llamada_a_funcion_externa_no_es_resoluble():
    (site,) = _scan('def f():\n    log.info("x", extra=build_extra())\n')
    assert site.problems


def test_otros_metodos_y_kwargs_no_log_se_ignoran():
    assert _scan('def f():\n    cfg.get("x", extra={"created": 1})\n    g(extra=1)\n') == []


def test_log_con_nivel_posicional_tambien_se_inspecciona():
    (site,) = _scan('def f():\n    log.log(20, "x", extra={"process": 1})\n')
    assert site.keys == {"process"}
