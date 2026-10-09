"""Importar los módulos del resumen semanal (T84) no carga `claude_agent_sdk`.

Mismo patrón que `test_api_no_claude_import.py`: subproceso con `sys.modules`
limpio. El resumen no usa LLM ni red; ni siquiera cargar su código debe
arrastrar el SDK ni `nocturna.infrastructure.llm`.
"""

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent(
    """
    import sys

    import nocturna.domain.archive_digest  # noqa: F401
    import nocturna.application.use_cases.archive_digest  # noqa: F401
    import nocturna.application.archive_digest_text  # noqa: F401
    import nocturna.api.routes.archive  # noqa: F401

    prefixes = ("claude_agent_sdk", "nocturna.infrastructure.llm")
    leaked = sorted(
        n for n in sys.modules if any(n == p or n.startswith(p + ".") for p in prefixes)
    )
    if leaked:
        print(f"módulos prohibidos tras importar el resumen semanal: {leaked}", file=sys.stderr)
        sys.exit(1)
    print("OK")
    """
)


def test_importar_los_modulos_del_resumen_semanal_no_mete_claude_en_sys_modules():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT], capture_output=True, text=True, timeout=60
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout
