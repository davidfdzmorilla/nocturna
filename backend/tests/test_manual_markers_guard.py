"""Congela la regresión exacta del bloqueante de la primera revisión de T40:
`tests/manual/test_sdk_smoke.py` llevaba `pytestmark = [pytest.mark.manual,
pytest.mark.db]`, y `uv run pytest -m db` -- un comando documentado para
lanzar los tests de base de datos -- recolectaba y ejecutaba ese test de
verdad, cobrando contra la suscripción del autor. La razón: `-m` en la línea
de comandos de pytest SUSTITUYE por completo el `-m 'not manual'` de
`addopts` (`pyproject.toml`), no se compone con él (comportamiento de
`argparse`, no un bug de este proyecto) -- así que un segundo marcador
filtrable en un fichero de `tests/manual/` abre un camino de selección que
no pasa por la exclusión de `manual`.

Hoy lo único que lo impide es un párrafo de docstring en
`tests/manual/test_sdk_smoke.py`. Este fichero lo convierte en una
comprobación ejecutable: recorre `tests/manual/test_*.py` con `ast` (sin
importar ni ejecutar nada -- cero riesgo de alcanzar el CLI) y afirma que
todos sus marcadores (`pytestmark` a nivel de módulo y `@pytest.mark.X` a
nivel de función) son un subconjunto de los permitidos.

**Marcadores permitidos, verificados contra el uso real del fichero
existente (no hipotéticos):**

- `manual`: el propio marcador que excluye estos tests de la suite normal
  (`addopts = "-m 'not manual'"`). Obligatorio, es lo que este fichero
  protege.
- `anyio`: infraestructura async del propio test (`@pytest.mark.anyio` en
  la función `test_smoke_...`, necesario para que el plugin de `anyio`
  ejecute una función `async def`). No es un marcador de dominio que alguien
  vaya a usar para seleccionar tests por línea de comandos a propósito, pero
  si algún día lo es (`uv run pytest -m anyio` seleccionaría TODOS los tests
  `anyio` de la suite, incluido este), ese es exactamente el mismo patrón de
  riesgo que este test existe para vigilar -- no se excluye a la ligera, se
  documenta aquí como el único adicional a `manual` que el fichero usa hoy.

Si se añade un marcador nuevo a un fichero de `tests/manual/` (por ejemplo
`db`, la regresión original), este test se pone en rojo. NO lo arregles
añadiendo el marcador a `_ALLOWED_MANUAL_MARKERS` sin pensarlo: primero
comprueba si ese marcador tiene, o podría tener, un filtro `-m
<marcador>` documentado o previsible que seleccionaría este fichero sin
pasar por `-m 'not manual'`. Si la respuesta es sí, el mecanismo correcto no
es esta lista, es hacer manual el único camino de verdad (variable de
entorno `NOCTURNA_ALLOW_REAL_CLAUDE=1` o flag `--run-manual` propio -- ver
`OPEN_DECISIONS.md`), que queda fuera del alcance de este fichero.
"""

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parent.parent
MANUAL_DIR = BACKEND_ROOT / "tests" / "manual"

#: Ver docstring del módulo: el único marcador adicional a `manual` que usa
#: de verdad `tests/manual/test_sdk_smoke.py` hoy es `anyio` (infraestructura
#: async, no un filtro de dominio pensado para seleccionar este fichero).
_ALLOWED_MANUAL_MARKERS = frozenset({"manual", "anyio"})


def _pytest_mark_name(node: ast.expr) -> str | None:
    """Extrae el nombre de un `pytest.mark.<nombre>` (con o sin llamada).

    Cubre `pytest.mark.manual` (`ast.Attribute`) y `pytest.mark.manual(...)`
    (`ast.Call` cuyo `func` es ese mismo `Attribute`). Cualquier otra forma
    (marcador dinámico, `getattr(pytest.mark, ...)`) no se reconoce y no
    cuenta -- no hay ningún caso así hoy en `tests/manual/`.
    """
    target = node.func if isinstance(node, ast.Call) else node
    if (
        isinstance(target, ast.Attribute)
        and isinstance(target.value, ast.Attribute)
        and target.value.attr == "mark"
        and isinstance(target.value.value, ast.Name)
        and target.value.value.id == "pytest"
    ):
        return target.attr
    return None


def _markers_in_file(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    markers: set[str] = set()

    for node in ast.walk(tree):
        # `pytestmark = pytest.mark.manual` o `pytestmark = [pytest.mark.manual, ...]`
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "pytestmark" for target in node.targets
        ):
            elements = (
                node.value.elts if isinstance(node.value, ast.List | ast.Tuple) else [node.value]
            )
            for element in elements:
                name = _pytest_mark_name(element)
                if name is not None:
                    markers.add(name)

        # `@pytest.mark.anyio` sobre una función (sync o async).
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                name = _pytest_mark_name(decorator)
                if name is not None:
                    markers.add(name)

    return markers


def _manual_test_files() -> list[Path]:
    return sorted(MANUAL_DIR.glob("test_*.py"))


def test_tests_manual_no_lleva_marcadores_fuera_de_los_permitidos():
    manual_files = _manual_test_files()

    assert manual_files, (
        f"no se encontró ningún fichero test_*.py en {MANUAL_DIR}: revisa la ruta, "
        "este test debería tener al menos tests/manual/test_sdk_smoke.py que vigilar"
    )

    violations: dict[str, list[str]] = {}
    for path in manual_files:
        extra = sorted(_markers_in_file(path) - _ALLOWED_MANUAL_MARKERS)
        if extra:
            violations[str(path)] = extra

    assert not violations, (
        "marcador(es) no permitido(s) en tests/manual/ fuera de "
        f"{sorted(_ALLOWED_MANUAL_MARKERS)}: {violations}. Esta es la regresión "
        "exacta del bloqueante de T40: combinar 'manual' con cualquier otro "
        "marcador filtrable reabre un camino de gasto real, porque 'uv run "
        "pytest -m <marcador>' SUSTITUYE -no compone- el 'not manual' de "
        "addopts (pyproject.toml). Antes de añadir ese marcador a la lista de "
        "permitidos, comprueba si alguien podría lanzarlo alguna vez con "
        "'-m <ese-marcador>' sin querer ejecutar este fichero de verdad."
    )
