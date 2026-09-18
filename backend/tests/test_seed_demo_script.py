"""Congela las dos propiedades no negociables de `scripts/seed_demo.py` (T50,
paso 4): que sigue fuera de todo lo que se empaqueta/instala, y que no toca
nada sin autorización explícita.

**Ningún test siembra datos con este script.** La suite entera sigue usando
`tests/db/factories.py`; esto solo vigila el script en sí, nunca lo ejecuta
de verdad contra una base de datos (`test_aborta_sin_la_variable_de_entorno`
comprueba justo lo contrario: que sin la variable, ni lo intenta).

- `test_project_scripts_solo_expone_nocturna`: si algún día alguien añade
  `seed-demo = "..."` (o cualquier otra entrada) a `[project.scripts]` de
  `backend/pyproject.toml`, este test se pone en rojo. Esa tabla es lo que
  `uv sync` instala como comando ejecutable del paquete; `seed_demo.py` no
  debe aparecer nunca ahí (ver el docstring del propio script).
- `test_seed_demo_no_esta_en_los_paquetes_de_la_wheel`: `backend/scripts/`
  no es un subdirectorio de `src/nocturna/`, así que
  `[tool.hatch.build.targets.wheel] packages = ["src/nocturna"]` ya lo deja
  fuera de la wheel por construcción; esto solo lo hace explícito y lo
  vigila si alguien ensancha esa lista de paquetes.
- `test_aborta_sin_la_variable_de_entorno`: lanza el script de verdad como
  subproceso (con el mismo intérprete que corre pytest, `sys.executable`,
  así que ve el mismo entorno con `nocturna` instalado) pero sin
  `NOCTURNA_ALLOW_SEED` en el entorno. `main()` comprueba esa variable
  (`_require_opt_in`) antes de construir `Settings`/el motor de base de
  datos, así que este test no necesita PostgreSQL levantado ni red: si el
  guarda se rompiera y el script llegara a intentar conectar, este test
  fallaría por timeout/error de conexión en vez de por el código de salida
  esperado, que ya sería una señal de alarma suficiente.
"""

from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT_PATH = BACKEND_ROOT / "pyproject.toml"
SEED_SCRIPT_PATH = BACKEND_ROOT / "scripts" / "seed_demo.py"


def _load_pyproject() -> dict[str, object]:
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def test_project_scripts_solo_expone_nocturna():
    pyproject = _load_pyproject()
    scripts = pyproject["project"]["scripts"]

    assert scripts == {"nocturna": "nocturna.cli:main"}, (
        "[project.scripts] de backend/pyproject.toml debe seguir teniendo una única "
        f"entrada ('nocturna'); se encontró {scripts!r}. scripts/seed_demo.py no se "
        "registra aquí a propósito (ver su docstring): es una herramienta de mano "
        "del autor, no un comando instalado del paquete."
    )


def test_seed_demo_no_esta_en_los_paquetes_de_la_wheel():
    pyproject = _load_pyproject()
    wheel_packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert wheel_packages == ["src/nocturna"], (
        "[tool.hatch.build.targets.wheel].packages debe seguir limitado a "
        f"['src/nocturna']; se encontró {wheel_packages!r}. backend/scripts/ vive "
        "fuera de esa carpeta a propósito, para que nunca entre en la wheel."
    )
    assert SEED_SCRIPT_PATH.exists(), (
        f"no se encontró {SEED_SCRIPT_PATH}: revisa la ruta antes de fiarte de este test"
    )
    assert not any(
        SEED_SCRIPT_PATH.is_relative_to(BACKEND_ROOT / package) for package in wheel_packages
    ), "scripts/seed_demo.py no debería quedar dentro de ningún paquete empaquetado en la wheel"


def test_aborta_sin_la_variable_de_entorno():
    env = {"PATH": os.environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, str(SEED_SCRIPT_PATH)],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 1, (
        "seed_demo.py debe salir con código 1 si NOCTURNA_ALLOW_SEED no está en el "
        f"entorno, no intentar conectar a ninguna base de datos. stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert "NOCTURNA_ALLOW_SEED" in result.stderr
    assert "NOCTURNA_ALLOW_SEED=1 uv run python scripts/seed_demo.py" in result.stderr
