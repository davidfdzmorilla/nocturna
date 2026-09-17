"""Test AST sobre `backend/src` y `backend/tests`, hermano de
`test_domain_purity.py`: en vez de una regla de dependencia entre capas,
aquí la regla es sobre quién puede tocar `claude_agent_sdk` y quién puede
invocar `LLMProvider.run_agent`.

**Qué NO es este fichero (corregido en T40, segunda revisión).** No es la
guarda que impide gastar suscripción de verdad -- esa es
`SubprocessCLITransport.connect`, parcheada en `conftest.py` (`_no_claude`,
ver su docstring para el porqué: es el único punto por el que pasan tanto
`query()` como `ClaudeSDKClient` antes de lanzar el CLI, así que cierra el
agujero con independencia de qué nombre se haya importado o cuándo). Las
dos revisiones anteriores de T40 fueron RECHAZADAS precisamente porque este
fichero se presentaba como si cerrara ese agujero (mirando solo
`alias.name == "query"` y solo bajo `backend/src`) sin hacerlo: ni veía
`ClaudeSDKClient`, ni miraba `backend/tests`, que es justo donde vive el
riesgo real (un test, no código de producción, es lo único que puede
vincular un nombre de `claude_agent_sdk` en tiempo de recolección, antes de
que cualquier fixture corra).

Con la guarda real ya cerrada en `SubprocessCLITransport.connect`, el valor
de este fichero es **disciplina y detección temprana**, no el cierre del
agujero:

- En `backend/src`: sigue congelando que solo
  `infrastructure/llm/agent_sdk_provider.py` puede hacer
  `from claude_agent_sdk import query`, y que `run_agent(` no se llama fuera
  de `infrastructure/llm/` (para T41-T43; ver más abajo).
- En `backend/tests`: detecta cualquier import de `claude_agent_sdk` --
  sea el módulo entero (`import claude_agent_sdk`, que da acceso dinámico a
  cualquier atributo incluido `query`) o un `from claude_agent_sdk import
  query`/`ClaudeSDKClient` -- fuera de los sitios legítimos
  (`tests/conftest.py`, que es donde vive la guarda; `tests/manual/`, el
  único camino que puede llamar de verdad; `tests/test_no_claude_guard.py`,
  que existe para probar la guarda bajo la guarda activa). También detecta
  el mismo patrón contra el RE-EXPORT de `agent_sdk_provider`
  (`from nocturna.infrastructure.llm.agent_sdk_provider import query`,
  segunda revisión de T40: ver `_sensitive_claude_agent_sdk_imports`).
  Importar solo TIPOS del SDK (`from claude_agent_sdk import ResultMessage`,
  como hace `tests/helpers/sdk_doubles.py` para construir mensajes reales
  sin abrir transporte) sigue permitido en cualquier sitio: no toca `query`,
  `ClaudeSDKClient` ni ningún submódulo, así que no hay nada que un test
  pudiera vincular de forma peligrosa.

Si un test de una tarea futura (T41 en adelante) mete
`from claude_agent_sdk import query` a nivel de módulo fuera de esos tres
sitios, esta prueba se pone en rojo aunque `SubprocessCLITransport.connect`
siga bloqueando la llamada real -- el objetivo es que ese import quede
señalado como una decisión consciente (ampliar la lista de sitios legítimos
aquí) y no un descuido silencioso.

También congela, para T41-T43, que hoy `run_agent(` no se llama desde
ningún sitio de `src/` fuera de `infrastructure/llm/`: el valor no es que
esa cifra vaya a seguir siendo cero -- el orquestador de T41 tendrá que
llamarlo --, sino que la primera vez que aparezca fuera de esa carpeta sea
una decisión consciente que rompa este test y lo obligue a actualizarse
explícitamente, no un descuido.
"""

import ast
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = BACKEND_ROOT / "src" / "nocturna"
TESTS_DIR = BACKEND_ROOT / "tests"
AGENT_SDK_PROVIDER_PATH = SRC_DIR / "infrastructure" / "llm" / "agent_sdk_provider.py"

