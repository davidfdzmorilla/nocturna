"""Test AST sobre todos los ficheros de `src/nocturna/api/`, hermana de
`test_llm_call_sites.py`: en vez de vigilar quién puede tocar
`claude_agent_sdk`, aquí la regla es que la API de lectura (`CLAUDE.md`, §
Web fase 1: "Cero llamadas a Claude, cero lógica de análisis") se mantenga
de solo lectura, sin escritura y sin ningún camino hacia el proveedor LLM.

Tres comprobaciones, todas por AST sobre `src/nocturna/api/**/*.py`:

- **Ningún decorador de ruta distinto de `get`.** Un `@router.post(...)`,
  `@router.put(...)`, `@router.patch(...)`, `@router.delete(...)` o
  `@router.api_route(...)` añadido por inercia (copiar un patrón de otra
  capa, o "de paso" al implementar algo que en realidad necesitaba lectura)
  pone este test en rojo.
- **Ninguna llamada a `commit`, `add`, `add_many`, `save`, `flush` ni
  `unpublished_for_run`.** Los cinco primeros son los verbos de escritura de
  este proyecto (`Session.commit`/`.add`, `ItemRepository.add_many`,
  `FindingRepository`/`ItemRepository.save`, `Session.flush`).
  `unpublished_for_run` no escribe nada, pero es el único método de
  `FindingRepository` que devuelve hallazgos sin publicar (`domain/
  repositories.py`), así que cuenta igual. `api/deps.py` ya documenta por qué
  `get_session` cierra siempre con `rollback()`: este test es la
  comprobación ejecutable de que, además, nadie invoca directamente ninguno
  de esos verbos ni ese método desde la propia capa HTTP.
- **Ningún import de `claude_agent_sdk`, `nocturna.infrastructure.llm`,
  `nocturna.application.agents`, `nocturna.application.budget` ni
  `nocturna.application.unit_of_work`.** Los cuatro últimos son exactamente
  la maquinaria de gasto y de agentes del pipeline nocturno; si apareciera
  aquí, sería la señal de que alguien está construyendo, por la puerta de
  atrás, el "pregúntale a Claude" para visitantes que `CLAUDE.md` prohíbe
  explícitamente.

**Qué NO es este fichero (mismo contrapeso honesto que `test_llm_call_sites.py`).**
No es una barrera de seguridad: es un detector de descuidos por patrón
sintáctico, con los mismos huecos conocidos.

- Un alias evade la detección de verbos de escritura por completo: un
  endpoint que hiciera `mutar = session.commit` (un `ast.Attribute` fuera de
  cualquier `ast.Call`) y más tarde invocara `mutar()` nunca aparece en
  `_forbidden_call_sites`, porque el nodo de la llamada tiene
  `func = Name(id="mutar")`, no `Attribute(attr="commit")`. No hay ningún
  caso así hoy en `api/`, pero quien mantenga este paquete no debe leer un
  passed de este test como garantía de que ningún endpoint escribe -- solo
  lo es porque hoy nadie usa un alias.
- `getattr(session, "commit")()` tampoco se detecta: el nombre `"commit"` es
  una cadena, no un `ast.Attribute`, así que `_forbidden_call_sites` nunca
  lo ve.
- Un decorador dinámico (`getattr(router, "post")(...)`) tampoco cuenta como
  decorador de ruta prohibido para este test: `_route_decorator_names` solo
  reconoce `@router.post(...)`/`@app.post(...)` con el nombre del método
  escrito literalmente.
- Un import indirecto (`import nocturna.application` a secas, seguido de
  `nocturna.application.agents.algo`) no aparece como import de
  `nocturna.application.agents`: `_find_forbidden_imports` solo mira el
  nombre del propio nodo `Import`/`ImportFrom`, no accesos por atributo
  posteriores.

El valor de este test está en que un `POST` añadido sin pensarlo, o un
`session.add(...)` copiado de otra capa por costumbre, sea un fallo visible
en la suite -- no en que cierre con certeza cualquier forma de saltarse la
regla.
"""

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
API_DIR = BACKEND_ROOT / "src" / "nocturna" / "api"

#: Métodos HTTP de escritura del propio SDK de FastAPI/Starlette
#: (`APIRouter.post`/`.put`/`.patch`/`.delete`) más `api_route`, que puede
#: registrar cualquier método -- incluidos los de escritura -- pasado como
#: argumento `methods=[...]`. `get`/`head`/`options` no están en esta lista
#: a propósito: son los únicos verbos que esta API debe exponer.
_FORBIDDEN_ROUTE_DECORATOR_NAMES = frozenset({"post", "put", "patch", "delete", "api_route"})

