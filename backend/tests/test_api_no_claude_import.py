"""Congela, en un subproceso interprete-limpio, que `import nocturna.api.app`
no mete `claude_agent_sdk` ni `nocturna.infrastructure.llm` en
`sys.modules`.

Precedente exacto: `tests/db/test_cli_dry_run_db.py::
test_claude_agent_sdk_no_se_importa_durante_dry_run`, que comprueba lo mismo
para `run-night --dry-run`. Corre en un subproceso a propósito, no en el
proceso de la sesión de pytest: si cualquier otro test de la suite ya
importó `claude_agent_sdk` antes de este (por ejemplo, uno que ejercite
`AgentSDKProvider` o la propia guarda `_no_claude` de `conftest.py`),
`sys.modules` de *este* proceso ya lo tendría, y comprobarlo aquí pasaría
sin probar nada. Un subproceso arranca desde `sys.modules` vacío de verdad.

Esto congela por test la restricción de `CLAUDE.md` (§ Web fase 1: "Cero
llamadas a Claude, cero lógica de análisis") a nivel de import, no solo de
comportamiento en tiempo de ejecución: ni siquiera *cargar* el módulo de la
API debe arrastrar el SDK de Claude ni el proveedor LLM a memoria. A
diferencia de `test_api_read_only.py` (AST, sin ejecutar nada), este test
importa el módulo de verdad -- por eso corre en subproceso, y por eso no
llama a `create_app()` ni abre ninguna conexión: solo el `import` en sí ya
es lo que se quiere comprobar, sin necesidad de base de datos.
"""

import subprocess
import sys
import textwrap

_FORBIDDEN_MODULE_PREFIXES = ("claude_agent_sdk", "nocturna.infrastructure.llm")

_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys

    import nocturna.api.app  # noqa: F401 -- solo importar, no llamar a create_app()

    forbidden_prefixes = ("claude_agent_sdk", "nocturna.infrastructure.llm")
    leaked = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    )
    if leaked:
        print(f"módulos prohibidos en sys.modules tras importar nocturna.api.app: {leaked}",
              file=sys.stderr)
        sys.exit(1)
    print("OK")
    """
)


def test_importar_nocturna_api_app_no_mete_claude_agent_sdk_ni_infrastructure_llm_en_sys_modules():
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"subproceso falló (code={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}\n"
        f"módulos prohibidos: {_FORBIDDEN_MODULE_PREFIXES}"
    )
    assert "OK" in result.stdout
    assert "Traceback" not in result.stderr