FORBIDDEN_IN_AGENT_SDK_PROVIDER = (
    "nocturna.application",
    "nocturna.infrastructure.config",
    "nocturna.infrastructure.db",
)

#: Nombres de `claude_agent_sdk` que, importados con nombre propio
#: (`from claude_agent_sdk import ...`), abren transporte real: `query()` lo
#: hace directamente, `ClaudeSDKClient` lo hace en cuanto se llama a
#: `.connect()`. El resto de nombres exportados por el paquete (mensajes,
#: opciones, excepciones...) son tipos: construirlos o pasarlos no lanza
#: ningún subproceso.
_TRANSPORT_OPENING_NAMES = frozenset({"query", "ClaudeSDKClient"})

#: Módulo dotted-path de `agent_sdk_provider`: hace
#: `from claude_agent_sdk import query` a nivel de módulo (ver
#: `AGENT_SDK_PROVIDER_PATH`), así que `nocturna.infrastructure.llm.
#: agent_sdk_provider.query` es un RE-EXPORT del `query` real, ya vinculado.
#: Segunda revisión de T40: `from nocturna.infrastructure.llm.
#: agent_sdk_provider import query` en un test vincula ese `query` real sin
#: nombrar nunca `claude_agent_sdk`, así que quedaba fuera de
#: `_sensitive_claude_agent_sdk_imports` -- falso negativo demostrado por el
#: revisor (5 passed con ese import presente). El patrón legítimo que ya usan
#: todos los tests existentes, `from nocturna.infrastructure.llm import
#: agent_sdk_provider` seguido de `agent_sdk_provider.query`, sigue permitido:
#: es acceso dinámico por atributo, no vincula el nombre en tiempo de
#: recolección, así que el parcheo de `conftest.py` (que corre antes de cada
#: test) sí lo alcanza.
_AGENT_SDK_PROVIDER_MODULE = "nocturna.infrastructure.llm.agent_sdk_provider"
_PROVIDER_REEXPORTED_TRANSPORT_NAMES = frozenset({"query"})

#: Ficheros/directorios de `backend/tests` donde SÍ es legítimo importar
#: `claude_agent_sdk` de forma sensible (el módulo entero, o
#: `query`/`ClaudeSDKClient` por nombre): son los tres sitios que la guarda
#: anti-Claude y sus propios tests necesitan tocar a propósito.
_CONFTEST_PATH = TESTS_DIR / "conftest.py"
_NO_CLAUDE_GUARD_TEST_PATH = TESTS_DIR / "test_no_claude_guard.py"
_MANUAL_DIR = TESTS_DIR / "manual"


def _python_files() -> list[Path]:
    return sorted(SRC_DIR.rglob("*.py"))


def _imports_claude_agent_sdk_query(path: Path) -> bool:
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "claude_agent_sdk":
            if any(alias.name == "query" for alias in node.names):
                return True
    return False


def _run_agent_call_sites(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    sites: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if name == "run_agent":
                sites.append(f"{path}:{node.lineno}")
    return sites


def _module_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(), filename=str(path))
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)
    return modules


def test_query_de_claude_agent_sdk_se_importa_en_un_unico_fichero():
    importers = [path for path in _python_files() if _imports_claude_agent_sdk_query(path)]

    assert importers == [AGENT_SDK_PROVIDER_PATH], (
        "solo 'infrastructure/llm/agent_sdk_provider.py' puede hacer "
        "'from claude_agent_sdk import query' en backend/src: si aparece un "
        "segundo importador, es una decisión consciente (más de un módulo de "
        "producción llamando al SDK) que hay que revisar y reflejar aquí a "
        "la vez. importadores encontrados: "
        f"{[str(p) for p in importers]}"
    )


def test_run_agent_no_se_llama_fuera_de_infrastructure_llm():
    infrastructure_llm_dir = SRC_DIR / "infrastructure" / "llm"
    sites: list[str] = []
    for path in _python_files():
        if infrastructure_llm_dir in path.parents:
            continue
        sites.extend(_run_agent_call_sites(path))

    assert not sites, (
        "run_agent() llamado fuera de infrastructure/llm/ por primera vez: "
        "esto es esperado a partir de T41 (el orquestador), pero es una "
        "decisión consciente, no un descuido -- actualiza este test para "
        f"reflejarlo. Sitios encontrados: {sites}"
    )