#: Verbos de escritura de este proyecto: `Session.commit`/`.add`/`.flush`
#: (SQLAlchemy), `ItemRepository.add_many`, `FindingRepository`/
#: `ItemRepository.save` (`infrastructure/db/repositories.py`).
#: `unpublished_for_run` no escribe nada, pero es el único método de
#: `FindingRepository` que devuelve hallazgos sin publicar (candidatos
#: pendientes de decisión del Editor, `domain/repositories.py`): si un
#: endpoint de esta API lo invocara, filtraría exactamente lo que
#: `test_api_schemas.py` y las dos barreras de `list_findings.py` existen
#: para impedir. Vigilarlo aquí es la misma lógica que vigilar los verbos de
#: escritura, aunque el nombre no empiece por un verbo de mutación.
_FORBIDDEN_WRITE_CALL_NAMES = frozenset(
    {"commit", "add", "add_many", "save", "flush", "unpublished_for_run"}
)

#: Módulos cuyo import, en cualquier fichero de `api/`, es la señal de que
#: algo está construyendo un camino hacia Claude o hacia el control de gasto
#: del pipeline desde la capa de lectura.
_FORBIDDEN_IMPORT_MODULES = (
    "claude_agent_sdk",
    "nocturna.infrastructure.llm",
    "nocturna.application.agents",
    "nocturna.application.budget",
    "nocturna.application.unit_of_work",
)


def _api_files() -> list[Path]:
    return sorted(API_DIR.rglob("*.py"))


def _route_decorator_names(path: Path) -> list[str]:
    """Nombres de decorador de ruta prohibidos encontrados en `path`.

    Reconoce `@algo.post(...)`, `@algo.put(...)`, etc. -- cualquier
    `ast.Attribute` cuyo nombre de atributo esté en
    `_FORBIDDEN_ROUTE_DECORATOR_NAMES`, sin exigir que `algo` se llame
    literalmente `router` o `app`: da igual el nombre de la variable, lo que
    importa es el verbo HTTP.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            for decorator in node.decorator_list:
                func = decorator.func if isinstance(decorator, ast.Call) else decorator
                if (
                    isinstance(func, ast.Attribute)
                    and func.attr in _FORBIDDEN_ROUTE_DECORATOR_NAMES
                ):
                    found.append(f"{path}:{decorator.lineno}: @...{func.attr}(...)")
    return found


def _forbidden_call_sites(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    sites: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name in _FORBIDDEN_WRITE_CALL_NAMES:
                sites.append(f"{path}:{node.lineno}: {name}(...)")
    return sites


def _forbidden_root(module_name: str) -> str | None:
    for forbidden in _FORBIDDEN_IMPORT_MODULES:
        if module_name == forbidden or module_name.startswith(f"{forbidden}."):
            return forbidden
    return None


def _forbidden_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                forbidden = _forbidden_root(alias.name)
                if forbidden:
                    violations.append(
                        f"{path}:{node.lineno}: import {alias.name} (regla: {forbidden})"
                    )
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            forbidden = _forbidden_root(node.module)
            if forbidden:
                violations.append(
                    f"{path}:{node.lineno}: from {node.module} import ... (regla: {forbidden})"
                )
    return violations


def test_el_directorio_api_existe_y_tiene_ficheros():
    assert API_DIR.is_dir(), f"no existe {API_DIR}"
    assert _api_files(), "api/ no debe estar vacío"


def test_ningun_fichero_de_api_declara_un_decorador_de_ruta_de_escritura():
    violations: list[str] = []
    for path in _api_files():
        violations.extend(_route_decorator_names(path))

    assert not violations, (
        "decorador de ruta de escritura encontrado en api/ (solo GET está permitido "
        f"en fase 1, CLAUDE.md § Web fase 1): {violations}"
    )


def test_ningun_fichero_de_api_llama_a_un_verbo_de_escritura():
    violations: list[str] = []
    for path in _api_files():
        violations.extend(_forbidden_call_sites(path))

    assert not violations, (
        "llamada a un verbo de escritura o a unpublished_for_run "
        "(commit/add/add_many/save/flush/unpublished_for_run) encontrada en api/: la API de "
        f"lectura no debe poder persistir nada ni leer candidatos sin publicar. {violations}"
    )


def test_ningun_fichero_de_api_importa_claude_ni_agentes_ni_presupuesto_ni_unit_of_work():
    violations: list[str] = []
    for path in _api_files():
        violations.extend(_forbidden_imports(path))

    assert not violations, (
        "import prohibido encontrado en api/ (la API nunca llama a Claude ni depende "
        f"de la maquinaria de gasto del pipeline, CLAUDE.md § Web fase 1): {violations}"
    )
