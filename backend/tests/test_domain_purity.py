"""Test de pureza de `domain/`: cero dependencias de infraestructura.

Cubre literalmente el "Hecho cuando" de T10: "Cero imports de SQLAlchemy o
del SDK en `domain/`". Recorre con `ast` TODOS los ficheros `.py` de
`domain/` -incluidos los que se añadan en el futuro, sin listarlos a mano- y
falla si aparece cualquier import prohibido, esté a nivel de módulo o
escondido dentro de una función. El hook `guard-write.sh` solo mira imports
de nivel de módulo; este test cubre ese hueco.
"""

import ast
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DOMAIN_DIR = BACKEND_ROOT / "src" / "nocturna" / "domain"

FORBIDDEN_MODULES = (
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


def _forbidden_root(module_name: str) -> str | None:
    for forbidden in FORBIDDEN_MODULES:
        if module_name == forbidden or module_name.startswith(f"{forbidden}."):
            return forbidden
    return None


def _domain_files() -> list[Path]:
    return sorted(DOMAIN_DIR.rglob("*.py"))


def _find_forbidden_imports(path: Path) -> list[str]:
    """Devuelve una línea por violación, incluidas las que están dentro de
    funciones: `ast.walk` recorre todo el árbol, no solo el nivel de módulo.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                forbidden = _forbidden_root(alias.name)
                if forbidden:
                    violations.append(
                        f"{path}:{node.lineno}: import prohibido '{alias.name}' "
                        f"(regla: {forbidden})"
                    )
        elif isinstance(node, ast.ImportFrom):
            module_name = node.module or ""
            forbidden = _forbidden_root(module_name)
            if forbidden:
                violations.append(
                    f"{path}:{node.lineno}: import prohibido "
                    f"'from {module_name} import ...' (regla: {forbidden})"
                )
    return violations


def test_el_directorio_domain_existe_y_tiene_ficheros():
    assert DOMAIN_DIR.is_dir(), f"no existe {DOMAIN_DIR}"
    assert _domain_files(), "domain/ no debe estar vacío"


@pytest.mark.parametrize(
    "path",
    _domain_files(),
    ids=[p.relative_to(DOMAIN_DIR).as_posix() for p in _domain_files()],
)
def test_fichero_de_domain_no_importa_infraestructura_ni_sdk(path):
    violations = _find_forbidden_imports(path)

    assert not violations, "imports prohibidos:\n" + "\n".join(violations)
