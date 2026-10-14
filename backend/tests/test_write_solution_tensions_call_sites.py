"""T85.e: `write_solution_tensions.py` solo llega a Claude vía `AgentRunner`."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "nocturna" / "application" / "use_cases"
MODULE = SRC / "write_solution_tensions.py"


def _tree() -> ast.Module:
    return ast.parse(MODULE.read_text(encoding="utf-8"))


def test_no_llama_a_run_agent_ni_importa_el_sdk_ni_el_proveedor_concreto() -> None:
    tree = _tree()
    calls = [
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Attribute)
        and n.func.attr in {"run_agent", "authorize", "query"}
    ]
    assert calls == []
    imported = [
        (n.module or "") if isinstance(n, ast.ImportFrom) else a.name
        for n in ast.walk(tree)
        if isinstance(n, (ast.Import, ast.ImportFrom))
        for a in (n.names if isinstance(n, ast.Import) else [n])
    ]
    assert not any(m.startswith(("claude_agent_sdk", "nocturna.infrastructure")) for m in imported)


def test_la_redaccion_pasa_por_agent_runner_con_rol_writer() -> None:
    source = MODULE.read_text(encoding="utf-8")
    assert "AgentRunner(" in source and "role=AgentRole.WRITER" in source
    assert "self._runner.run(" in source


def test_pending_no_recibe_proveedor_ni_guard() -> None:
    import inspect

    from nocturna.application.use_cases.write_solution_tensions import SelectSolutionTensions

    params = set(inspect.signature(SelectSolutionTensions.__init__).parameters)
    assert not params & {"provider", "guard", "budget_guard", "work", "runner"}


@pytest.mark.parametrize("name", ["domain/solution_tension.py"])
def test_el_dominio_sigue_puro(name: str) -> None:
    text = (SRC.parents[1] / name).read_text(encoding="utf-8")
    assert "claude_agent_sdk" not in text and "sqlalchemy" not in text
