"""Tests de la carga tipada de `config/pipeline.toml`.

Todos los casos negativos escriben su propio TOML en `tmp_path`; ninguno
modifica `config/pipeline.toml` del repositorio. Ningún test toca la red
ni la base de datos.
"""

from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from nocturna.infrastructure.config import Settings, load_pipeline_config

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PIPELINE_TOML = REPO_ROOT / "config" / "pipeline.toml"

# Configuración base válida, con la misma forma que config/pipeline.toml,
# usada como punto de partida para los casos negativos.
BASE_TOML = """
[budget]
nightly_tokens = 300000
editor_reserve_tokens = 60000
weekly_reset_weekday = "monday"
weekly_reset_hour = 0
reset_day_multiplier = 1.0
reader_estimated_tokens = 6000

[limits]
max_items_per_night = 40
max_turns_per_agent = 3
item_timeout_s = 180
run_timeout_s = 16200
max_editor_calls_per_night = 2
max_calls_per_item = 2

[window]
start = "00:00"
hard_stop = "04:45"
timezone = "Europe/Madrid"

[models]
reader = "sonnet"
popularizer = "sonnet"
editor = "opus"

[sources.arxiv]
categories = ["astro-ph.EP", "astro-ph.GA"]
page_size = 100
max_results_per_fetch = 400

[llm]
provider = "agent_sdk"
"""


def _write_toml(tmp_path: Path, content: str, name: str = "pipeline.toml") -> Path:
    path = tmp_path / name
    path.write_text(content)
    return path


def test_carga_el_pipeline_toml_del_repositorio():
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    assert config.budget.nightly_tokens == 300000
    assert config.limits.max_items_per_night == 40
    assert config.limits.max_turns_per_agent == 3
    assert config.limits.max_editor_calls_per_night == 2
    assert config.limits.max_calls_per_item == 2
    assert config.window.start == time(0, 0)
    assert config.window.hard_stop == time(4, 45)
    assert config.window.timezone == "Europe/Madrid"
    assert config.models.reader
    assert config.models.popularizer
    assert config.models.editor
    assert config.llm.provider == "agent_sdk"


def test_las_categorias_arxiv_no_estan_vacias():
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    assert len(config.sources.arxiv.categories) > 0
    assert all(c.startswith("astro-ph") for c in config.sources.arxiv.categories)


def test_arxiv_page_size_y_max_results_se_cargan():
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    assert config.sources.arxiv.page_size == 100
    assert config.sources.arxiv.max_results_per_fetch == 400


