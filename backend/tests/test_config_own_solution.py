"""T83: `[tension.own_solution]` en `pipeline.toml` (obligatoria, sin valores por defecto)."""

import pytest
from pydantic import ValidationError
from test_config import BASE_TOML, REAL_PIPELINE_TOML, _write_toml

from nocturna.infrastructure.config import TensionOwnSolutionConfig, load_pipeline_config

SECTION = "[tension.own_solution]\nvalue_rel_tolerance = 0.01\npubdate_margin_months = 6\n"


def _without_section() -> str:
    assert SECTION in BASE_TOML
    return BASE_TOML.replace(SECTION, "")


def test_el_pipeline_toml_real_carga_la_regla_de_solucion_propia():
    own = load_pipeline_config(REAL_PIPELINE_TOML).tension.own_solution

    assert isinstance(own, TensionOwnSolutionConfig)
    assert own.value_rel_tolerance == 0.01
    assert own.pubdate_margin_months == 6


def test_el_toml_base_de_los_tests_carga_la_seccion(tmp_path):
    own = load_pipeline_config(_write_toml(tmp_path, BASE_TOML)).tension.own_solution

    assert (own.value_rel_tolerance, own.pubdate_margin_months) == (0.01, 6)


def test_falta_tension_own_solution_falla(tmp_path):
    with pytest.raises(ValidationError, match="own_solution"):
        load_pipeline_config(_write_toml(tmp_path, _without_section()))


@pytest.mark.parametrize("key", ["value_rel_tolerance", "pubdate_margin_months"])
def test_falta_una_clave_de_own_solution_falla(tmp_path, key):
    content = "\n".join(line for line in BASE_TOML.splitlines() if not line.startswith(f"{key} = "))

    with pytest.raises(ValidationError, match=key):
        load_pipeline_config(_write_toml(tmp_path, content))


def test_clave_extra_en_own_solution_falla(tmp_path):
    content = BASE_TOML.replace(SECTION, SECTION + "extra = 1\n")

    with pytest.raises(ValidationError):
        load_pipeline_config(_write_toml(tmp_path, content))


def test_la_seccion_no_tiene_valores_por_defecto_en_codigo():
    with pytest.raises(ValidationError):
        TensionOwnSolutionConfig()  # type: ignore[call-arg]


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("value_rel_tolerance = 0.01", "value_rel_tolerance = 0"),
        ("value_rel_tolerance = 0.01", "value_rel_tolerance = -0.01"),
        ("value_rel_tolerance = 0.01", "value_rel_tolerance = 0.11"),
        ("value_rel_tolerance = 0.01", "value_rel_tolerance = 1.0"),
        ("pubdate_margin_months = 6", "pubdate_margin_months = -1"),
        ("pubdate_margin_months = 6", "pubdate_margin_months = 1.5"),
        ("pubdate_margin_months = 6", 'pubdate_margin_months = "6"'),
    ],
)
def test_valores_invalidos_de_own_solution_fallan(tmp_path, old, new):
    assert old in BASE_TOML

    with pytest.raises(ValidationError):
        load_pipeline_config(_write_toml(tmp_path, BASE_TOML.replace(old, new)))


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    [
        ("value_rel_tolerance = 0.01", "value_rel_tolerance = 0.1", (0.1, 6)),
        ("value_rel_tolerance = 0.01", "value_rel_tolerance = 1e-6", (1e-6, 6)),
        ("pubdate_margin_months = 6", "pubdate_margin_months = 0", (0.01, 0)),
    ],
)
def test_limites_admitidos_de_own_solution_cargan(tmp_path, old, new, expected):
    config = load_pipeline_config(_write_toml(tmp_path, BASE_TOML.replace(old, new)))

    own = config.tension.own_solution
    assert (own.value_rel_tolerance, own.pubdate_margin_months) == expected


def test_la_seccion_es_inmutable(tmp_path):
    own = load_pipeline_config(REAL_PIPELINE_TOML).tension.own_solution

    with pytest.raises(ValidationError):
        own.value_rel_tolerance = 0.05  # type: ignore[misc]


def test_own_solution_rule_from_config_toma_los_valores_de_la_configuracion(tmp_path):
    from nocturna.cli import own_solution_rule_from_config

    toml = BASE_TOML.replace("value_rel_tolerance = 0.01", "value_rel_tolerance = 0.03").replace(
        "pubdate_margin_months = 6", "pubdate_margin_months = 9"
    )
    rule = own_solution_rule_from_config(load_pipeline_config(_write_toml(tmp_path, toml)))

    assert (rule.value_rel_tolerance, rule.pubdate_margin_months) == (0.03, 9)
