"""Tests de la carga tipada de `config/pipeline.toml`.

Todos los casos negativos escriben su propio TOML en `tmp_path`; ninguno
modifica `config/pipeline.toml` del repositorio. Ningún test toca la red
ni la base de datos.
"""

from datetime import time
from pathlib import Path

import pytest
from pydantic import ValidationError

from nocturna.infrastructure.arxiv.retry import MAX_JITTER_FACTOR
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
popularizer_estimated_tokens = 7000
editor_base_tokens = 4000
editor_tokens_per_candidate = 700

[limits]
max_items_per_night = 40
max_turns_per_agent = 3
item_timeout_s = 180
editor_timeout_s = 300
run_timeout_s = 16200
max_editor_calls_per_night = 2
max_calls_per_item = 2
popularizer_min_interest_score = 4
max_consecutive_failures = 5

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
retry_max_attempts = 4
retry_base_delay_s = 5.0
retry_max_elapsed_s = 60.0

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
    assert config.budget.editor_base_tokens == 2500
    assert config.budget.editor_tokens_per_candidate == 850
    assert config.limits.max_items_per_night == 40
    assert config.limits.max_turns_per_agent == 3
    assert config.limits.max_editor_calls_per_night == 2
    assert config.limits.max_calls_per_item == 2
    assert config.limits.max_consecutive_failures == 5
    assert config.window.start == time(0, 0)
    assert config.window.hard_stop == time(4, 45)
    assert config.window.timezone == "Europe/Madrid"
    assert config.models.reader
    assert config.models.popularizer
    assert config.models.editor
    assert config.llm.provider == "agent_sdk"
    assert config.sources.arxiv.retry_max_attempts == 4
    assert config.sources.arxiv.retry_base_delay_s == 5.0
    assert config.sources.arxiv.retry_max_elapsed_s == 60.0


def test_la_reserva_del_editor_cubre_el_peor_caso_en_el_toml_real():
    """Invariante que `PipelineConfig._editor_reserve_covers_worst_case`
    impone en la carga (ver `infrastructure/config.py`): `editor_base_tokens
    + max_items_per_night * editor_tokens_per_candidate` debe caber en
    `editor_reserve_tokens`. Expresado sobre las claves, no sobre los
    literales congelados en `test_carga_el_pipeline_toml_del_repositorio`:
    ese test fija los valores actuales de calibración y se pondrá en rojo
    cuando alguien los recalibre (correcto, es su función); este debe seguir
    en verde después de cualquier recalibración futura que respete el
    invariante, incluida la definitiva del cierre de T60."""
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    worst_case = (
        config.budget.editor_base_tokens
        + config.limits.max_items_per_night * config.budget.editor_tokens_per_candidate
    )

    assert worst_case <= config.budget.editor_reserve_tokens


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


