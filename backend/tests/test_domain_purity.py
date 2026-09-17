"""Test de pureza de `domain/` y de la regla de dependencia de `application/`.

`domain/` cubre literalmente el "Hecho cuando" de T10: "Cero imports de
SQLAlchemy o del SDK en `domain/`". `application/` cubre la regla de
dependencia de `CLAUDE.md` ("repositorios como interfaces en `domain`
implementadas en `infrastructure`", dependencias solo hacia dentro):
`application/` puede importar `domain/`, pero nunca `infrastructure/` ni
`api/`, ni bibliotecas de infraestructura concretas (`httpx`,
`claude_agent_sdk`) directamente -- eso es justo lo que las interfaces de
`domain/` (`ItemRepository`, `ArxivSource`, `LLMProvider`) existen para
evitar.

Ambos recorren con `ast` TODOS los ficheros `.py` del directorio -incluidos
los que se añadan en el futuro, sin listarlos a mano- y fallan si aparece
cualquier import prohibido, esté a nivel de módulo o escondido dentro de una
función. El hook `guard-write.sh` solo mira imports de nivel de módulo; este
test cubre ese hueco.
"""

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = BACKEND_ROOT / "src" / "nocturna" / "domain"
APPLICATION_DIR = BACKEND_ROOT / "src" / "nocturna" / "application"

FORBIDDEN_DOMAIN_MODULES = (
    "sqlalchemy",
    "claude_agent_sdk",
    "fastapi",
    "httpx",
    "requests",
    "pydantic",
    "nocturna.application",
    "nocturna.infrastructure",
    "nocturna.api",
    "anthropic",
    "psycopg",
    "socket",
    "urllib",
    "subprocess",
)

# `application/` sí puede importar `nocturna.domain`: es la única capa de la
# que depende. Lo prohibido es saltarse las interfaces de dominio para
# hablar directamente con infraestructura concreta. `pydantic` también está
# vetado aquí (revisión de T30): `BudgetPolicy` es deliberadamente una
# `dataclass` de la biblioteca estándar, no un `BaseModel` -- ver su
# docstring en `application/budget.py` -- precisamente porque la validación
# del TOML ya ocurrió en `infrastructure/config.py`; `application/` no debe
# volver a depender de Pydantic para repetirla.
FORBIDDEN_APPLICATION_MODULES = (
    "nocturna.infrastructure",
    "nocturna.api",
    "httpx",
    "claude_agent_sdk",
    "pydantic",
)

# `application/agents/` es la única excepción al veto de `pydantic` de
# arriba, aprobada explícitamente por el autor para T41. El motivo original
# del veto (T30, revisión de `BudgetPolicy`) es "no repetir una validación
# que ya ocurrió en `infrastructure/config.py`": el TOML ya se valida una
# vez al cargarlo, así que `application/budget.py` no necesita una segunda
# capa de validación sobre datos que ya son de confianza. Ese motivo no
# cubre `application/agents/reader_output.py`: la salida cruda de un agente
# LLM no se valida en NINGÚN otro punto del pipeline -- ni antes ni después
# -- así que aquí sí hace falta una primera (y única) validación real, y
# `CLAUDE.md` (sección "Agentes (fase 1)": "Salida siempre en JSON validado
# con Pydantic") y `ARCHITECTURE.md:109` piden Pydantic exactamente para
# esto. La excepción es del subpaquete `application/agents/`, no de
# `application/` entero: cualquier otro módulo de `application/` (empezando
# por `budget.py` o un futuro caso de uso) sigue vetado, y así lo comprueba
# `test_pydantic_solo_se_permite_dentro_de_application_agents` más abajo.
APPLICATION_MODULES_ALLOWING_PYDANTIC = ("nocturna.application.agents",)


def _forbidden_root(module_name: str, forbidden_modules: tuple[str, ...]) -> str | None:
    for forbidden in forbidden_modules:
        if module_name == forbidden or module_name.startswith(f"{forbidden}."):
            return forbidden
    return None


def _python_files(directory: Path) -> list[Path]:
    return sorted(directory.rglob("*.py"))