def test_agent_sdk_provider_no_importa_application_ni_config_ni_db():
    modules = _module_imports(AGENT_SDK_PROVIDER_PATH)

    violations = [
        module
        for module in modules
        if any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for forbidden in FORBIDDEN_IN_AGENT_SDK_PROVIDER
        )
    ]

    assert not violations, f"imports prohibidos en agent_sdk_provider.py: {violations}"


def _tests_python_files() -> list[Path]:
    return sorted(TESTS_DIR.rglob("*.py"))


def _is_legitimate_sensitive_import_site(path: Path) -> bool:
    if path in (_CONFTEST_PATH, _NO_CLAUDE_GUARD_TEST_PATH):
        return True
    return _MANUAL_DIR in path.parents


def _sensitive_claude_agent_sdk_imports(path: Path) -> list[str]:
    """Imports de `claude_agent_sdk` en `path` que NO son "solo tipos".

    Dos patrones cuentan como sensibles:

    - `import claude_agent_sdk` (o `import claude_agent_sdk.cualquier_cosa`,
      incluido `_internal`): el módulo entero, con acceso dinámico por
      atributo a `query`/`ClaudeSDKClient` en cuanto se ejecuta
      `claude_agent_sdk.query(...)`, como hace deliberadamente
      `test_no_claude_guard.py`.
    - `from claude_agent_sdk import query` / `... import ClaudeSDKClient`
      (con o sin alias): vincula el nombre real en el momento del import,
      exactamente el patrón que en T40 dejó pasar una llamada real cuando se
      hace a nivel de módulo, antes de que ninguna guarda corra.
      `from claude_agent_sdk import <solo tipos>` (p. ej. `ResultMessage`,
      `AssistantMessage`) NO cuenta: no da acceso a nada que abra
      transporte.
    - `from claude_agent_sdk.<submódulo> import lo-que-sea`: se trata entero
      como sensible, a propósito. Los tipos públicos documentados viven en
      el paquete `claude_agent_sdk` de nivel superior (ver
      `tests/helpers/sdk_doubles.py`); un import directo a un submódulo
      (`_internal` u otro) es exactamente la clase de acceso que esta regla
      quiere que pase solo por los sitios legítimos, así que no vale la pena
      distinguir tipos de transporte ahí dentro.
    - `from nocturna.infrastructure.llm.agent_sdk_provider import query`
      (con o sin alias): `agent_sdk_provider` ya hace
      `from claude_agent_sdk import query` a nivel de módulo, así que este
      import es un RE-EXPORT que vincula el mismo `query` real, sin nombrar
      nunca `claude_agent_sdk` -- el falso negativo que demostró la segunda
      revisión de T40. El patrón `from nocturna.infrastructure.llm import
      agent_sdk_provider` seguido de acceso por atributo
      (`agent_sdk_provider.query`), que es el que usan todos los tests
      legítimos hoy, NO cuenta: no vincula el nombre en tiempo de
      recolección, así que el parcheo de `conftest.py` lo sigue alcanzando.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "claude_agent_sdk" or alias.name.startswith("claude_agent_sdk."):
                    findings.append(f"{path}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            if node.module == "claude_agent_sdk":
                sensitive_names = [
                    alias.name for alias in node.names if alias.name in _TRANSPORT_OPENING_NAMES
                ]
                if sensitive_names:
                    findings.append(
                        f"{path}:{node.lineno}: from claude_agent_sdk import "
                        f"{', '.join(sensitive_names)}"
                    )
            elif node.module.startswith("claude_agent_sdk."):
                names = ", ".join(alias.name for alias in node.names)
                findings.append(f"{path}:{node.lineno}: from {node.module} import {names}")
            elif node.module == _AGENT_SDK_PROVIDER_MODULE:
                sensitive_names = [
                    alias.name
                    for alias in node.names
                    if alias.name in _PROVIDER_REEXPORTED_TRANSPORT_NAMES
                ]
                if sensitive_names:
                    findings.append(
                        f"{path}:{node.lineno}: from {node.module} import "
                        f"{', '.join(sensitive_names)}"
                    )
    return findings


def test_import_de_claude_agent_sdk_que_abre_transporte_solo_en_sitios_legitimos():
    """Ningún fichero de `backend/tests` fuera de los tres sitios legítimos
    puede importar `claude_agent_sdk` de forma sensible (ver
    `_sensitive_claude_agent_sdk_imports`). Un test futuro (T41 en adelante)
    que haga `from claude_agent_sdk import query` a nivel de módulo pone
    esta prueba en rojo, aunque `SubprocessCLITransport.connect` (parcheada
    en `conftest.py`) siga bloqueando la llamada real: el objetivo es que
    ese import sea una decisión consciente, no un descuido silencioso.
    """
    violations: list[str] = []
    for path in _tests_python_files():
        if _is_legitimate_sensitive_import_site(path):
            continue
        violations.extend(_sensitive_claude_agent_sdk_imports(path))

    assert not violations, (
        "import de claude_agent_sdk que abre transporte (o da acceso dinámico "
        "a 'query'/'ClaudeSDKClient') fuera de los sitios legítimos de "
        "backend/tests (tests/conftest.py, tests/test_no_claude_guard.py, "
        "tests/manual/): importar solo TIPOS del paquete público "
        "('from claude_agent_sdk import ResultMessage', etc., como hace "
        "tests/helpers/sdk_doubles.py) sigue permitido en cualquier sitio. "
        "Si esto es intencional, amplía la lista de sitios legítimos aquí a "
        "la vez que la guarda '_no_claude' de conftest.py, conscientemente. "
        f"Violaciones encontradas: {violations}"
    )


def test_reexport_de_query_desde_agent_sdk_provider_tambien_se_detecta(tmp_path):
    """Regresión del falso negativo que la segunda revisión de T40 demostró:
    `from nocturna.infrastructure.llm.agent_sdk_provider import query`
    vincula el `query` real (re-exportado por `agent_sdk_provider`, que a su
    vez hace `from claude_agent_sdk import query`) sin nombrar nunca
    `claude_agent_sdk`, así que `_sensitive_claude_agent_sdk_imports` lo
    dejaba pasar (el revisor lo probó: 5 passed con ese import presente en
    un fichero de `backend/tests`).

    Prueba el helper directamente contra un fichero sintético en `tmp_path`,
    no contra un fichero real dentro de `backend/tests`: no queremos dejar
    un import sensible de verdad en el árbol de tests solo para ejercitar
    esta regla -- eso sería recrear el propio riesgo que la regla existe
    para señalar.
    """
    synthetic = tmp_path / "test_synthetic_reexport.py"
    synthetic.write_text("from nocturna.infrastructure.llm.agent_sdk_provider import query\n")

    findings = _sensitive_claude_agent_sdk_imports(synthetic)

    assert findings, (
        "_sensitive_claude_agent_sdk_imports no detectó "
        "'from nocturna.infrastructure.llm.agent_sdk_provider import query': "
        "el re-export del provider debe tratarse como sensible igual que "
        "'from claude_agent_sdk import query' -- ver _AGENT_SDK_PROVIDER_MODULE"
    )

    # El patrón legítimo (módulo + acceso por atributo) sigue sin marcarse:
    # no vincula el nombre en tiempo de recolección, así que el parcheo de
    # conftest.py lo alcanza igual que hoy.
    legitimate = tmp_path / "test_synthetic_attribute_access.py"
    legitimate.write_text(
        "from nocturna.infrastructure.llm import agent_sdk_provider\n"
        "\n"
        "\n"
        "def use():\n"
        "    return agent_sdk_provider.query\n"
    )

    assert _sensitive_claude_agent_sdk_imports(legitimate) == [], (
        "el patrón legítimo 'from nocturna.infrastructure.llm import "
        "agent_sdk_provider' + acceso por atributo no debería marcarse como "
        "sensible: es el que usan hoy todos los tests de agent_sdk_provider"
    )
