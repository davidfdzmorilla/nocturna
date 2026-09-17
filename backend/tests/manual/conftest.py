"""Fixtures de `tests/manual/`: reexport de base de datos + guarda de gasto real.

## Reexporta la fixture de base de datos de `tests/db/conftest.py`

Las fixtures de un `conftest.py` solo llegan a los subdirectorios de donde
vive: `tests/manual/` es hermano de `tests/db/`, no descendiente, así que no
hereda `db_session_factory` (ni `test_engine`/`test_database_url`, de las
que depende) por el mero hecho de existir en el mismo árbol `tests/`.
`test_sdk_smoke.py` (T40) y `test_read_item_smoke.py` (T41) necesitan esa
fixture (commit real + `TRUNCATE` en el teardown, la misma que usan los
tests de atomicidad de `BudgetGuard` en `tests/db/test_budget_guard_db.py`)
para crear el `Run` y comprobar el acumulado de `AgentCall` con una sesión
nueva, como si fuera un reinicio.

Se carga `tests/db/conftest.py` con `importlib` bajo un nombre de módulo
propio, no con `import conftest` a secas: pytest ya importa ese fichero bajo
el nombre `conftest` en `sys.modules` al recolectar `tests/db/`, y un
`import conftest` plano en este fichero heredaría lo que hubiera en esa
entrada según el orden de recolección (colisión con el `conftest` de la
raíz de `tests/`, que se registra con el mismo nombre), no necesariamente
`tests/db/conftest.py`. Un nombre de módulo único evita esa ambigüedad.

Solo se reexportan los tres nombres que estos tests necesitan
(`db_session_factory` y las dos fixtures de las que depende por cadena de
`session_scope`): pytest descubre una fixture por su nombre en el espacio
de nombres del módulo `conftest.py`, sin que importe dónde se definió
originalmente la función.

## Guarda de gasto real: `NOCTURNA_ALLOW_REAL_CLAUDE`

`docs/OPEN_DECISIONS.md` ("Mecanismo fuerte para tests manuales",
"Ubicación de ejecución de pytest determina qué tests se recolectan"):
`addopts = "-m 'not manual'"` vive en `backend/pyproject.toml`. Lanzar
pytest desde la raíz del repositorio resuelve el `pyproject.toml` de la
raíz (sin ese `addopts`) y **recolecta este directorio igual**; hasta ahora
la única barrera real era acordarse de arrancar pytest desde `backend/`, y
eso no es una barrera. Con T41 aparece el segundo test manual
(`test_read_item_smoke.py`), el momento en que el plan pedía cerrar el
agujero.

`_require_real_claude_opt_in`, más abajo, es una fixture **autouse** sobre
todo `tests/manual/`: sin `NOCTURNA_ALLOW_REAL_CLAUDE` en el entorno,
cualquier test de este directorio se salta, sin importar desde dónde se
lance pytest ni qué `-m` se use en la línea de comandos -- ni el marcador
`manual` ni la ubicación del `pyproject.toml` activo importan ya.

Comando para lanzar los tests manuales de verdad (gasta la suscripción
Claude Max del autor):

    env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s
"""

import importlib.util
import os
from pathlib import Path

import pytest

_DB_CONFTEST_PATH = Path(__file__).resolve().parents[1] / "db" / "conftest.py"
_spec = importlib.util.spec_from_file_location("nocturna_tests_db_conftest", _DB_CONFTEST_PATH)
assert _spec is not None and _spec.loader is not None
_db_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db_conftest)

test_database_url = _db_conftest.test_database_url
test_engine = _db_conftest.test_engine
db_session_factory = _db_conftest.db_session_factory

_ALLOW_REAL_CLAUDE_ENV_VAR = "NOCTURNA_ALLOW_REAL_CLAUDE"

_RUN_MANUAL_COMMAND = (
    "env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s"
)


@pytest.fixture(autouse=True)
def _require_real_claude_opt_in() -> None:
    """Segunda barrera para todo `tests/manual/`, independiente de `-m` y del `cwd`.

    `pytest.skip`, deliberadamente, no `pytest.fail`: lo que esta guarda
    defiende es que la suscripción no se gaste por accidente, no que la
    variable esté siempre fijada. Sin ella, lanzar la suite desde la raíz
    del repositorio (o cualquier sitio donde `addopts = "-m 'not manual'"`
    de `backend/pyproject.toml` no esté en vigor) es el caso normal para
    quien no es el autor lanzando esto a mano, no una anomalía; un `fail`
    dejaría esa situación habitual en rojo permanente, lo que entrena a
    ignorar el rojo -- justo lo contrario de lo que busca esta guarda.
    `skip` dice "esto no corrió", que es la verdad, sin fingir un fallo.
    """
    if _ALLOW_REAL_CLAUDE_ENV_VAR not in os.environ:
        pytest.skip(
            f"test manual saltado: falta {_ALLOW_REAL_CLAUDE_ENV_VAR} en el entorno. "
            f"Para lanzarlo de verdad (gasta la suscripción Claude Max del autor): "
            f"{_RUN_MANUAL_COMMAND}"
        )