def _find_forbidden_imports(path: Path, forbidden_modules: tuple[str, ...]) -> list[str]:
    """Devuelve una línea por violación, incluidas las que están dentro de
    funciones: `ast.walk` recorre todo el árbol, no solo el nivel de módulo.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                forbidden = _forbidden_root(alias.name, forbidden_modules)
                if forbidden:
                    violations.append(
                        f"{path}:{node.lineno}: import prohibido '{alias.name}' "
                        f"(regla: {forbidden})"
                    )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            forbidden = _forbidden_root(module_name, forbidden_modules)
            if forbidden:
                violations.append(
                    f"{path}:{node.lineno}: import prohibido "
                    f"'from {module_name} import ...' (regla: {forbidden})"
                )
    return violations


def _domain_files() -> list[Path]:
    return _python_files(DOMAIN_DIR)


def _application_files() -> list[Path]:
    return _python_files(APPLICATION_DIR)


def test_el_directorio_domain_existe_y_tiene_ficheros():
    assert DOMAIN_DIR.is_dir(), f"no existe {DOMAIN_DIR}"
    assert _domain_files(), "domain/ no debe estar vacío"


def test_el_directorio_application_existe_y_tiene_ficheros():
    assert APPLICATION_DIR.is_dir(), f"no existe {APPLICATION_DIR}"
    assert _application_files(), "application/ no debe estar vacío"


@pytest.mark.parametrize(
    "path",
    _domain_files(),
    ids=[p.relative_to(DOMAIN_DIR).as_posix() for p in _domain_files()],
)
def test_fichero_de_domain_no_importa_infraestructura_ni_sdk(path):
    violations = _find_forbidden_imports(path, FORBIDDEN_DOMAIN_MODULES)

    assert not violations, "imports prohibidos:\n" + "\n".join(violations)


def _is_under(path: Path, package_dotted: str) -> bool:
    """`True` si `path` vive dentro del subpaquete `package_dotted` (p. ej.
    `"nocturna.application.agents"`) relativo a `APPLICATION_DIR`.
    """
    package_relative = Path(*package_dotted.split(".")[2:])  # sin "nocturna.application"
    package_dir = APPLICATION_DIR / package_relative
    return package_dir == path or package_dir in path.parents


def _forbidden_modules_for(path: Path) -> tuple[str, ...]:
    if any(_is_under(path, allowed) for allowed in APPLICATION_MODULES_ALLOWING_PYDANTIC):
        return tuple(m for m in FORBIDDEN_APPLICATION_MODULES if m != "pydantic")
    return FORBIDDEN_APPLICATION_MODULES


@pytest.mark.parametrize(
    "path",
    _application_files(),
    ids=[p.relative_to(APPLICATION_DIR).as_posix() for p in _application_files()],
)
def test_fichero_de_application_no_importa_infraestructura_ni_sdk_ni_httpx(path):
    violations = _find_forbidden_imports(path, _forbidden_modules_for(path))

    assert not violations, "imports prohibidos:\n" + "\n".join(violations)


# --- El veto a pydantic solo se levanta para application/agents/ ----------


def test_pydantic_solo_se_permite_dentro_de_application_agents():
    """La excepción de `APPLICATION_MODULES_ALLOWING_PYDANTIC` es del
    subpaquete `application/agents/`, no un agujero general en
    `application/`: cualquier fichero de `application/` que NO esté bajo
    `application/agents/` sigue vetado para `pydantic`, `budget.py`
    incluido.
    """
    non_agents_files = [
        path for path in _application_files() if not _is_under(path, "nocturna.application.agents")
    ]

    assert non_agents_files, "no hay ficheros de application/ fuera de agents/ que comprobar"
    for path in non_agents_files:
        assert "pydantic" in _forbidden_modules_for(path)


def test_reader_output_importa_pydantic_de_verdad_no_es_una_excepcion_vacia():
    """Ancla de mutación: si `application/agents/reader_output.py` dejara de
    importar `pydantic`, la excepción del test anterior sería una excepción
    para nada -- este test confirma que hay un import real que la necesita.
    """
    reader_output_path = APPLICATION_DIR / "agents" / "reader_output.py"

    assert reader_output_path.is_file(), f"no existe {reader_output_path}"
    violations = _find_forbidden_imports(reader_output_path, ("pydantic",))

    assert violations, "reader_output.py debería importar pydantic (y esta prueba lo confirma)"
