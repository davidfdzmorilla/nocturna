"""Congela, en un subproceso limpio, que importar los modulos de T73
(`domain.tension`, `domain.catalog`, `application.use_cases.compute_tensions`)
no mete `claude_agent_sdk`, `nocturna.infrastructure` ni `httpx` en
`sys.modules`. Subproceso por el mismo motivo que
`test_api_no_claude_import.py`: `sys.modules` de la sesion de pytest ya
puede estar contaminado.
"""

import subprocess
import sys
import textwrap

_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys

    import nocturna.domain.catalog  # noqa: F401
    import nocturna.domain.tension  # noqa: F401
    import nocturna.application.use_cases.compute_tensions  # noqa: F401

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


def test_importar_modulos_de_tension_no_mete_sdk_infrastructure_ni_httpx():
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"subproceso fallo (code={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout
