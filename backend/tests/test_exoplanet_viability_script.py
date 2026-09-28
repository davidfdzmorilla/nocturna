"""Tests de contrato y de regresión de `backend/scripts/exoplanet_viability.py` (T71).

El script ya existe e implementa `normalize_name`, `match_object`,
`parse_measurements`, `arxiv_bibcode_fragment`, `own_solutions`, `sigma`,
`parse_catalog_row`, `ArchiveClient`, `load_ep_readings`, `build_report`,
`render_text` y `run_experiment`. Este fichero fija su contrato función a
función (secciones 1-14) y, tras la revisión que rechazó la primera
versión, añade una sección de **regresión** (18): cada test reproduce,
con el literal exacto que dio el reviewer, un fallo concreto de la
primera versión de `parse_measurements`/`sigma`/`parse_catalog_row`/
`own_solutions`/`build_report`/`run_experiment`. Esos fallos ya están
corregidos y la sección pasa en verde; se queda para que no vuelvan a
aparecer. La sección 19 endurece el criterio de `parse_measurements` a
uno estricto -- la unidad solo cuenta si va inmediatamente después del
error, sin búsqueda más allá de ese punto. Ningún test de este fichero
se relaja para pasar ni el script se toca desde aquí.

## Precedentes copiados

- `tests/test_seed_demo_script.py`: patrón de guarda por variable de
  entorno (subproceso limpio, sin PostgreSQL, exit code + mensaje en
  stderr) y las dos comprobaciones de empaquetado (`[project.scripts]`,
  `[tool.hatch.build.targets.wheel].packages`).
- `tests/test_api_no_claude_import.py`: patrón de subproceso interprete-
  limpio para comprobar qué hay en `sys.modules` tras cargar un módulo.
- Skill `testing-without-claude`: ningún test de este fichero llama a
  Claude ni a la red real; `httpx.MockTransport` sirve las fixtures de
  `tests/fixtures/exoplanet_archive/`. El propio `conftest.py` bloquea
  `httpx.HTTPTransport`/`AsyncHTTPTransport` reales y el CLI `claude`
  (`_no_network`, `_no_claude`), pero como este script no importa
  `claude_agent_sdk`, la única guarda que aplica de verdad aquí es la de
  red.

## Cómo se carga el script bajo test

`scripts/exoplanet_viability.py` vive fuera de `src/nocturna/` (mismo sitio
que `seed_demo.py`) y no se instala como paquete, así que no se puede hacer
`import nocturna_scripts.exoplanet_viability`. Se carga con
`importlib.util.spec_from_file_location("exoplanet_viability", SCRIPT_PATH)`
seguido de `exec_module`, y **se registra en `sys.modules` antes de
`exec_module`** (`_load_script`): el script usa `from __future__ import
annotations` y `@dataclass(frozen=True, slots=True)`, y algunas versiones
de `dataclasses`/`typing` resuelven anotaciones perezosas contra
`sys.modules[cls.__module__]` -- registrar el módulo primero es lo que
`test_api_no_claude_import.py` y el resto de la suite ya asumen para casos
así.

## Decisiones de forma no fijadas por el plan (documentadas aquí porque el
plan de T71 no bajaba a este nivel de detalle, no porque el código no
exista)

- `CatalogIndex`: `frozenset`/`dict` de nombres normalizados. Se construye
  con `CatalogIndex.from_rows(rows)`, donde `rows` es la salida de
  `ArchiveClient.query_csv` sobre la vista `pscomppars` con las columnas
  `pl_name,hostname,hd_name,hip_name,tic_id,sy_pnum` (las que sí existen,
  ver el README de las fixtures: `gaia_id` no existe). Cada fila aporta:
  el nombre de planeta exacto (`pl_name`) y, para cada alias del sistema
  (`hostname`, `hd_name`, `hip_name`, `tic_id`, todos opcionales salvo
  `hostname`), una referencia a ese `pl_name`. Cuando un alias de sistema
  referencia a más de un `pl_name` (sistema multiplanetario, `sy_pnum >
  1`), `match_object` debe devolver `kind="host_multi"` con todos los
  `pl_name` candidatos en `MatchResult.candidates`; con exactamente un
  `pl_name`, `kind="host_single"` y ese único nombre en
  `MatchResult.pl_name`.
- `MatchResult`: `kind: Literal["planet","host_single","host_multi",
  "unmatched"]`, `pl_name: str | None` (el planeta resuelto cuando
  `kind` es `"planet"` o `"host_single"`; `None` en los otros dos casos),
  `candidates: tuple[str, ...] = ()` (los `pl_name` del sistema cuando
  `kind == "host_multi"`; vacío en los demás casos).
- `parse_catalog_row(row: dict[str, str | None]) -> list[CatalogSolution]`:
  no está en la lista de funciones públicas del plan, pero hace falta para
  convertir una fila de la tabla `ps` (que trae masa, radio y periodo con
  su propio error/límite en la misma fila, ver
  `ps_wasp12_masses_radii_period.csv`) en `CatalogSolution` -- que solo
  lleva **una** `Measurement | None`. Devuelve siempre tres
  `CatalogSolution` (mismo `pl_name`/`hostname`/`refname`/`default_flag`,
  uno por parámetro: `mass_earth`, `radius_earth`, `period_days`), con
  `measurement=None` cuando ese parámetro no tiene valor, tiene el flag
  `*lim` distinto de `"0"`, o le falta alguno de los dos errores. El
  backend implementa contra los tests 10 y 11 de este fichero.
- `sigma(paper, prior)`: convención congelada por
  `test_sigma_*` de este fichero -- si los valores son iguales, `0.0`
  cualquiera que sean los errores; si no, se usa en cada medida el error
  del lado que mira hacia la otra medida (el que reduce la distancia
  entre ambas), no el error del lado que se aleja.
- `Case`/`Report` (entrada y salida de `build_report`): ver
  `test_build_report_*` de este fichero para su forma exacta. `Case` es
  una fila por intento de emparejamiento (un `external_id` sin `objects`
  produce un único `Case` con `raw_name=None`); `Report` agrupa los
  recuentos que pide el plan de T71.
- `ArchiveClient.query_csv` devuelve `list[dict[str, str | None]]`: una
  entrada por fila del CSV, claves = cabecera, valores `None` cuando la
  celda viene vacía (`""` en el CSV) y `str` en caso contrario -- nunca se
  convierte a `float`/`int` aquí, eso es cosa de `parse_catalog_row`.
"""

from __future__ import annotations

import importlib.util
import json
import math
import os
import subprocess
import sys
import textwrap
import tomllib
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, call
from uuid import uuid4

import httpx
import pytest

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = BACKEND_ROOT / "scripts" / "exoplanet_viability.py"
PYPROJECT_PATH = BACKEND_ROOT / "pyproject.toml"
FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "exoplanet_archive"

_ALLOW_ENV_VAR = "NOCTURNA_ALLOW_ARCHIVE_QUERY"

# Constantes de conversión que el propio plan de T71 pide declarar
# explícitamente en el test (no se importan del script: si el script usa
# un valor distinto, los tests de `parse_measurements` deben notarlo).
R_JUP_IN_R_EARTH = 11.209
M_JUP_IN_M_EARTH = 317.83


def _load_fixture_bytes(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _load_script() -> ModuleType:
    """Carga `scripts/exoplanet_viability.py` como módulo `exoplanet_viability`.

    Registra el módulo en `sys.modules` antes de `exec_module` -- ver la
    sección "Cómo se carga el script bajo test" del docstring de este
    fichero.
    """
    spec = importlib.util.spec_from_file_location("exoplanet_viability", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["exoplanet_viability"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script():
    if not SCRIPT_PATH.exists():
        pytest.fail(
            f"{SCRIPT_PATH} no existe: se ha borrado o movido. Este fichero fija su "
            "contrato contra ese script (T71); no lo recrees aquí, restaura el fichero."
        )
    return _load_script()


# ---------------------------------------------------------------------------
# 1. Integración en subproceso: run_experiment con MockTransport + fixtures,
#    sin claude_agent_sdk/nocturna.infrastructure.llm/nocturna.application.
# ---------------------------------------------------------------------------


_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import importlib.util
    import sys
    from pathlib import Path
    from uuid import uuid4

    import httpx

    SCRIPT_PATH = Path(sys.argv[1])
    FIXTURE_PATH = Path(sys.argv[2])

    spec = importlib.util.spec_from_file_location("exoplanet_viability", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["exoplanet_viability"] = module
    spec.loader.exec_module(module)

    ps_wasp12_csv = FIXTURE_PATH.read_bytes()

    def _handle(request: httpx.Request) -> httpx.Response:
        # Respuesta única para cualquier consulta: este test comprueba el
        # cableado de run_experiment, no la exactitud del cruce (eso lo
        # cubren los tests unitarios de match_object/parse_measurements/
        # own_solutions/sigma). Cualquier ADQL que el script decida enviar
        # recibe un CSV válido y parseable.
        return httpx.Response(200, content=ps_wasp12_csv, headers={"content-type": "text/csv"})

    http = httpx.Client(transport=httpx.MockTransport(_handle))
    client = module.ArchiveClient(http, max_requests=5, spacing_s=0.0, sleep=lambda s: None)

    reading = module.EpReading(
        external_id="2409.99999",
        title="A transit paper about WASP-12 b",
        abstract=r"We measure a radius of $1.347 \\pm 0.047 R_{\\rm Jup}$ for the planet.",
        objects=("WASP-12 b",),
        claims=(),
        run_ids=frozenset({uuid4()}),
    )

    report = module.run_experiment([reading], client)

    assert hasattr(report, "sigma_bins"), "run_experiment debe devolver un Report con sigma_bins"
    assert hasattr(report, "items_ep"), "run_experiment debe devolver un Report con items_ep"

    forbidden_prefixes = (
        "claude_agent_sdk",
        "nocturna.infrastructure.llm",
        "nocturna.application",
    )
    leaked = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    )
    if leaked:
        print(f"módulos prohibidos en sys.modules: {leaked}", file=sys.stderr)
        sys.exit(1)
    print("OK")
    """
)


def test_run_experiment_en_subproceso_no_importa_claude_ni_agentes():
    fixture_path = FIXTURES_DIR / "ps_wasp12_masses_radii_period.csv"
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(SCRIPT_PATH), str(fixture_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"subproceso falló (code={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout
    assert "Traceback" not in result.stderr


# ---------------------------------------------------------------------------
# 2. Guarda de entorno para main(): sin NOCTURNA_ALLOW_ARCHIVE_QUERY, exit 1,
#    remedio en stderr, sin conectar a nada.
# ---------------------------------------------------------------------------


def test_main_aborta_sin_la_variable_de_entorno():
    env = {"PATH": os.environ.get("PATH", "")}
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH)],
        cwd=BACKEND_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 1, (
        f"main() debe salir con código 1 si falta {_ALLOW_ENV_VAR}, sin intentar conectar "
        f"a la base de datos ni al Exoplanet Archive. stdout={result.stdout!r} "
        f"stderr={result.stderr!r}"
    )
    assert _ALLOW_ENV_VAR in result.stderr
    assert f"{_ALLOW_ENV_VAR}=1" in result.stderr


# ---------------------------------------------------------------------------
# 3. Fuera de [project.scripts] y de la wheel (mismo patrón que seed_demo.py).
# ---------------------------------------------------------------------------


def _load_pyproject() -> dict[str, object]:
    with PYPROJECT_PATH.open("rb") as f:
        return tomllib.load(f)


def test_no_esta_en_project_scripts():
    pyproject = _load_pyproject()
    scripts = pyproject["project"]["scripts"]

    assert scripts == {"nocturna": "nocturna.cli:main"}, (
        "[project.scripts] debe seguir teniendo una única entrada ('nocturna'); "
        f"se encontró {scripts!r}. exoplanet_viability.py no se registra aquí, igual "
        "que seed_demo.py: es una herramienta de mano, no un comando instalado."
    )


def test_no_esta_en_los_paquetes_de_la_wheel():
    pyproject = _load_pyproject()
    wheel_packages = pyproject["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert wheel_packages == ["src/nocturna"], (
        f"[tool.hatch.build.targets.wheel].packages debe seguir siendo ['src/nocturna']; "
        f"se encontró {wheel_packages!r}."
    )
    assert not any(
        SCRIPT_PATH.is_relative_to(BACKEND_ROOT / package) for package in wheel_packages
    ), "scripts/exoplanet_viability.py no debería quedar dentro de ningún paquete de la wheel"


# ---------------------------------------------------------------------------
# 4. normalize_name
# ---------------------------------------------------------------------------


def test_normalize_name_wasp12b_equivale_a_wasp12_espacio_b(script):
    assert script.normalize_name("WASP-12b") == script.normalize_name("WASP-12 b")


def test_normalize_name_ignora_variantes_unicode_de_guion(script):
    # en dash (U+2013), non-breaking hyphen (U+2011)
    variantes = ["WASP-12 b", "WASP–12 b", "WASP‑12 b"]
    normalizados = {script.normalize_name(v) for v in variantes}
    assert len(normalizados) == 1, f"todas las variantes deberían normalizar igual: {normalizados}"


def test_normalize_name_colapsa_espacios_repetidos(script):
    assert script.normalize_name("WASP-12   b") == script.normalize_name("WASP-12 b")


def test_normalize_name_distingue_nombres_distintos(script):
    assert script.normalize_name("WASP-12 b") != script.normalize_name("WASP-13 b")
    assert script.normalize_name("WASP-12") != script.normalize_name("WASP-12 b")


# ---------------------------------------------------------------------------
# 5. match_object
# ---------------------------------------------------------------------------


_CATALOG_ROWS = [
    {
        "pl_name": "WASP-12 b",
        "hostname": "WASP-12",
        "hd_name": None,
        "hip_name": None,
        "tic_id": "TIC 86396382",
        "sy_pnum": "1",
    },
    {
        "pl_name": "K2-43 b",
        "hostname": "K2-43",
        "hd_name": None,
        "hip_name": None,
        "tic_id": "TIC 443616612",
        "sy_pnum": "2",
    },
    {
        "pl_name": "K2-43 c",
        "hostname": "K2-43",
        "hd_name": None,
        "hip_name": None,
        "tic_id": "TIC 443616612",
        "sy_pnum": "2",
    },
    {
        "pl_name": "HD 2039 b",
        "hostname": "HD 2039",
        "hd_name": "HD 2039",
        "hip_name": "HIP 1931",
        "tic_id": "TIC 281461362",
        "sy_pnum": "1",
    },
]


def _build_index(script):
    return script.CatalogIndex.from_rows(_CATALOG_ROWS)


def test_match_object_planeta_exacto(script):
    index = _build_index(script)
    result = script.match_object("WASP-12 b", index)

    assert result.kind == "planet"
    assert result.pl_name == "WASP-12 b"


def test_match_object_anfitriona_de_un_solo_planeta(script):
    index = _build_index(script)
    result = script.match_object("WASP-12", index)

    assert result.kind == "host_single"
    assert result.pl_name == "WASP-12 b"


def test_match_object_anfitriona_multiplanetaria_es_ambigua(script):
    index = _build_index(script)
    result = script.match_object("K2-43", index)

    assert result.kind == "host_multi"
    assert result.pl_name is None
    assert set(result.candidates) == {"K2-43 b", "K2-43 c"}


def test_match_object_alias_local_hd_o_tic(script):
    index = _build_index(script)

    by_hd = script.match_object("HD 2039", index)
    assert by_hd.kind == "host_single"
    assert by_hd.pl_name == "HD 2039 b"

    by_tic = script.match_object("TIC 281461362", index)
    assert by_tic.kind == "host_single"
    assert by_tic.pl_name == "HD 2039 b"


def test_match_object_sin_emparejar(script):
    index = _build_index(script)
    result = script.match_object("Betelgeuse", index)

    assert result.kind == "unmatched"
    assert result.pl_name is None
    assert result.candidates == ()


# ---------------------------------------------------------------------------
# 6. parse_measurements
# ---------------------------------------------------------------------------


def test_parse_measurements_radio_en_jupiter_convertido_a_tierras(script):
    text = r"We derive $1.347 \pm 0.047 R_{\rm Jup}$ for the planetary radius."
    result = script.parse_measurements(text)

    assert len(result) == 1
    (measurement,) = result
    assert measurement.parameter == "radius_earth"
    assert measurement.value == pytest.approx(1.347 * R_JUP_IN_R_EARTH, rel=1e-6)
    assert measurement.err_plus == pytest.approx(0.047 * R_JUP_IN_R_EARTH, rel=1e-6)
    assert measurement.err_minus == pytest.approx(0.047 * R_JUP_IN_R_EARTH, rel=1e-6)


def test_parse_measurements_masa_en_jupiter_convertida_a_tierras(script):
    text = r"$5.02 \pm 0.75 M_{\rm Jup}$"
    (measurement,) = script.parse_measurements(text)

    assert measurement.parameter == "mass_earth"
    assert measurement.value == pytest.approx(5.02 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert measurement.err_plus == pytest.approx(0.75 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert measurement.err_minus == pytest.approx(0.75 * M_JUP_IN_M_EARTH, rel=1e-6)


def test_parse_measurements_periodo_orbital_en_dias(script):
    text = "the orbital period (0.67247479 $\\pm$ 0.00000028 days) is well constrained"
    (measurement,) = script.parse_measurements(text)

    assert measurement.parameter == "period_days"
    assert measurement.value == pytest.approx(0.67247479, rel=1e-9)
    assert measurement.err_plus == pytest.approx(0.00000028, rel=1e-6)
    assert measurement.err_minus == pytest.approx(0.00000028, rel=1e-6)


def test_parse_measurements_masa_asimetrica_con_guion_bajo(script):
    text = r"$M=2.8^{+0.5}_{-0.5}$ M$_{\text{Jup}}$"
    (measurement,) = script.parse_measurements(text)

    assert measurement.parameter == "mass_earth"
    assert measurement.value == pytest.approx(2.8 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert measurement.err_plus == pytest.approx(0.5 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert measurement.err_minus == pytest.approx(0.5 * M_JUP_IN_M_EARTH, rel=1e-6)


def test_parse_measurements_masa_asimetrica_sin_guion_bajo_en_el_error_inferior(script):
    """Variante real vista en la BD: `^{+0.12}{-0.14}` sin `_` antes de la
    llave del error negativo. `err_plus` y `err_minus` deben salir distintos
    (0.12 vs 0.14), confirmando que de verdad es asimétrica y no solo una
    notación distinta de un valor simétrico."""
    text = r"the mass is $0.52^{+0.12}{-0.14}$ $M_{\rm Jup}$"
    (measurement,) = script.parse_measurements(text)

    assert measurement.parameter == "mass_earth"
    assert measurement.value == pytest.approx(0.52 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert measurement.err_plus == pytest.approx(0.12 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert measurement.err_minus == pytest.approx(0.14 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert measurement.err_plus != measurement.err_minus


def test_parse_measurements_dos_masas_en_castellano_con_unicode(script):
    text = "el análisis da 54±3 y 258±11 masas terrestres para los dos planetas"
    result = script.parse_measurements(text)

    assert len(result) == 2
    first, second = result
    assert first.parameter == second.parameter == "mass_earth"
    assert first.value == pytest.approx(54.0)
    assert first.err_plus == pytest.approx(3.0)
    assert first.err_minus == pytest.approx(3.0)
    assert second.value == pytest.approx(258.0)
    assert second.err_plus == pytest.approx(11.0)
    assert second.err_minus == pytest.approx(11.0)


def test_parse_measurements_temperatura_no_es_masa_ni_radio_ni_periodo(script):
    text = "Teff = 960(+28/-32) K"
    assert script.parse_measurements(text) == []


def test_parse_measurements_periodo_de_rotacion_no_es_periodo_orbital(script):
    """Decisión congelada por T71: un 'periodo' solo cuenta como
    `period_days` si va precedido de 'orbital', 'periodo orbital' o 'P ='
    en un entorno razonable del número. 'periodo de rotación' (de la
    estrella, en minutos, no del planeta) se rechaza en vez de convertirse
    -- confundir un periodo de rotación estelar con el periodo orbital de
    un planeta sería peor que perder el dato."""
    text = "periodo de rotación de 5.3516 ± 0.0001 minutos para la estrella"
    assert script.parse_measurements(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "[M/H] = -0.33 ± 0.06 dex",
        "-3.46 ± 1.17 ms/yr",
        "51 +/- 31 %",
    ],
)
def test_parse_measurements_ignora_metalicidad_pm_y_porcentajes(script, text):
    assert script.parse_measurements(text) == []


def test_parse_measurements_valor_sin_error_no_produce_measurement(script):
    assert script.parse_measurements("the planet has a mass of 2.8 M_J") == []


@pytest.mark.parametrize(
    "text",
    [
        "the mass is < 5 M_J",
        "we report an upper limit of 5 M_J on the companion mass",
    ],
)
def test_parse_measurements_limites_no_producen_measurement(script, text):
    assert script.parse_measurements(text) == []


def test_parse_measurements_coma_decimal(script):
    text = "$1,347 \\pm 0,047 R_J$"
    (measurement,) = script.parse_measurements(text)

    assert measurement.parameter == "radius_earth"
    assert measurement.value == pytest.approx(1.347 * R_JUP_IN_R_EARTH, rel=1e-6)
    assert measurement.err_plus == pytest.approx(0.047 * R_JUP_IN_R_EARTH, rel=1e-6)


# ---------------------------------------------------------------------------
# 7. arxiv_bibcode_fragment
# ---------------------------------------------------------------------------


def test_arxiv_bibcode_fragment_id_nuevo(script):
    assert script.arxiv_bibcode_fragment("2409.12345") == "arXiv240912345"


def test_arxiv_bibcode_fragment_ignora_la_version(script):
    assert script.arxiv_bibcode_fragment("2409.12345v2") == "arXiv240912345"


def test_arxiv_bibcode_fragment_id_antiguo_devuelve_none(script):
    assert script.arxiv_bibcode_fragment("astro-ph/0601001") is None


# ---------------------------------------------------------------------------
# 8. own_solutions
# ---------------------------------------------------------------------------


def test_own_solutions_excluye_las_filas_propias_de_las_previas(script):
    own_measurement = script.Measurement(
        parameter="mass_earth", value=300.0, err_plus=10.0, err_minus=10.0
    )
    own = script.CatalogSolution(
        pl_name="WASP-12 b",
        refname="<a href=https://arxiv.org/abs/2409.12345>Autor et al. 2024, arXiv240912345</a>",
        default_flag=False,
        measurement=own_measurement,
    )
    ajena_1 = script.CatalogSolution(
        pl_name="WASP-12 b",
        refname=(
            "<a refstr=HEBB_ET_AL__2009 "
            "href=https://ui.adsabs.harvard.edu/abs/2009ApJ...693.1920H/abstract "
            "target=ref> Hebb et al. 2009 </a>"
        ),
        default_flag=False,
        measurement=script.Measurement(
            parameter="mass_earth", value=448.0, err_plus=32.0, err_minus=312.0
        ),
    )
    ajena_2 = script.CatalogSolution(
        pl_name="WASP-12 b",
        refname=(
            "<a refstr=KNUTSON_ET_AL__2014 "
            "href=https://ui.adsabs.harvard.edu/abs/2014ApJ...785..126K/abstract "
            "target=ref> Knutson et al. 2014 </a>"
        ),
        default_flag=False,
        measurement=script.Measurement(
            parameter="mass_earth", value=441.78, err_plus=41.32, err_minus=41.32
        ),
    )
    solutions = [ajena_1, own, ajena_2]

    result = script.own_solutions(solutions, "2409.12345")

    assert result == [own]

    previas = [s for s in solutions if s not in result]
    assert previas == [ajena_1, ajena_2]


def test_own_solutions_sin_coincidencia_devuelve_lista_vacia(script):
    ajena = script.CatalogSolution(
        pl_name="WASP-12 b",
        refname="<a href=https://ui.adsabs.harvard.edu/abs/2009ApJ...693.1920H/abstract>x</a>",
        default_flag=False,
        measurement=None,
    )

    assert script.own_solutions([ajena], "2409.12345") == []


# ---------------------------------------------------------------------------
# 9. sigma
# ---------------------------------------------------------------------------


def test_sigma_simetrico(script):
    paper = script.Measurement(parameter="mass_earth", value=10.0, err_plus=1.0, err_minus=1.0)
    prior = script.Measurement(parameter="mass_earth", value=8.0, err_plus=1.0, err_minus=1.0)

    result = script.sigma(paper, prior)

    assert result == pytest.approx(2.0 / math.sqrt(2.0))


def test_sigma_asimetrico_usa_el_error_que_mira_al_otro_valor(script):
    """paper (12) > prior (8): la distancia se cierra por abajo en el papel
    (usa `paper.err_minus`) y por arriba en la previa (usa `prior.err_plus`)."""
    paper = script.Measurement(parameter="mass_earth", value=12.0, err_plus=1.0, err_minus=2.0)
    prior = script.Measurement(parameter="mass_earth", value=8.0, err_plus=0.5, err_minus=1.5)

    result = script.sigma(paper, prior)

    esperado = (12.0 - 8.0) / math.sqrt(2.0**2 + 0.5**2)
    assert result == pytest.approx(esperado)


def test_sigma_asimetrico_direccion_contraria(script):
    """prior (12) > paper (8): ahora se usa `paper.err_plus` (hacia arriba,
    hacia la previa) y `prior.err_minus` (hacia abajo, hacia el papel)."""
    paper = script.Measurement(parameter="mass_earth", value=8.0, err_plus=1.0, err_minus=1.5)
    prior = script.Measurement(parameter="mass_earth", value=12.0, err_plus=0.5, err_minus=2.0)

    result = script.sigma(paper, prior)

    esperado = (12.0 - 8.0) / math.sqrt(1.0**2 + 2.0**2)
    assert result == pytest.approx(esperado)


def test_sigma_valores_iguales_es_cero_sin_importar_los_errores(script):
    paper = script.Measurement(parameter="mass_earth", value=5.0, err_plus=1.0, err_minus=3.0)
    prior = script.Measurement(parameter="mass_earth", value=5.0, err_plus=2.0, err_minus=0.1)

    assert script.sigma(paper, prior) == 0.0


# ---------------------------------------------------------------------------
# 10. filas de catálogo inválidas: *lim != 0 o sin error -> measurement=None
# ---------------------------------------------------------------------------


def test_parse_catalog_row_lim_distinto_de_cero_da_measurement_none(script):
    row = {
        "pl_name": "X-1 b",
        "hostname": "X-1",
        "default_flag": "0",
        "pl_refname": "<a href=https://ui.adsabs.harvard.edu/abs/2020X>ref</a>",
        "pl_bmasse": "500.0",
        "pl_bmasseerr1": None,
        "pl_bmasseerr2": None,
        "pl_bmasselim": "1",
        "pl_rade": None,
        "pl_radeerr1": None,
        "pl_radeerr2": None,
        "pl_radelim": "0",
        "pl_orbper": None,
        "pl_orbpererr1": None,
        "pl_orbpererr2": None,
        "pl_orbperlim": "0",
    }
    solutions = script.parse_catalog_row(row)

    by_param = {s.measurement.parameter: s for s in solutions if s.measurement is not None}
    assert "mass_earth" not in by_param


def test_parse_catalog_row_sin_error_da_measurement_none(script):
    row = {
        "pl_name": "X-1 b",
        "hostname": "X-1",
        "default_flag": "0",
        "pl_refname": "<a href=https://ui.adsabs.harvard.edu/abs/2020X>ref</a>",
        "pl_bmasse": "500.0",
        "pl_bmasseerr1": None,
        "pl_bmasseerr2": None,
        "pl_bmasselim": "0",
        "pl_rade": None,
        "pl_radeerr1": None,
        "pl_radeerr2": None,
        "pl_radelim": "0",
        "pl_orbper": None,
        "pl_orbpererr1": None,
        "pl_orbpererr2": None,
        "pl_orbperlim": "0",
    }
    solutions = script.parse_catalog_row(row)
    mass_solutions = [s for s in solutions if s.measurement is not None]

    assert mass_solutions == []


# ---------------------------------------------------------------------------
# 11. Parseo de los CSV grabados de verdad (vía ArchiveClient.query_csv)
# ---------------------------------------------------------------------------


def _mock_client(script, fixture_name: str, *, content_type: str = "text/csv"):
    content = _load_fixture_bytes(fixture_name)

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": content_type})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    return script.ArchiveClient(http, max_requests=5, spacing_s=0.0, sleep=lambda s: None)


def test_query_csv_wasp12_columnas_vacias_son_none_y_refname_es_html_ads(script):
    client = _mock_client(script, "ps_wasp12_masses_radii_period.csv")
    rows = client.query_csv("select ... from ps where hostname='WASP-12'")

    assert len(rows) == 5
    assert set(rows[0].keys()) == {
        "pl_name",
        "hostname",
        "default_flag",
        "pl_refname",
        "pl_bmasse",
        "pl_bmasseerr1",
        "pl_bmasseerr2",
        "pl_bmasselim",
        "pl_rade",
        "pl_radeerr1",
        "pl_radeerr2",
        "pl_radelim",
        "pl_orbper",
        "pl_orbpererr1",
        "pl_orbpererr2",
        "pl_orbperlim",
    }
    knutson = rows[0]
    assert knutson["pl_rade"] is None
    assert knutson["pl_radeerr1"] is None
    assert "ui.adsabs.harvard.edu" in knutson["pl_refname"]
    assert "arXiv" not in knutson["pl_refname"]


def test_parse_catalog_row_sobre_filas_reales_de_wasp12(script):
    client = _mock_client(script, "ps_wasp12_masses_radii_period.csv")
    rows = client.query_csv("select ... from ps where hostname='WASP-12'")

    knutson_solutions = script.parse_catalog_row(rows[0])
    knutson_valid = {s.measurement.parameter for s in knutson_solutions if s.measurement}
    assert knutson_valid == {"mass_earth"}

    hebb_solutions = script.parse_catalog_row(rows[1])
    hebb_valid = {s.measurement.parameter for s in hebb_solutions if s.measurement}
    assert hebb_valid == {"mass_earth", "radius_earth", "period_days"}
    hebb_mass = next(
        s.measurement
        for s in hebb_solutions
        if s.measurement and s.measurement.parameter == "mass_earth"
    )
    assert hebb_mass.err_plus == pytest.approx(32.0)
    assert hebb_mass.err_minus == pytest.approx(312.0)

    sing_solutions = script.parse_catalog_row(rows[2])
    sing_valid = {s.measurement.parameter for s in sing_solutions if s.measurement}
    assert sing_valid == set(), (
        "Sing 2016 no trae ningún error: las tres medidas deben quedar en None"
    )


def test_query_csv_sample30_ids_columnas_esperadas(script):
    client = _mock_client(script, "pscomppars_ids_sample30.csv")
    rows = client.query_csv(
        "select pl_name,hostname,hd_name,hip_name,tic_id,sy_pnum from pscomppars"
    )

    assert len(rows) == 30
    assert set(rows[0].keys()) == {
        "pl_name",
        "hostname",
        "hd_name",
        "hip_name",
        "tic_id",
        "sy_pnum",
    }
    assert rows[1]["hd_name"] is None
    assert rows[0]["tic_id"] == "TIC 281461362"


# ---------------------------------------------------------------------------
# 12. ArchiveClient: espaciado, max_requests, estado != 200, VOTable de
#     error con HTTP 200, tope de bytes.
# ---------------------------------------------------------------------------


def test_archive_client_espacia_con_sleep_inyectado_sin_esperar_de_verdad(script):
    content = _load_fixture_bytes("ps_count_arxiv_refname.csv")
    calls: list[float] = []

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": "text/csv"})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    client = script.ArchiveClient(http, max_requests=5, spacing_s=2.5, sleep=calls.append)

    client.query_csv("select count(*) from ps")
    client.query_csv("select count(*) from ps")
    client.query_csv("select count(*) from ps")

    assert calls == [2.5, 2.5], (
        "la primera petición no debe esperar; cada una después de la primera sí"
    )


def test_archive_client_supera_max_requests_lanza_excepcion(script):
    content = _load_fixture_bytes("ps_count_arxiv_refname.csv")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": "text/csv"})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    client = script.ArchiveClient(http, max_requests=2, spacing_s=0.0, sleep=lambda s: None)

    client.query_csv("select 1")
    client.query_csv("select 1")
    with pytest.raises(Exception, match="max_requests|límite|cortesía|peticion"):
        client.query_csv("select 1")


def test_archive_client_estado_distinto_de_200_lanza_excepcion_clara(script):
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, content=b"service unavailable")

    http = httpx.Client(transport=httpx.MockTransport(handle))
    client = script.ArchiveClient(http, max_requests=5, spacing_s=0.0, sleep=lambda s: None)

    with pytest.raises(Exception, match="503"):
        client.query_csv("select 1")


def test_archive_client_votable_de_error_con_http_200_lanza_excepcion_clara(script):
    """`pscomppars_error_gaia_id.xml`: HTTP 200 pero el cuerpo es un
    VOTABLE de error (`QUERY_STATUS=ERROR`, columna inexistente). No debe
    parsearse como si fuera CSV -- eso produciría filas basura en silencio."""
    content = _load_fixture_bytes("pscomppars_error_gaia_id.xml")

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=content, headers={"content-type": "text/csv"})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    client = script.ArchiveClient(http, max_requests=5, spacing_s=0.0, sleep=lambda s: None)

    with pytest.raises(Exception, match="ORA-00904|QUERY_STATUS|VOTABLE"):
        client.query_csv("select gaia_id from pscomppars")


def test_archive_client_tope_de_bytes(script):
    huge_csv = b"a,b\n" + b"1,2\n" * 10_000_000

    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=huge_csv, headers={"content-type": "text/csv"})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    client = script.ArchiveClient(http, max_requests=5, spacing_s=0.0, sleep=lambda s: None)

    with pytest.raises(Exception, match="bytes|tamaño|grande|MAX"):
        client.query_csv("select * from ps")


# ---------------------------------------------------------------------------
# 13. build_report: recuentos, tramos de sigma, casos por noche
# ---------------------------------------------------------------------------


def _case(script, **overrides):
    defaults = dict(
        external_id="2409.00001",
        raw_name="WASP-12 b",
        match=script.MatchResult(kind="planet", pl_name="WASP-12 b"),
        prior_count=2,
        via_a=None,
        via_b=None,
        sigma=None,
        lost_reason=None,
    )
    defaults.update(overrides)
    return script.Case(**defaults)


def test_build_report_recuentos_basicos_por_via(script):
    via_a_only = _case(
        script,
        external_id="2409.00001",
        via_a=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        via_b=None,
        sigma=1.5,
    )
    via_b_only = _case(
        script,
        external_id="2409.00002",
        via_a=None,
        via_b=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        sigma=0.5,
    )
    via_both = _case(
        script,
        external_id="2409.00003",
        via_a=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        via_b=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        sigma=2.5,
    )
    sin_objects = _case(
        script,
        external_id="2409.00004",
        raw_name=None,
        match=None,
        prior_count=0,
        lost_reason="sin_objects",
    )
    no_emparejado = _case(
        script,
        external_id="2409.00005",
        raw_name="Objeto desconocido",
        match=script.MatchResult(kind="unmatched", pl_name=None),
        prior_count=0,
        lost_reason="no_match",
    )
    ambiguo = _case(
        script,
        external_id="2409.00006",
        raw_name="K2-43",
        match=script.MatchResult(
            kind="host_multi", pl_name=None, candidates=("K2-43 b", "K2-43 c")
        ),
        prior_count=0,
        lost_reason="ambiguo",
    )

    report = script.build_report(
        [via_a_only, via_b_only, via_both, sin_objects, no_emparejado, ambiguo], nights=3
    )

    assert report.items_ep == 6
    assert report.items_with_objects == 5
    assert report.via_a_count == 2
    assert report.via_b_count == 2
    assert report.via_both_count == 1
    assert report.lost_by_reason["sin_objects"] == 1
    assert report.lost_by_reason["no_match"] == 1
    assert report.lost_by_reason["ambiguo"] == 1


def test_build_report_tramos_de_sigma(script):
    sigmas = [0.5, 1.5, 2.5, 4.0, 6.0]
    cases = [
        _case(
            script,
            external_id=f"2409.0000{i}",
            via_a=script.Measurement(parameter="mass_earth", value=1, err_plus=1, err_minus=1),
            sigma=s,
        )
        for i, s in enumerate(sigmas)
    ]

    report = script.build_report(cases, nights=1)

    assert report.sigma_bins["<1"] == 1
    assert report.sigma_bins["1-2"] == 1
    assert report.sigma_bins["2-3"] == 1
    assert report.sigma_bins["3-5"] == 1
    assert report.sigma_bins[">=5"] == 1


def test_build_report_casos_por_noche(script):
    cases = [
        _case(
            script,
            external_id=f"2409.0000{i}",
            via_a=script.Measurement(parameter="mass_earth", value=1, err_plus=1, err_minus=1),
            sigma=1.0,
        )
        for i in range(4)
    ]

    report = script.build_report(cases, nights=2)

    assert report.cases_per_night == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# 14 (render_text): smoke -- no llama a Claude, es texto plano determinista.
# ---------------------------------------------------------------------------


def test_render_text_es_una_cadena_no_vacia_y_no_llama_a_nada_externo(script):
    report = script.build_report(
        [
            _case(
                script,
                via_a=script.Measurement(parameter="mass_earth", value=1, err_plus=1, err_minus=1),
                sigma=1.5,
            )
        ],
        nights=1,
    )

    text = script.render_text(report)

    assert isinstance(text, str)
    assert text.strip() != ""


# ---------------------------------------------------------------------------
# 15. Servicio de alias (`ArchiveClient.lookup_alias`), cableado en
# `run_experiment`: solo se consulta para nombres sin emparejar localmente
# que contienen al menos un dígito, respetando el subtope de peticiones.
#
# Decisiones de forma no fijadas por el plan (T71 solo dice "servicio de
# alias... solo para nombres que no emparejaron localmente y contienen al
# menos un dígito"):
#
# - Cuando `run_experiment` agota el subtope de `_ALIAS_SUBCAP` (40)
#   peticiones de alias, los nombres restantes que hubieran calificado para
#   alias (sin emparejar, con dígito) se cuentan en
#   `Report.lost_by_reason["tope_alias"]`, un `lost_reason` nuevo (no
#   reutiliza `"no_match"`: ese caso sí llegó a intentar el archivo, este ni
#   siquiera pide alias). Si `backend` implementa esto con otra clave, este
#   test debe actualizarse a la clave real, no relajarse a un `>= 1`.
# - Un nombre resuelto por alias que apunta a un único planeta (kind
#   `"planet"` cuando el propio nombre es un alias de planeta, `"host_
#   single"` cuando es un alias de sistema con un solo planeta) debe
#   terminar contribuyendo a `Report.match_by_kind` con esa clave, igual
#   que un emparejamiento por TAP.
# ---------------------------------------------------------------------------


def _index_csv(rows: list[tuple[str, str, str, str, str, str]] = ()) -> bytes:
    header = "pl_name,hostname,hd_name,hip_name,tic_id,sy_pnum\n"
    body = "".join(",".join(row) + "\n" for row in rows)
    return (header + body).encode()


_EMPTY_PS_CSV = (
    b"pl_name,hostname,default_flag,pl_refname,pl_bmasse,pl_bmasseerr1,pl_bmasseerr2,"
    b"pl_bmasselim,pl_rade,pl_radeerr1,pl_radeerr2,pl_radelim,pl_orbper,pl_orbpererr1,"
    b"pl_orbpererr2,pl_orbperlim\n"
)


def _reading(script, *, objects: tuple[str, ...]):
    return script.EpReading(
        external_id="2409.00001",
        title="A transit paper",
        abstract="An abstract without any embedded measurement.",
        objects=objects,
        claims=(),
        run_ids=frozenset({uuid4()}),
    )


def _alias_archive_client(script, *, index_csv: bytes, alias_response: bytes, max_requests: int):
    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "aliaslookup" in url:
            return httpx.Response(
                200, content=alias_response, headers={"content-type": "application/json"}
            )
        query = request.url.params.get("query", "")
        content = index_csv if "pscomppars" in query else _EMPTY_PS_CSV
        return httpx.Response(200, content=content, headers={"content-type": "text/csv"})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    return script.ArchiveClient(
        http, max_requests=max_requests, spacing_s=0.0, sleep=lambda s: None
    )


def test_run_experiment_llama_a_lookup_alias_solo_para_no_emparejados_con_digito(script):
    # "WASP-12 b" empareja por TAP (no debe llamar a alias). "cool dwarfs"
    # no empareja y no tiene dígitos (no debe llamar a alias). "TIC 999999"
    # no empareja y tiene dígitos (debe llamar a alias exactamente una vez).
    index_csv = _index_csv([("WASP-12 b", "WASP-12", "", "", "", "1")])
    alias_response = _load_fixture_bytes("aliaslookup_wasp12.json")
    client = _alias_archive_client(
        script, index_csv=index_csv, alias_response=alias_response, max_requests=10
    )
    client.lookup_alias = MagicMock(wraps=client.lookup_alias)

    reading = _reading(script, objects=("WASP-12 b", "cool dwarfs", "TIC 999999"))

    script.run_experiment([reading], client)

    assert client.lookup_alias.call_args_list == [call("TIC 999999")], (
        "lookup_alias debe llamarse solo para 'TIC 999999' (no emparejado localmente, "
        "con dígito); ni para 'WASP-12 b' (ya emparejado) ni para 'cool dwarfs' "
        "(sin dígitos)."
    )


def test_run_experiment_respeta_subtope_de_40_peticiones_de_alias(script):
    index_csv = _index_csv([])  # nada empareja por TAP
    alias_not_found = json.dumps(
        {
            "manifest": {
                "requested_name": "x",
                "resolved_name": None,
                "lookup_status": "SYSTEM NOT FOUND",
            }
        }
    ).encode()
    names = tuple(f"Unmatched TIC{i}" for i in range(41))
    client = _alias_archive_client(
        script, index_csv=index_csv, alias_response=alias_not_found, max_requests=100
    )
    client.lookup_alias = MagicMock(wraps=client.lookup_alias)

    reading = _reading(script, objects=names)

    report = script.run_experiment([reading], client)

    assert client.lookup_alias.call_count == 40, (
        "con 41 nombres candidatos a alias, solo deben salir 40 peticiones "
        "(subtope ArchiveClient._ALIAS_SUBCAP)."
    )
    assert report.lost_by_reason.get("tope_alias") == 1, (
        "el nombre 41 debe contarse como 'sin emparejar (tope de alias)', sin "
        "intentar una petición número 41."
    )


def test_run_experiment_resuelve_nombre_por_alias_como_planeta_o_anfitriona(script):
    # Fixture aliaslookup_wasp12.json: "TIC 86396382 b" es un alias del
    # PLANETA WASP-12 b; "TIC 86396382" es un alias del sistema/anfitriona
    # WASP-12, que tiene un único planeta (host_single). Ninguno de los dos
    # aparece en el índice local (tic_id vacío), así que ambos deben pasar
    # por alias.
    index_csv = _index_csv([("WASP-12 b", "WASP-12", "", "", "", "1")])
    alias_response = _load_fixture_bytes("aliaslookup_wasp12.json")
    client = _alias_archive_client(
        script, index_csv=index_csv, alias_response=alias_response, max_requests=10
    )

    reading = _reading(script, objects=("TIC 86396382 b", "TIC 86396382"))

    report = script.run_experiment([reading], client)

    assert report.match_by_kind.get("planet") == 1, (
        "'TIC 86396382 b' es un alias de planeta en el fixture: debe resolver kind='planet'"
    )
    assert report.match_by_kind.get("host_single") == 1, (
        "'TIC 86396382' es un alias de sistema con un solo planeta: debe resolver "
        "kind='host_single'"
    )


# ---------------------------------------------------------------------------
# 16. Report desglosa los tramos sigma frente a la solución `default_flag`
# (`Case.sigma_default` / `Report.sigma_default_bins`), además del máximo
# frente a todas las previas (`Report.sigma_bins`), y por vía
# (`sigma_bins_via_a`/`sigma_bins_via_b`). Ver la sección de regresión más
# abajo para el caso -- ya detectado por el reviewer -- en que `via_a` y
# `via_b` coexisten con valores distintos: `sigma_bins_via_b` no debe
# heredar el sigma calculado con `via_a`.
# ---------------------------------------------------------------------------


def test_build_report_desglosa_tramos_sigma_frente_a_default_flag(script):
    caso_via_a = _case(
        script,
        external_id="2409.00010",
        via_a=script.Measurement(parameter="mass_earth", value=1, err_plus=1, err_minus=1),
        via_b=None,
        sigma=4.5,
        sigma_default=1.2,
    )
    caso_via_b = _case(
        script,
        external_id="2409.00011",
        via_a=None,
        via_b=script.Measurement(parameter="mass_earth", value=1, err_plus=1, err_minus=1),
        sigma=0.5,
        sigma_default=0.5,
    )

    report = script.build_report([caso_via_a, caso_via_b], nights=1)

    assert report.sigma_bins["3-5"] == 1
    assert report.sigma_bins["<1"] == 1
    assert report.sigma_default_bins["1-2"] == 1
    assert report.sigma_default_bins["<1"] == 1
    assert report.sigma_bins_via_a["3-5"] == 1
    assert report.sigma_bins_via_b["<1"] == 1


# ---------------------------------------------------------------------------
# 17. render_text avisa de que la fórmula de sigma es provisional y rotula
# la referencia de tokens como "orden de magnitud" (no una decisión de
# presupuesto).
# ---------------------------------------------------------------------------


def test_render_text_incluye_aviso_provisional_y_tokens_orden_de_magnitud(script):
    report = script.build_report(
        [
            _case(
                script,
                via_a=script.Measurement(parameter="mass_earth", value=1, err_plus=1, err_minus=1),
                sigma=1.5,
            )
        ],
        nights=1,
    )

    text = script.render_text(report)

    assert "PROVISIONAL" in text
    assert "orden de magnitud" in text


# ---------------------------------------------------------------------------
# 18 (T71, corrección tras revisión rechazada): regresiones del reviewer.
#
# Cada test de esta sección reproduce, con el literal exacto que dio el
# reviewer, un fallo concreto de la primera versión de
# `parse_measurements`/`sigma`/`parse_catalog_row`/`own_solutions`/
# `build_report`/`run_experiment`. Los bugs ya están corregidos: esta
# sección pasa en verde y se queda para que ninguno reaparezca. Ninguno se
# relaja para pasar y el script no se toca desde este fichero.
# ---------------------------------------------------------------------------


def _archive_client_for_ps(script, *, index_csv: bytes, ps_csv: bytes, max_requests: int = 20):
    """Como `_mock_client`, pero con un CSV distinto para `pscomppars` (el
    índice) y para `ps` (soluciones), según qué tabla nombra el ADQL."""

    def handle(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("query", "")
        content = index_csv if "pscomppars" in query else ps_csv
        return httpx.Response(200, content=content, headers={"content-type": "text/csv"})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    return script.ArchiveClient(
        http, max_requests=max_requests, spacing_s=0.0, sleep=lambda s: None
    )


# --- B1: puntuación final pegada al valor de error --------------------------


def test_puntuacion_final_no_lanza_excepcion_y_parsea_el_valor(script):
    """Una coma de lista o un punto final pegados al número de error no
    deben romper `_to_float` con un `ValueError`: en la primera versión el
    punto/coma se colaba dentro del grupo `\\d[\\d.,]*` y `float("0.01.")`
    explotaba. `_NUM = r"\\d+(?:[.,]\\d+)?"` ya lo evita. Los tres casos
    fijan el contrato para que el bug no reaparezca."""
    caso_1 = script.parse_measurements("a mass of 1.2 ± 0.3 M_J.")
    assert len(caso_1) == 1
    assert caso_1[0].parameter == "mass_earth"
    assert caso_1[0].value == pytest.approx(1.2 * M_JUP_IN_M_EARTH, rel=1e-6)

    caso_2 = script.parse_measurements("1.2 ± 0.3 M_J, consistent with")
    assert len(caso_2) == 1
    assert caso_2[0].parameter == "mass_earth"

    caso_3 = script.parse_measurements("Rp/R* = 0.1 ± 0.01, R_p = 2.0 ± 0.1 R_J")
    assert len(caso_3) == 1, (
        "0.1 +/- 0.01 es un cociente Rp/R*, no una medida de masa/radio/periodo: solo "
        "debe reconocerse el radio 2.0 +/- 0.1 R_J"
    )
    (medida,) = caso_3
    assert medida.parameter == "radius_earth"
    assert medida.value == pytest.approx(2.0 * R_JUP_IN_R_EARTH, rel=1e-6)


# --- B2: la unidad debe anclarse al valor que le corresponde ---------------


def test_unidad_no_se_contamina_desde_otro_valor_del_mismo_texto(script):
    """La ventana de búsqueda de unidad simbólica de la primera versión
    llegaba hasta el siguiente valor con error (o, si no había otro, hasta
    el final del texto), así que la unidad de UN valor podía "verse" en la
    unidad de OTRO valor lejano en el mismo texto. Cada caso de abajo tiene
    un valor sin unidad propia cerca (temperatura, masa estelar, periodo de
    rotación, "Jupiter analog") que no debe heredar la unidad de un valor
    vecino. La sección 19 (más abajo) endurece esto todavía más: ni
    siquiera vale una ventana acotada por el siguiente valor, solo cuenta
    la unidad inmediatamente pegada al error (o propagada por una lista de
    separadores triviales, ver esa sección)."""
    resultado_1 = script.parse_measurements(r"Teff = 5000 \pm 100 K, mass 1.2 \pm 0.1 M_J")
    assert len(resultado_1) == 1, (
        f"5000 K no es una masa: solo 1.2 M_J debe reconocerse, se obtuvo {resultado_1}"
    )
    assert resultado_1[0].value == pytest.approx(1.2 * M_JUP_IN_M_EARTH, rel=1e-6)

    resultado_2 = script.parse_measurements(
        r"$M_\star = 1.1 \pm 0.1 M_\odot$ and planet $1.3 \pm 0.2 M_J$"
    )
    assert len(resultado_2) == 1, (
        f"1.1 M_sol (la estrella) no es la masa del planeta: solo 1.3 M_J debe "
        f"reconocerse, se obtuvo {resultado_2}"
    )
    assert resultado_2[0].value == pytest.approx(1.3 * M_JUP_IN_M_EARTH, rel=1e-6)

    resultado_3 = script.parse_measurements(
        r"mass $5.02 \pm 0.75 M_{\rm Jup}$ and radius $1.3 \pm 0.1 R_{\rm Jup}$"
    )
    assert {m.parameter for m in resultado_3} == {"mass_earth", "radius_earth"}, (
        "cada valor debe clasificarse con SU PROPIA unidad, no con la del otro: "
        f"se obtuvo {[(m.parameter, m.value) for m in resultado_3]}"
    )
    masa = next(m for m in resultado_3 if m.parameter == "mass_earth")
    radio = next(m for m in resultado_3 if m.parameter == "radius_earth")
    assert masa.value == pytest.approx(5.02 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert radio.value == pytest.approx(1.3 * R_JUP_IN_R_EARTH, rel=1e-6)

    assert script.parse_measurements(r"eccentricity 0.05 \pm 0.01 for Jupiter analog") == [], (
        "'Jupiter analog' no es una unidad de masa/radio de Júpiter"
    )

    assert (
        script.parse_measurements("orbital period of 3.5 d and rotation period 12.1 ± 0.3 days")
        == []
    ), (
        "3.5 no tiene error explícito y 12.1 es un periodo de ROTACIÓN (de la "
        "estrella), no orbital; el 'orbital' de la frase anterior no debe colarse "
        "en la clasificación del segundo valor"
    )


# --- B3: factores de escala explícitos deben descartar el valor ------------


def test_factores_de_escala_se_descartan(script):
    assert script.parse_measurements(r"$(1.2 \pm 0.3) \times 10^{-3} M_{\rm Jup}$") == [], (
        "el valor va multiplicado por un factor de escala explícito: no es la masa real"
    )

    assert (
        script.parse_measurements(r"2.13^{+1.18}_{-0.61} \times10^7 and a mass of 1.2 M_{\rm Jup}")
        == []
    ), (
        "2.13e7 no es ninguna medida de masa/radio/periodo (factor de escala); 1.2 "
        "M_Jup no tiene error explícito -- el texto completo no debe producir "
        "ninguna Measurement"
    )

    # Se comporta igual que los dos casos de arriba: se deja aquí para
    # fijar el contrato junto a ellos.
    assert script.parse_measurements(r"2.13$^{+1.18}_{-0.61} \times10^7$") == []


# --- Notaciones de unidad menos frecuentes, ya reconocidas -----------------


def test_falsos_negativos_de_unidad_y_notacion(script):
    resultado_masa_tierra = script.parse_measurements(r"$8.7 \pm 1.1\,M_\oplus$")
    assert len(resultado_masa_tierra) == 1, r"M_\oplus (masa terrestre) no se reconoce"
    assert resultado_masa_tierra[0].parameter == "mass_earth"
    assert resultado_masa_tierra[0].value == pytest.approx(8.7)
    assert resultado_masa_tierra[0].err_plus == pytest.approx(1.1)

    resultado_radio_tierra = script.parse_measurements(r"$2.1 \pm 0.1\,R_\oplus$")
    assert len(resultado_radio_tierra) == 1, r"R_\oplus (radio terrestre) no se reconoce"
    assert resultado_radio_tierra[0].parameter == "radius_earth"
    assert resultado_radio_tierra[0].value == pytest.approx(2.1)

    resultado_mp = script.parse_measurements(r"M_p = 3.1 \pm 0.2 M_{\rm J}")
    assert len(resultado_mp) == 1, r"M_{\rm J} (con \rm antes de J, no 'Jup') no se reconoce"
    assert resultado_mp[0].parameter == "mass_earth"
    assert resultado_mp[0].value == pytest.approx(3.1 * M_JUP_IN_M_EARTH, rel=1e-6)

    resultado_dollar = script.parse_measurements(r"2.13$^{+0.2}_{-0.1}$ M_J")
    assert len(resultado_dollar) == 1, (
        "un '$' entre el valor y '^' (patrón asimétrico) no debe impedir el match"
    )
    assert resultado_dollar[0].parameter == "mass_earth"
    assert resultado_dollar[0].value == pytest.approx(2.13 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert resultado_dollar[0].err_plus == pytest.approx(0.2 * M_JUP_IN_M_EARTH, rel=1e-6)
    assert resultado_dollar[0].err_minus == pytest.approx(0.1 * M_JUP_IN_M_EARTH, rel=1e-6)


# --- Rangos/desigualdades: nunca son una medida puntual --------------------


@pytest.mark.parametrize(
    "text",
    [
        r"the mass is < 1.2 \pm 0.3 M_J",
        r"the mass is > 1.2 \pm 0.3 M_J",
        r"the mass is \lesssim 1.2 \pm 0.3 M_J",
        r"the mass is \gtrsim 1.2 \pm 0.3 M_J",
        r"1.2 \pm 0.3 M_J < upper bound",
        r"$54\pm3~{\rm M_\oplus} <M< 258\pm11~{\rm M_\oplus}$",
    ],
)
def test_valores_en_desigualdad_se_descartan(script, text):
    assert script.parse_measurements(text) == [], (
        f"un valor precedido o seguido de <, >, \\lesssim o \\gtrsim es un límite, no "
        f"una medida puntual: {text!r}"
    )


# --- Vía (a): varios valores del mismo parámetro en el mismo texto --------


def test_run_experiment_descarta_via_a_con_valores_multiples_del_mismo_parametro(script):
    """Cuatro valores de masa en el mismo texto (variantes de una misma
    tabla de resultados, p. ej.): la vía (a) no puede saber cuál de los
    cuatro es "el" valor del papel para ese parámetro, así que debe
    descartarse -- `lost_reason="valores_multiples"` -- en vez de quedarse
    con el primero encontrado en silencio."""
    index_csv = _index_csv([("WASP-1 b", "WASP-1", "", "", "", "1")])
    ps_csv = _EMPTY_PS_CSV + b"WASP-1 b,WASP-1,1,prior ref,90,5,5,0,,,,0,,,,0\n"
    client = _archive_client_for_ps(script, index_csv=index_csv, ps_csv=ps_csv)

    abstract = (
        r"the derived masses are $0.52^{+0.12}{-0.14}$, $0.65^{+0.21}{-0.18}$, "
        r"$0.67\pm0.16$, and $0.78^{+0.19}{-0.20}$ $M_{\rm Jup}$"
    )
    reading = script.EpReading(
        external_id="2409.00002",
        title="t",
        abstract=abstract,
        objects=("WASP-1 b",),
        claims=(),
        run_ids=frozenset({uuid4()}),
    )

    report = script.run_experiment([reading], client)

    assert report.lost_by_reason.get("valores_multiples") == 1, (
        "con cuatro valores de masa en el mismo texto, la vía (a) debe descartarse "
        f"con lost_reason='valores_multiples'; lost_by_reason obtenido: "
        f"{report.lost_by_reason}"
    )


# --- Error cero: dato inválido, no una incertidumbre real de cero ---------


def test_sigma_con_denominador_combinado_cero_lanza_valueerror(script):
    """Si el lado de cada medida que mira hacia la otra (el que usa `sigma`)
    tiene error 0 en las dos, y los valores son distintos, el denominador
    combinado es 0: no hay manera de expresar esa distancia en unidades de
    error que no existen. `sigma` debe fallar con `ValueError` en vez de
    devolver un número: devolver la distancia sin dividir (`return
    distance`) disfrazaría un dato inválido del catálogo como si fuera una
    medida de tensión más. (Nótese
    que esto NO afecta al caso `paper.value == prior.value`, que sigue
    devolviendo `0.0` sin mirar los errores: eso no cambia con este test)."""
    paper = script.Measurement(parameter="mass_earth", value=10.0, err_plus=1.0, err_minus=0.0)
    prior = script.Measurement(parameter="mass_earth", value=8.0, err_plus=0.0, err_minus=1.0)

    with pytest.raises(ValueError):
        script.sigma(paper, prior)


def test_parse_measurements_error_cero_no_produce_measurement(script):
    """Un `\\pm 0` es un error explícito pero nulo: no aporta incertidumbre
    real y debe tratarse como si no hubiera error (igual que un valor sin
    `\\pm`), para no producir después una `Measurement` con error cero que
    haga explotar `sigma`."""
    assert script.parse_measurements(r"$1.2 \pm 0 M_J$") == []


def test_parse_catalog_row_error_cero_da_measurement_none(script):
    row = {
        "pl_name": "X-1 b",
        "hostname": "X-1",
        "default_flag": "0",
        "pl_refname": "ref",
        "pl_bmasse": "500.0",
        "pl_bmasseerr1": "0",
        "pl_bmasseerr2": "0",
        "pl_bmasselim": "0",
        "pl_rade": None,
        "pl_radeerr1": None,
        "pl_radeerr2": None,
        "pl_radelim": "0",
        "pl_orbper": None,
        "pl_orbpererr1": None,
        "pl_orbpererr2": None,
        "pl_orbperlim": "0",
    }
    solutions = script.parse_catalog_row(row)
    mass_solutions = [s for s in solutions if s.measurement is not None]

    assert mass_solutions == [], (
        "err1=err2='0' es una medida sin incertidumbre real: measurement debe salir "
        f"None, igual que si faltara el error; se obtuvo {mass_solutions}"
    )


# --- Vía (b): otras notaciones del id de arXiv en pl_refname --------------


@pytest.mark.parametrize(
    "refname",
    [
        "<a href=https://arxiv.org/abs/2409.12345>Autor et al. 2024</a>",
        "arXiv:2409.12345",
    ],
)
def test_own_solutions_reconoce_otras_notaciones_del_id_de_arxiv(script, refname):
    own = script.CatalogSolution(
        pl_name="WASP-1 b", refname=refname, default_flag=False, measurement=None
    )

    result = script.own_solutions([own], "2409.12345")

    assert result == [own], (
        f"own_solutions debe reconocer {refname!r} como la solución propia del paper "
        "2409.12345, no solo el fragmento exacto 'arXiv240912345'"
    )


# --- Informe: recuentos independientes de si hay valor del papel ---------


def test_planets_with_ge2_priors_cuenta_aunque_no_haya_valor_del_papel(script):
    """Un planeta con 3 soluciones previas válidas en el catálogo pero SIN
    valor del papel (ni vía a ni vía b) debe seguir contando en
    `planets_with_ge2_priors`: el recuento es sobre lo que ya sabe el
    catálogo, no sobre si este paper aporta un valor nuevo."""
    index_csv = _index_csv([("WASP-1 b", "WASP-1", "", "", "", "1")])
    ps_csv = (
        _EMPTY_PS_CSV
        + b"WASP-1 b,WASP-1,1,ref A,100,5,5,0,,,,0,,,,0\n"
        + b"WASP-1 b,WASP-1,0,ref B,110,5,5,0,,,,0,,,,0\n"
        + b"WASP-1 b,WASP-1,0,ref C,90,5,5,0,,,,0,,,,0\n"
    )
    client = _archive_client_for_ps(script, index_csv=index_csv, ps_csv=ps_csv)

    reading = script.EpReading(
        external_id="2409.00001",
        title="t",
        abstract="no numeric measurement here",
        objects=("WASP-1 b",),
        claims=(),
        run_ids=frozenset({uuid4()}),
    )

    report = script.run_experiment([reading], client)

    assert report.planets_with_ge2_priors == 1, (
        "WASP-1 b tiene 3 soluciones previas válidas en el catálogo (ninguna vía "
        f"aporta valor del papel): debe contar igual; se obtuvo "
        f"{report.planets_with_ge2_priors}"
    )


def test_sigma_bins_via_b_usa_el_valor_de_la_via_b_no_el_de_la_via_a(script):
    """Cuando un caso tiene las dos vías con valores distintos, `Case.sigma`
    se calcula con el valor de la vía (a), preferida por
    `_pick_parameter`/`_resolve_case` -- pero `sigma_bins_via_a` y
    `sigma_bins_via_b` deben reflejar, cada uno, la discrepancia calculada
    con SU PROPIO valor: `sigma_bins_via_b` con el de la vía (b) (130 M⊕
    frente a la previa de 90 M⊕, sigma≈5.66, tramo '>=5'), no con el de la
    vía (a) (100 M⊕, sigma≈1.41, tramo '1-2')."""
    index_csv = _index_csv([("WASP-1 b", "WASP-1", "", "", "", "1")])
    ps_csv = (
        _EMPTY_PS_CSV
        + b"WASP-1 b,WASP-1,0,<a href=https://x>own arXiv240912345</a>,130,5,5,0,,,,0,,,,0\n"
        + b"WASP-1 b,WASP-1,1,<a href=https://ui.adsabs.harvard.edu/abs/2009ApJ>p</a>,90,5,5,0,,,,0,,,,0\n"
    )
    client = _archive_client_for_ps(script, index_csv=index_csv, ps_csv=ps_csv)

    reading = script.EpReading(
        external_id="2409.12345",
        title="t",
        abstract="We measure a mass of 100 ± 5 masas terrestres for the planet.",
        objects=("WASP-1 b",),
        claims=(),
        run_ids=frozenset({uuid4()}),
    )

    report = script.run_experiment([reading], client)

    assert report.sigma_bins_via_a["1-2"] == 1, (
        f"vía a (100 vs previa 90, sigma≈1.41) debería caer en '1-2'; se obtuvo "
        f"{report.sigma_bins_via_a}"
    )
    assert report.sigma_bins_via_b[">=5"] == 1, (
        "vía b (130 vs previa 90, sigma≈5.66) debería caer en '>=5', calculada con "
        f"SU PROPIO valor (no con el de la vía a); se obtuvo {report.sigma_bins_via_b}"
    )


# ---------------------------------------------------------------------------
# 19 (T71, tercera iteración, segundo rechazo): parser ESTRICTO de unidades.
#
# Nuevo contrato de `parse_measurements`, más estricto que el de las
# secciones 1-18: una unidad solo cuenta si va INMEDIATAMENTE después del
# error, sin ventanas ni búsqueda hasta el siguiente valor. "Inmediatamente"
# admite entre el error y la unidad, en cualquier orden/repetición, solo:
# espacios, `$`, `~`, `\,`, `\;`, `)`, y los envoltorios que la propia
# unidad ya consume (`{\rm `, `\mathrm{`, `\text{`, `{`, `}`). Cualquier
# otro carácter en medio (una palabra, una coma suelta, un punto) descarta
# la unidad para ese valor.
#
# Excepción: en una LISTA de valores ($a$, $b$, y $c$ UNIDAD), la unidad
# pegada al ÚLTIMO elemento se propaga hacia atrás a los anteriores, pero
# solo si todo lo que separa cada par de valores consecutivos es un
# separador de lista trivial: coma, "and", "y", espacios, `$` (ninguna otra
# palabra ni signo en medio). Esta regla se aplica por igual a las
# unidades simbólicas de Júpiter/Tierra y a la frase de texto "Earth
# masses"/"masas terrestres": TODA unidad respeta la misma regla, ni más
# ni menos.
#
# Cada sub-sección documenta, con el literal probado, el comportamiento
# exigido por el contrato (se deja fijado igual que los casos de la
# sección 18).
# ---------------------------------------------------------------------------

# --- 19.1: unidad lejana en el mismo texto -> NO cuenta (salvo lista) ------


_UNIDAD_NO_INMEDIATA_CASES = [
    pytest.param(
        r"Teff = 960 \pm 30 K and the planet radius is 1.2 R_J.",
        [],
        id="teff_960_no_es_radio_1.2_sin_error_propio",
    ),
    pytest.param(
        r"An orbital period of 4.2 \pm 0.1 days and a radius of 1.3 R_J",
        [("period_days", 4.2)],
        id="periodo_4.2_dias_inmediato_radio_1.3_sin_error",
    ),
    pytest.param(
        r"We measure an eccentricity of 0.3 \pm 0.1; a companion of 12 M_J is excluded.",
        [],
        id="excentricidad_0.3_no_es_masa_del_companero_12",
    ),
    pytest.param(
        r"metallicity of 0.21 \pm 0.05 dex. The planet, with a radius of about 2 R_\oplus",
        [],
        id="metalicidad_0.21_no_es_radio_2",
    ),
    pytest.param(
        r"transit depth 0.8 \pm 0.1 per cent for a 2 R_\oplus planet",
        [],
        id="profundidad_transito_0.8_no_es_radio_2",
    ),
    pytest.param(
        r"Teff = 5200 \pm 80 K. We derive a mass below 30 earth masses.",
        [],
        id="teff_5200_no_es_masa_30_limite_superior",
    ),
    pytest.param(
        r"T_eq = 900 \pm 50 K, with an upper limit of 20 Earth masses",
        [],
        id="teq_900_no_es_masa_20_limite_superior",
    ),
    pytest.param(
        r"P = 3.2 \pm 0.1 d and a minimum mass of about 5 Earth masses",
        [("period_days", 3.2)],
        id="periodo_3.2_dia_de_una_letra_no_es_masa_5_minima",
    ),
    pytest.param(
        r"stellar age of 2.1^{+0.3}_{-0.2} Gyr and a mass of 4 Earth masses",
        [],
        id="edad_estelar_2.1_no_es_masa_4_sin_error",
    ),
    pytest.param(
        r"semi-amplitude of 12.3 \pm 0.4 m s$^{-1}$, implying 25 \pm 2 Earth masses",
        [("mass_earth", 25.0)],
        id="semiamplitud_12.3_no_es_masa_solo_25_lo_es",
    ),
]


@pytest.mark.parametrize("text, expected", _UNIDAD_NO_INMEDIATA_CASES)
def test_unidad_no_cuenta_si_no_va_inmediatamente_tras_el_error(script, text, expected):
    """Ninguno de estos textos tiene, entre el error del valor "señuelo" y
    la unidad real que aparece más adelante en la frase, solo separadores
    triviales: hay palabras de por medio (K, dex, per cent, Gyr, m s...) o
    puntuación no admitida (`;`, `.`). Bajo el contrato estricto, la unidad
    NO debe encontrarse para ese valor: ni cuando no hay otro valor con
    error detrás, ni para la frase textual "Earth masses", la búsqueda de
    unidad puede extenderse más allá del separador trivial inmediato."""
    resultado = script.parse_measurements(text)
    obtenido = [(m.parameter, m.value) for m in resultado]

    if not expected:
        assert obtenido == [], f"se esperaba [] y se obtuvo {obtenido} para {text!r}"
        return

    assert len(obtenido) == len(expected), (
        f"se esperaba {expected}, se obtuvo {obtenido} para {text!r}"
    )
    for (parametro_obtenido, valor_obtenido), (parametro_esperado, valor_esperado) in zip(
        obtenido, expected, strict=True
    ):
        assert parametro_obtenido == parametro_esperado
        assert valor_obtenido == pytest.approx(valor_esperado, rel=1e-6)


# --- 19.2: factores de escala adicionales (\cdot, x, e-N, potencia suelta) -


@pytest.mark.parametrize(
    "text",
    [
        r"1.2 \pm 0.3 \cdot 10^{-3} M_J",
        r"1.2 \pm 0.3 x 10^-3 M_J",
        r"1.2 \pm 0.3 e-3 M_J",
        r"1.2 \pm 0.3 10^{-3} M_J",
    ],
)
def test_factores_de_escala_cdot_x_e_y_potencia_suelta_se_descartan(script, text):
    """Además de `\\times`/`×`, `\\cdot`, la `x` suelta, la notación `e-3`
    y una potencia de diez sin operador explícito entre el error y la
    unidad también indican que el valor va multiplicado por un factor de
    escala y no es la medida real; el script debe descartar los cuatro
    casos siguientes."""
    assert script.parse_measurements(text) == [], (
        f"un factor de escala (\\cdot/x/e-N/potencia suelta) entre el error y la "
        f"unidad debe descartar el valor: {text!r}"
    )


# --- 19.3: desigualdades envueltas en $...$ o en notación unicode ----------


@pytest.mark.parametrize(
    "text",
    [
        r"$\lesssim$ 1.2 \pm 0.1 M_J",
        r"≲ 1.2 \pm 0.1 M_J",
        r"≳ 1.2 \pm 0.1 M_J",
        r"≤ 1.2 \pm 0.1 M_J",
        r"≥ 1.2 \pm 0.1 M_J",
    ],
)
def test_desigualdad_con_dolar_o_unicode_tambien_descarta_el_valor(script, text):
    """Además de `<`, `>`, `\\lesssim`, `\\gtrsim`, `\\la`, `\\ga` en
    ASCII, el `$` que envuelve `\\lesssim` en modo matemático y los
    símbolos unicode ≲/≳/≤/≥ (habituales en abstracts ya compuestos)
    también deben reconocerse como desigualdad; el script debe descartar
    los cinco casos siguientes."""
    assert script.parse_measurements(text) == [], (
        f"una desigualdad envuelta en $...$ o en notación unicode (≲, ≳, ≤, ≥) es un "
        f"límite, no una medida puntual: {text!r}"
    )


# --- 19.4: notaciones de unidad de Júpiter/Tierra que deben reconocerse ---


@pytest.mark.parametrize(
    "text, parameter, value",
    [
        pytest.param(
            r"5.0 \pm 0.5 M_\mathrm{J}", "mass_earth", 5.0 * M_JUP_IN_M_EARTH, id="m_mathrm_j"
        ),
        pytest.param(
            r"5.0 \pm 0.5 M_{\mathrm{Jup}}",
            "mass_earth",
            5.0 * M_JUP_IN_M_EARTH,
            id="m_mathrm_jup_con_llaves",
        ),
        pytest.param(
            r"5.0 \pm 0.5\,M_{\mathrm{J}}",
            "mass_earth",
            5.0 * M_JUP_IN_M_EARTH,
            id="m_mathrm_j_con_espacio_fino",
        ),
        pytest.param(
            r"5.0 \pm 0.5 Jupiter masses",
            "mass_earth",
            5.0 * M_JUP_IN_M_EARTH,
            id="jupiter_masses_en_texto",
        ),
        pytest.param(
            r"1.1 \pm 0.1 Jupiter radii",
            "radius_earth",
            1.1 * R_JUP_IN_R_EARTH,
            id="jupiter_radii_en_texto",
        ),
        pytest.param(
            r"5.0 \pm 0.5 M_{Jupiter}",
            "mass_earth",
            5.0 * M_JUP_IN_M_EARTH,
            id="m_jupiter_palabra_completa",
        ),
    ],
)
def test_notaciones_de_unidad_de_jupiter_se_reconocen(script, text, parameter, value):
    """Estas seis notaciones son reales (vistas en abstracts ya ingeridos) y
    deben reconocerse: la palabra completa `Jupiter` (no solo `J`/`Jup`
    con límite de palabra justo después) y la frase textual "Jupiter
    masses"/"Jupiter radii" también cuentan como unidad válida."""
    resultado = script.parse_measurements(text)
    assert len(resultado) == 1, f"se esperaba una Measurement para {text!r}, se obtuvo {resultado}"
    (medida,) = resultado
    assert medida.parameter == parameter
    assert medida.value == pytest.approx(value, rel=1e-6)


# --- 19.5: periodo con contexto "P =" y unidad de un solo carácter --------


def test_periodo_con_contexto_p_igual_y_unidad_de_un_caracter(script):
    """Además de la palabra completa "day"/"days"/"día"/"días", una "d"
    suelta (abreviatura habitual junto a "P =") debe reconocerse como
    unidad de días, clasificando el valor como `period_days` en vez de
    descartarlo por completo."""
    resultado = script.parse_measurements(r"P = 3.5 \pm 0.1 d")
    assert len(resultado) == 1, f"se esperaba una Measurement, se obtuvo {resultado}"
    (medida,) = resultado
    assert medida.parameter == "period_days"
    assert medida.value == pytest.approx(3.5)


# --- 19.6: listas -- la unidad del último elemento propaga hacia atrás ----


def test_lista_de_cuatro_masas_separadas_por_comas_y_reproduce_las_cuatro(script):
    """`$a$, $b$, $c$, and $d$ $M_{\\rm Jup}$`: entre cada par de valores
    consecutivos solo hay separador de lista trivial (`$, $`, `$, and $`),
    así que la unidad pegada al ÚLTIMO valor se propaga a los tres
    anteriores. Esta propagación entre valores separados por un separador
    de lista trivial se aplica igual a las unidades simbólicas de
    Júpiter/Tierra que a la frase textual "Earth masses" (ver el resto de
    esta sección): se fija aquí el contrato de la sección 19, que exige la
    misma propagación pero NUNCA más allá de una lista de separadores
    triviales."""
    texto = (
        r"the derived masses are $0.52^{+0.12}{-0.14}$, $0.65^{+0.21}{-0.18}$, "
        r"$0.67\pm0.16$, and $0.78^{+0.19}{-0.20}$ $M_{\rm Jup}$"
    )

    resultado = script.parse_measurements(texto)

    assert len(resultado) == 4, f"se esperaban 4 masas, se obtuvo {resultado}"
    assert {m.parameter for m in resultado} == {"mass_earth"}
    valores = sorted(m.value for m in resultado)
    esperados = sorted(v * M_JUP_IN_M_EARTH for v in (0.52, 0.65, 0.67, 0.78))
    for obtenido, esperado in zip(valores, esperados, strict=True):
        assert obtenido == pytest.approx(esperado, rel=1e-6)


def test_run_experiment_lista_con_valores_de_parametros_distintos_es_valores_multiples(script):
    """`Teff = 960 \\pm 30, 1.2 \\pm 0.1 M_J`: el 960 (Teff, no una masa) y
    el 1.2 (sí una masa de Júpiter) están separados solo por `, ` -- un
    separador de lista trivial -- así que la unidad `M_J` pegada al último
    valor se propaga hacia atrás y el 960 también se clasifica como
    `mass_earth`. Con dos valores DISTINTOS del mismo parámetro en el mismo
    texto, la vía (a) debe descartarse por completo (`lost_reason=
    "valores_multiples"`, sin ningún sigma) en vez de quedarse con
    cualquiera de los dos en silencio. La propagación de `M_J` hacia el
    960 y el descarte por valores múltiples ya están cubiertos por la
    sección 18: se deja aquí para fijar el contrato de listas de la
    sección 19 junto a los casos de arriba."""
    index_csv = _index_csv([("WASP-1 b", "WASP-1", "", "", "", "1")])
    ps_csv = _EMPTY_PS_CSV + b"WASP-1 b,WASP-1,1,prior ref,90,5,5,0,,,,0,,,,0\n"
    client = _archive_client_for_ps(script, index_csv=index_csv, ps_csv=ps_csv)

    reading = script.EpReading(
        external_id="2409.00003",
        title="t",
        abstract=r"Teff = 960 \pm 30, 1.2 \pm 0.1 M_J",
        objects=("WASP-1 b",),
        claims=(),
        run_ids=frozenset({uuid4()}),
    )

    report = script.run_experiment([reading], client)

    assert report.lost_by_reason.get("valores_multiples") == 1, (
        f"se esperaba lost_reason='valores_multiples'; lost_by_reason obtenido: "
        f"{report.lost_by_reason}"
    )
    assert report.sigma_bins == script._empty_sigma_bins(), (
        "sin valor de la vía (a) ni de la vía (b) [WASP-1 b no tiene solución propia "
        f"en el catálogo], no debe calcularse ningún sigma; se obtuvo {report.sigma_bins}"
    )


# --- 19.7: `Case.source_snippet`, listado en `render_text`, y en `--json` -
#
# Decisiones de forma no fijadas por el plan de T71 (igual que la nota
# antes de la sección 15):
#
# - `Case` gana un campo opcional `source_snippet: str | None = None`.
#   Solo los casos con `via_a is not None` lo llevan rellenado: el
#   fragmento es del texto que ve la vía (a) (abstract + claims), no algo
#   que se pueda reconstruir para un valor que solo viene del catálogo
#   (vía (b)). Un caso sin vía (a) se queda con `source_snippet=None`.
# - `render_text` pasa a aceptar la lista de `Case` como segundo argumento
#   posicional (`render_text(report, cases)`) y añade, tras los recuentos
#   ya existentes, una sección final con una línea por cada `Case` con
#   `sigma >= 2`: como mínimo `external_id`, `match.pl_name`, el parámetro,
#   el valor y el propio `sigma`, más el fragmento de `source_snippet`.
# - `_case_to_json` (el volcado de `--json`) añade `sigma_via_a`,
#   `sigma_via_b` y `source_snippet` al diccionario por caso.
# ---------------------------------------------------------------------------


def test_case_acepta_source_snippet_opcional(script):
    """`Case` debe tener el campo opcional `source_snippet`; construir uno
    con ese argumento no debe fallar con `TypeError` (argumento
    inesperado)."""
    caso = _case(
        script,
        via_a=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        source_snippet="...un fragmento de ejemplo...",
    )
    assert caso.source_snippet == "...un fragmento de ejemplo..."


def test_case_con_solo_via_b_no_lleva_source_snippet(script):
    """`source_snippet` es un fragmento del texto del PAPER que ve la vía
    (a); un caso con solo vía (b) [el valor viene del propio catálogo] no
    tiene de dónde sacarlo y debe quedarse en `None`."""
    caso = _case(
        script,
        via_a=None,
        via_b=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        source_snippet=None,
    )
    assert caso.source_snippet is None


def test_run_experiment_full_rellena_source_snippet_con_el_fragmento_de_origen(script):
    """El `Case` resultante para un ítem con valor de la vía (a) debe
    llevar en `source_snippet` un recorte del texto de origen (abstract +
    claims -- el mismo texto que recibe `parse_measurements`) de
    aproximadamente 40 caracteres a cada lado del valor, no el abstract
    completo ni `None`. Se usa `_run_experiment_full` (no expuesta por
    `run_experiment`, que solo devuelve el `Report` agregado) porque es el
    único punto de la API que da acceso a los `Case` individuales.
    `_resolve_case` debe rellenar `source_snippet` a partir de ese mismo
    recorte."""
    index_csv = _index_csv([("WASP-1 b", "WASP-1", "", "", "", "1")])
    ps_csv = _EMPTY_PS_CSV + b"WASP-1 b,WASP-1,1,prior ref,90,5,5,0,,,,0,,,,0\n"
    client = _archive_client_for_ps(script, index_csv=index_csv, ps_csv=ps_csv)

    abstract = (
        "We present new observations of the transiting hot Jupiter WASP-1 b "
        r"obtained with a ground-based spectrograph. The planet has a mass "
        r"of $5.0 \pm 0.5 M_J$, consistent with earlier radial velocity "
        "estimates from independent monitoring campaigns over three seasons."
    )
    reading = script.EpReading(
        external_id="2409.00004",
        title="t",
        abstract=abstract,
        objects=("WASP-1 b",),
        claims=(),
        run_ids=frozenset({uuid4()}),
    )

    cases, _report = script._run_experiment_full([reading], client)
    (caso,) = cases

    assert caso.via_a is not None
    assert caso.source_snippet is not None, "un Case con via_a debe llevar source_snippet"
    assert "5.0" in caso.source_snippet
    assert "M_J" in caso.source_snippet
    assert len(caso.source_snippet) < len(abstract), (
        "el fragmento debe ser un recorte alrededor del valor, no el abstract completo"
    )


def test_case_to_json_incluye_sigma_via_a_y_sigma_via_b(script):
    """El volcado `--json` (`_case_to_json`) debe incluir `sigma_via_a` y
    `sigma_via_b`, no solo `sigma`/`sigma_default`: son los que
    `render_text` ya desglosa en `sigma_bins_via_a`/`sigma_bins_via_b`
    (sección 16), y el informe JSON debe poder reproducir esos mismos
    números caso a caso."""
    caso = _case(
        script,
        via_a=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        via_b=script.Measurement(parameter="mass_earth", value=130, err_plus=5, err_minus=5),
        sigma=1.5,
        sigma_via_a=1.5,
        sigma_via_b=3.0,
    )

    data = script._case_to_json(caso)

    assert data["sigma_via_a"] == pytest.approx(1.5)
    assert data["sigma_via_b"] == pytest.approx(3.0)


def test_case_to_json_incluye_source_snippet(script):
    """Igual que el test anterior, para `source_snippet`: `Case` debe
    tener el campo y `_case_to_json` debe volcarlo en el diccionario."""
    caso = _case(
        script,
        via_a=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        source_snippet="...fragmento...",
    )

    data = script._case_to_json(caso)

    assert data["source_snippet"] == "...fragmento..."


def test_render_text_lista_casos_con_sigma_mayor_igual_2_y_su_fragmento(script):
    """`render_text` debe aceptar también la lista de `Case` (no solo el
    `Report` agregado) y añadir, tras los recuentos ya existentes, una
    sección final que liste cada caso con sigma >= 2: `external_id`,
    planeta, parámetro, valor, sigma y el fragmento de `source_snippet`.
    Un caso con sigma < 2 no debe aparecer en esa lista."""
    caso_alto = _case(
        script,
        external_id="2409.00099",
        match=script.MatchResult(kind="planet", pl_name="WASP-99 b"),
        via_a=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        sigma=4.2,
        source_snippet="...a mass of 100 ± 5 Earth masses for the transiting planet...",
    )
    caso_bajo = _case(
        script,
        external_id="2409.00098",
        match=script.MatchResult(kind="planet", pl_name="WASP-98 b"),
        via_a=script.Measurement(parameter="mass_earth", value=100, err_plus=5, err_minus=5),
        sigma=1.0,
        source_snippet="fragmento irrelevante que no debe aparecer",
    )
    report = script.build_report([caso_alto, caso_bajo], nights=1)

    texto = script.render_text(report, [caso_alto, caso_bajo])

    assert "2409.00099" in texto
    assert "WASP-99 b" in texto
    assert "mass_earth" in texto
    assert "4.2" in texto
    assert "a mass of 100" in texto
    assert "2409.00098" not in texto, "un caso con sigma < 2 no debe listarse"