def test_una_clave_desconocida_falla(tmp_path):
    content = BASE_TOML.replace(
        "[budget]\n",
        '[budget]\nunknown_key = "surprise"\n',
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_falta_nightly_tokens_falla(tmp_path):
    content = BASE_TOML.replace("nightly_tokens = 300000\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_reserva_del_editor_menor_que_el_presupuesto_nocturno(tmp_path):
    content = BASE_TOML.replace(
        "editor_reserve_tokens = 60000",
        "editor_reserve_tokens = 300000",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError, match="editor_reserve_tokens"):
        load_pipeline_config(path)


@pytest.mark.parametrize(
    "old,new",
    [
        ("nightly_tokens = 300000", "nightly_tokens = 0"),
        ("max_items_per_night = 40", "max_items_per_night = 0"),
        ("max_turns_per_agent = 3", "max_turns_per_agent = -1"),
        ("item_timeout_s = 180", "item_timeout_s = 0"),
        ("run_timeout_s = 16200", "run_timeout_s = -10"),
    ],
)
def test_valores_de_presupuesto_no_positivos_fallan(tmp_path, old, new):
    content = BASE_TOML.replace(old, new)
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_falta_max_editor_calls_per_night_falla(tmp_path):
    content = BASE_TOML.replace("max_editor_calls_per_night = 2\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize("value", [0, -1])
def test_max_editor_calls_per_night_no_positivo_falla(tmp_path, value):
    content = BASE_TOML.replace(
        "max_editor_calls_per_night = 2",
        f"max_editor_calls_per_night = {value}",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_falta_max_calls_per_item_falla(tmp_path):
    content = BASE_TOML.replace("max_calls_per_item = 2\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize("value", [0, -1])
def test_max_calls_per_item_no_positivo_falla(tmp_path, value):
    content = BASE_TOML.replace(
        "max_calls_per_item = 2",
        f"max_calls_per_item = {value}",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_falta_window_timezone_falla(tmp_path):
    content = BASE_TOML.replace('timezone = "Europe/Madrid"\n', "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_window_timezone_inexistente_falla(tmp_path):
    content = BASE_TOML.replace(
        'timezone = "Europe/Madrid"',
        'timezone = "Europe/Nowhereland"',
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError, match="window.timezone"):
        load_pipeline_config(path)


@pytest.mark.parametrize("hour", [-1, 24])
def test_weekly_reset_hour_fuera_de_rango_falla(tmp_path, hour):
    content = BASE_TOML.replace(
        "weekly_reset_hour = 0",
        f"weekly_reset_hour = {hour}",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_weekly_reset_weekday_desconocido_falla(tmp_path):
    content = BASE_TOML.replace(
        'weekly_reset_weekday = "monday"',
        'weekly_reset_weekday = "Monday"',
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_reset_day_multiplier_menor_que_uno_falla(tmp_path):
    content = BASE_TOML.replace(
        "reset_day_multiplier = 1.0",
        "reset_day_multiplier = 0.5",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_window_start_igual_a_hard_stop_falla(tmp_path):
    content = BASE_TOML.replace(
        'hard_stop = "04:45"',
        'hard_stop = "00:00"',
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError, match="window.start"):
        load_pipeline_config(path)


def test_window_que_cruza_medianoche_es_valida(tmp_path):
    # No se valida start < hard_stop: la ventana nocturna cruza medianoche
    # por definición (ver WindowConfig), así que start="00:00" con
    # hard_stop="04:45" debe cargar sin error.
    path = _write_toml(tmp_path, BASE_TOML)

    config = load_pipeline_config(path)

    assert config.window.start == time(0, 0)
    assert config.window.hard_stop == time(4, 45)


def test_provider_desconocido_falla(tmp_path):
    content = BASE_TOML.replace('provider = "agent_sdk"', 'provider = "api_key"')
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_la_configuracion_es_inmutable():
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    with pytest.raises(ValidationError):
        config.budget.nightly_tokens = 1


def test_database_url_se_sobrescribe_por_entorno(monkeypatch):
    monkeypatch.setenv(
        "NOCTURNA_DATABASE_URL",
        "postgresql+psycopg://test:test@localhost:5432/nocturna_test",
    )

    settings = Settings()

    assert settings.database_url == "postgresql+psycopg://test:test@localhost:5432/nocturna_test"


def test_categorias_vacias_fallan(tmp_path):
    content = BASE_TOML.replace(
        'categories = ["astro-ph.EP", "astro-ph.GA"]',
        "categories = []",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize(
    "old,new",
    [
        ("page_size = 100", "page_size = 0"),
        ("max_results_per_fetch = 400", "max_results_per_fetch = 0"),
        ("page_size = 100", "page_size = -1"),
        ("max_results_per_fetch = 400", "max_results_per_fetch = -1"),
    ],
)
def test_arxiv_page_size_y_max_results_no_positivos_fallan(tmp_path, old, new):
    content = BASE_TOML.replace(old, new)
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_arxiv_page_size_mayor_que_max_results_per_fetch_falla(tmp_path):
    content = BASE_TOML.replace(
        "page_size = 100",
        "page_size = 500",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError, match="page_size"):
        load_pipeline_config(path)


def test_ruta_inexistente_propaga_file_not_found_error(tmp_path):
    missing_path = tmp_path / "no_existe.toml"

    with pytest.raises(FileNotFoundError):
        load_pipeline_config(missing_path)
