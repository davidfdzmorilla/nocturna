"""Congela, en un subproceso limpio, que `domain.exoplanet_filter` y
`scripts/backfill_exoplanet_match.py` no meten `claude_agent_sdk` en
`sys.modules` (T79). Subproceso: `sys.modules` de pytest puede estar contaminado."""

import subprocess
import sys
import textwrap
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]

_SCRIPT = textwrap.dedent(
    """
    import importlib.util
    import sys

    import nocturna.domain.exoplanet_filter  # noqa: F401

    assert "claude_agent_sdk" not in sys.modules, "domain.exoplanet_filter importo el SDK"

    spec = importlib.util.spec_from_file_location("backfill", sys.argv[1])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert "claude_agent_sdk" not in sys.modules, "el script de relleno importo el SDK"
    print("OK")
    """
)


def test_ni_el_dominio_del_filtro_ni_el_script_de_relleno_importan_el_sdk():
    script = BACKEND_ROOT / "scripts" / "backfill_exoplanet_match.py"

    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(script)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout


def test_el_modulo_del_filtro_solo_importa_la_biblioteca_estandar_y_el_dominio():
    import ast

    source = (BACKEND_ROOT / "src/nocturna/domain/exoplanet_filter.py").read_text()
    imported = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert imported <= {"re", "dataclasses", "nocturna", "__future__"}, imported
