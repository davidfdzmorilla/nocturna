"""Ayudantes puros de los tests de despliegue (T107, ADR 0044).

Sin red, sin Docker y sin Claude: leen texto y el `uv.lock`.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Iterable, Mapping

_FORBIDDEN = re.compile(r"claude|anthropic", re.IGNORECASE)
_FORBIDDEN_ENV_KEY = re.compile(r"^(ANTHROPIC|CLAUDE)", re.IGNORECASE)


def forbidden_terms(text: str) -> list[str]:
    """Una entrada `linea N: <linea>` por cada linea con `claude` o `anthropic`."""
    return [
        f"linea {n}: {line.strip()}"
        for n, line in enumerate(text.splitlines(), start=1)
        if _FORBIDDEN.search(line)
    ]


def lock_closure(lock: str, root: str, groups: Iterable[str]) -> set[str]:
    """Cierre transitivo de dependencias de `root` en un `uv.lock`.

    Incluye `[package].dependencies`, los grupos pedidos de `dev-dependencies` y las
    dependencias de los extras que el lock enumera (p. ej. `psycopg[binary]`).
    """
    packages = {p["name"]: p for p in tomllib.loads(lock).get("package", [])}
    start = packages[root]
    pending: list[tuple[str, tuple[str, ...]]] = []

    def add(deps: Iterable[Mapping]) -> None:
        for d in deps:
            pending.append((d["name"], tuple(d.get("extra", ()))))

    add(start.get("dependencies", []))
    for g in groups:
        add(start.get("dev-dependencies", {}).get(g, []))

    seen: set[str] = set()
    while pending:
        name, extras = pending.pop()
        pkg = packages.get(name)
        if pkg is None:
            continue
        if name not in seen:
            seen.add(name)
            add(pkg.get("dependencies", []))
        for extra in extras:
            add(pkg.get("optional-dependencies", {}).get(extra, []))
    return seen


def env_violations(env: Mapping[str, str] | Iterable[str]) -> list[str]:
    """Variables de entorno prohibidas: nombre `ANTHROPIC*`/`CLAUDE*` o valor que los nombre.

    Acepta un dict (compose) o una lista `K=V` (`docker image inspect`).
    """
    if isinstance(env, Mapping):
        pairs = [(str(k), "" if v is None else str(v)) for k, v in env.items()]
    else:
        pairs = [tuple(item.split("=", 1)) if "=" in item else (item, "") for item in env]
    return [f"{k}={v}" for k, v in pairs if _FORBIDDEN_ENV_KEY.search(k) or _FORBIDDEN.search(v)]
