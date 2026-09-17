"""Reexporta la fixture de base de datos de `tests/db/conftest.py`.

Las fixtures de un `conftest.py` solo llegan a los subdirectorios de donde
vive: `tests/manual/` es hermano de `tests/db/`, no descendiente, así que no
hereda `db_session_factory` (ni `test_engine`/`test_database_url`, de las
que depende) por el mero hecho de existir en el mismo árbol `tests/`.
`test_sdk_smoke.py` necesita esa fixture (commit real + `TRUNCATE` en el
teardown, la misma que usan los tests de atomicidad de `BudgetGuard` en
`tests/db/test_budget_guard_db.py`) para crear el `Run` y comprobar el
acumulado de `AgentCall` con una sesión nueva, como si fuera un reinicio.

Se carga `tests/db/conftest.py` con `importlib` bajo un nombre de módulo
propio, no con `import conftest` a secas: pytest ya importa ese fichero bajo
el nombre `conftest` en `sys.modules` al recolectar `tests/db/`, y un
`import conftest` plano en este fichero heredaría lo que hubiera en esa
entrada según el orden de recolección (colisión con el `conftest` de la
raíz de `tests/`, que se registra con el mismo nombre), no necesariamente
`tests/db/conftest.py`. Un nombre de módulo único evita esa ambigüedad.

Solo se reexportan los tres nombres que `test_sdk_smoke.py` necesita
(`db_session_factory` y las dos fixtures de las que depende por cadena de
`session_scope`): pytest descubre una fixture por su nombre en el espacio
de nombres del módulo `conftest.py`, sin que importe dónde se definió
originalmente la función.
"""

import importlib.util
from pathlib import Path

_DB_CONFTEST_PATH = Path(__file__).resolve().parents[1] / "db" / "conftest.py"
_spec = importlib.util.spec_from_file_location("nocturna_tests_db_conftest", _DB_CONFTEST_PATH)
assert _spec is not None and _spec.loader is not None
_db_conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_db_conftest)

test_database_url = _db_conftest.test_database_url
test_engine = _db_conftest.test_engine
db_session_factory = _db_conftest.db_session_factory
