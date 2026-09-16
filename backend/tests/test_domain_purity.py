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
# hablar directamente con infraestructura concreta.
FORBIDDEN_APPLICATION_MODULES = (
    "nocturna.infrastructure",
    "nocturna.api",
    "httpx",
    "claude_agent_sdk",
)


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


@pytest.mark.parametrize(
    "path",
    _application_files(),
    ids=[p.relative_to(APPLICATION_DIR).as_posix() for p in _application_files()],
)
def test_fichero_de_application_no_importa_infraestructura_ni_sdk_ni_httpx(path):
    violations = _find_forbidden_imports(path, FORBIDDEN_APPLICATION_MODULES)

    assert not violations, "imports prohibidos:\n" + "\n".join(violations)
