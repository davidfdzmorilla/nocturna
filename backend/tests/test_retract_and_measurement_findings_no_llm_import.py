"""T100: `retract_finding` y `generate_measurement_findings` no tocan el LLM.

Dos comprobaciones: en un subproceso limpio, importarlos no mete
`claude_agent_sdk`, `nocturna.infrastructure` ni `httpx` en `sys.modules`; y
por AST, ninguno importa (ni nombra) `LLMProvider`, `AgentRunner` ni `BudgetGuard`.
"""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "nocturna" / "application" / "use_cases"
MODULES = ["retract_finding", "generate_measurement_findings"]
FORBIDDEN_NAMES = {"LLMProvider", "AgentRunner", "BudgetGuard", "run_agent", "authorize"}


@pytest.mark.parametrize("module", MODULES)
def test_importar_el_modulo_no_mete_sdk_infrastructure_ni_httpx(module: str) -> None:
    script = textwrap.dedent(
        f"""
        import sys
        import nocturna.application.use_cases.{module}  # noqa: F401
        forbidden = ("claude_agent_sdk", "nocturna.infrastructure", "httpx")
        leaked = sorted(
            n for n in sys.modules
            if any(n == p or n.startswith(p + ".") for p in forbidden)
        )
        if leaked:
            print(f"modulos prohibidos: {{leaked}}", file=sys.stderr)
            sys.exit(1)
        print("OK")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


@pytest.mark.parametrize("module", MODULES)
def test_el_modulo_no_nombra_llmprovider_agentrunner_ni_budgetguard(module: str) -> None:
    tree = ast.parse((SRC / f"{module}.py").read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom | ast.Import):
            names.update(a.name.split(".")[-1] for a in node.names)
            if isinstance(node, ast.ImportFrom) and node.module:
                names.update(node.module.split("."))
        elif isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    assert not (names & FORBIDDEN_NAMES), names & FORBIDDEN_NAMES
    assert "llm" not in names and "budget" not in names