def test_falta_editor_base_tokens_falla(tmp_path):
    content = BASE_TOML.replace("editor_base_tokens = 4000\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_falta_editor_tokens_per_candidate_falla(tmp_path):
    content = BASE_TOML.replace("editor_tokens_per_candidate = 700\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize("value", [0, -1])
def test_editor_base_tokens_no_positivo_falla(tmp_path, value):
    content = BASE_TOML.replace(
        "editor_base_tokens = 4000",
        f"editor_base_tokens = {value}",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize("value", [0, -1])
def test_editor_tokens_per_candidate_no_positivo_falla(tmp_path, value):
    content = BASE_TOML.replace(
        "editor_tokens_per_candidate = 700",
        f"editor_tokens_per_candidate = {value}",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_reserva_del_editor_no_cubre_el_peor_caso_de_candidatos_falla(tmp_path):
    # editor_base_tokens (4000) + max_items_per_night (40) *
    # editor_tokens_per_candidate (700) = 32000, que cabe en
    # editor_reserve_tokens = 60000. Bajar la reserva por debajo de ese
    # peor caso debe hacer fallar la carga, de día, no a las 04:00.
    content = BASE_TOML.replace(
        "editor_reserve_tokens = 60000",
        "editor_reserve_tokens = 30000",
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


def test_falta_max_consecutive_failures_falla(tmp_path):
    content = BASE_TOML.replace("max_consecutive_failures = 5\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize("value", [0, -1])
def test_max_consecutive_failures_no_positivo_falla(tmp_path, value):
    content = BASE_TOML.replace(
        "max_consecutive_failures = 5",
        f"max_consecutive_failures = {value}",
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


def test_cors_origins_se_sobrescribe_por_entorno_en_formato_json(monkeypatch):
    # pydantic-settings parsea los tipos complejos (list[str]) desde
    # variables de entorno como JSON, no como lista separada por comas (ver
    # comentario de `cors_origins` en infrastructure/config.py). Este test
    # congela ese formato: si alguien lo cambiara a comas sin actualizar el
    # comentario, esto se pone en rojo.
    monkeypatch.setenv("NOCTURNA_CORS_ORIGINS", '["http://a.example", "http://b.example"]')

    settings = Settings()

    assert settings.cors_origins == ["http://a.example", "http://b.example"]


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


def test_falta_retry_max_attempts_falla(tmp_path):
    content = BASE_TOML.replace("retry_max_attempts = 4\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_falta_retry_base_delay_s_falla(tmp_path):
    content = BASE_TOML.replace("retry_base_delay_s = 5.0\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_falta_retry_max_elapsed_s_falla(tmp_path):
    content = BASE_TOML.replace("retry_max_elapsed_s = 60.0\n", "")
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize("value", [0, -1])
def test_retry_max_attempts_no_positivo_falla(tmp_path, value):
    content = BASE_TOML.replace(
        "retry_max_attempts = 4",
        f"retry_max_attempts = {value}",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize("value", [0, -1.0])
def test_retry_base_delay_s_no_positivo_falla(tmp_path, value):
    content = BASE_TOML.replace(
        "retry_base_delay_s = 5.0",
        f"retry_base_delay_s = {value}",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_retry_max_elapsed_s_menor_que_el_peor_caso_con_jitter_falla(tmp_path):
    """`ArxivConfig._retry_max_elapsed_covers_nominal_backoff`: con
    `retry_max_attempts = 4` y `retry_base_delay_s = 5.0`, el peor caso de
    esperas CON jitter (`_default_jitter` multiplica hasta `MAX_JITTER_FACTOR`,
    ver `retry.py`) es `5.0 * MAX_JITTER_FACTOR * (2**3 - 1) = 52.5`. Un
    `retry_max_elapsed_s` por debajo de eso no deja tiempo ni para ese peor
    caso, y debe fallar al cargar, no descubrirse a las 00:05 con la
    secuencia de reintentos cortándose antes de tiempo porque el jitter dio
    la peor tirada posible."""
    worst_case = 5.0 * MAX_JITTER_FACTOR * (2**3 - 1)
    content = BASE_TOML.replace(
        "retry_max_elapsed_s = 60.0",
        f"retry_max_elapsed_s = {worst_case - 0.1}",
    )
    path = _write_toml(tmp_path, content)

    with pytest.raises(ValidationError, match="retry_max_elapsed_s"):
        load_pipeline_config(path)


def test_retry_max_elapsed_s_igual_al_peor_caso_con_jitter_es_valida(tmp_path):
    worst_case = 5.0 * MAX_JITTER_FACTOR * (2**3 - 1)
    content = BASE_TOML.replace(
        "retry_max_elapsed_s = 60.0",
        f"retry_max_elapsed_s = {worst_case}",
    )
    path = _write_toml(tmp_path, content)

    config = load_pipeline_config(path)

    assert config.sources.arxiv.retry_max_elapsed_s == worst_case


def test_ruta_inexistente_propaga_file_not_found_error(tmp_path):
    missing_path = tmp_path / "no_existe.toml"

    with pytest.raises(FileNotFoundError):
        load_pipeline_config(missing_path)
