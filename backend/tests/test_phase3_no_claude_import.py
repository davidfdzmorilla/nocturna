"""T110.a: el instrumento de cierre de la fase 3 no toca el LLM ni el control de gasto.

Recorre con `ast` los modulos `phase3*` (y `public_site.py`): ningun import, ni a nivel de
modulo ni dentro de una funcion, de `llm`, `claude_agent_sdk` o `budget`. Nombres en espanol.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "nocturna"
MODULES = [
    SRC / "domain" / "phase3.py",
    SRC / "application" / "use_cases" / "phase3.py",
    SRC / "infrastructure" / "ops" / "phase3_log.py",
    SRC / "infrastructure" / "ops" / "public_site.py",
]
FORBIDDEN = ("claude_agent_sdk", "anthropic", "budget", "llm")


def _imported(tree: ast.AST) -> list[str]:
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names.append(module)
            names += [f"{module}.{alias.name}" for alias in node.names]
    return names


@pytest.mark.parametrize("path", MODULES, ids=lambda p: p.name)
def test_el_modulo_existe_y_no_importa_llm_sdk_ni_budget(path):
    assert path.is_file()
    imports = _imported(ast.parse(path.read_text()))

    for name in imports:
        parts = name.split(".")
        assert "llm" not in parts, f"{path.name} importa {name}"
        assert not any(p in FORBIDDEN for p in parts), f"{path.name} importa {name}"


def test_el_dominio_de_phase3_no_importa_sqlalchemy_ni_httpx():
    imports = _imported(ast.parse(MODULES[0].read_text()))

    assert not [
        n
        for n in imports
        if n.split(".")[0] in {"sqlalchemy", "httpx", "nocturna"}
        and not n.startswith("nocturna.domain")
    ]


def test_el_caso_de_uso_de_phase3_no_importa_infraestructura():
    imports = _imported(ast.parse(MODULES[1].read_text()))

    assert not [n for n in imports if n.startswith(("nocturna.infrastructure", "httpx"))]
