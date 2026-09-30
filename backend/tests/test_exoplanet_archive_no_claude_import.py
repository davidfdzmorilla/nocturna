"""Congela, en un subproceso limpio, que el adaptador del Exoplanet Archive
no importa `claude_agent_sdk` ni `nocturna.application` (T74, ADR 0012):
`infrastructure/` depende de `domain/`, nunca de la capa de aplicación, y una
fuente consumida solo por código Python no necesita el SDK.
"""

import subprocess
import sys
import textwrap

_SCRIPT = textwrap.dedent(
    """
    import sys

    import nocturna.infrastructure.exoplanet_archive.catalog  # noqa: F401
    import nocturna.infrastructure.exoplanet_archive.client  # noqa: F401
    import nocturna.infrastructure.exoplanet_archive.mappers  # noqa: F401
    import nocturna.infrastructure.exoplanet_archive.names  # noqa: F401

    forbidden = ("claude_agent_sdk", "nocturna.application")
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


def test_el_adaptador_del_exoplanet_archive_no_importa_el_sdk_ni_application():
    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT], capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout
