"""T85 no gasta tokens: importar el dominio y el caso de uso del cálculo de tensiones
entre soluciones no mete `claude_agent_sdk`, `nocturna.infrastructure` ni `httpx`
en `sys.modules`, y el caso de uso no referencia proveedor, guardia de presupuesto
ni fabrica de agentes (subproceso limpio, como `test_tension_no_claude_import.py`)."""

import ast
import subprocess
import sys
import textwrap
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src" / "nocturna"
CASO_DE_USO = SRC / "application" / "use_cases" / "compute_solution_tensions.py"
DOMINIO = SRC / "domain" / "solution_tension.py"

_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys

    import nocturna.domain.solution_tension  # noqa: F401
    import nocturna.application.use_cases.compute_solution_tensions  # noqa: F401

    forbidden = ("claude_agent_sdk", "nocturna.infrastructure", "httpx")
    leaked = sorted(
        name
        for name in sys.modules
        if any(name == p or name.startswith(p + ".") for p in forbidden)
    )
    if leaked:
        print(f"modulos prohibidos en sys.modules: {leaked}", file=sys.stderr)
        sys.exit(1)
    print("OK")
    """
)


def test_importar_el_calculo_de_tensiones_entre_soluciones_no_mete_sdk_ni_infraestructura():
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT], capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def _imported_and_called_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.add(node.module or "")
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, ast.Name):
            names.add(node.id)
    return names


def test_el_caso_de_uso_no_referencia_proveedor_presupuesto_ni_agentes():
    prohibidos = {
        "LLMProvider",
        "BudgetGuard",
        "AgentWorkFactory",
        "AgentRunner",
        "run_agent",
        "claude_agent_sdk",
    }
    for path in (CASO_DE_USO, DOMINIO):
        assert not (_imported_and_called_names(path) & prohibidos), path.name
