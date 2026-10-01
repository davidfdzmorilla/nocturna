"""Tests de la sección `[exoplanet_filter]` de `pipeline.toml` (T79).

Los casos negativos parten del TOML real y sustituyen solo esa sección en
`tmp_path`; ninguno modifica `config/pipeline.toml`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from nocturna.infrastructure.config import ExoplanetFilterConfig, load_pipeline_config

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PIPELINE_TOML = REPO_ROOT / "config" / "pipeline.toml"

# La sección empieza en `[exoplanet_filter]` y termina antes del siguiente
# encabezado en columna 0 (las listas internas van sangradas).
_SECTION = re.compile(r"^\[exoplanet_filter\]\n.*?(?=^\[)", re.DOTALL | re.MULTILINE)


def _config_with_section(tmp_path: Path, section: str | None) -> Path:
    text = REAL_PIPELINE_TOML.read_text(encoding="utf-8")
    assert _SECTION.search(text), "el TOML real debe tener [exoplanet_filter]"
    replacement = "" if section is None else f"[exoplanet_filter]\n{section}\n\n"
    path = tmp_path / "pipeline.toml"
    path.write_text(_SECTION.sub(lambda _m: replacement, text, count=1), encoding="utf-8")
    return path


def test_el_toml_real_carga_con_la_lista_b():
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    assert config.exoplanet_filter.keywords == ["exoplanet", "exoplanets"]
    assert len(config.exoplanet_filter.designation_patterns) == 8


def test_listas_vacias_se_admiten(tmp_path):
    path = _config_with_section(tmp_path, "keywords = []\ndesignation_patterns = []")

    config = load_pipeline_config(path)

    assert config.exoplanet_filter.keywords == []
    assert config.exoplanet_filter.designation_patterns == []


def test_regex_invalida_falla(tmp_path):
    path = _config_with_section(tmp_path, 'keywords = []\ndesignation_patterns = ["(sin cerrar"]')

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize(
    "section",
    [
        'keywords = [""]\ndesignation_patterns = []',
        'keywords = []\ndesignation_patterns = [""]',
        'keywords = ["  "]\ndesignation_patterns = []',
    ],
)
def test_cadena_vacia_falla(tmp_path, section):
    with pytest.raises(ValidationError):
        load_pipeline_config(_config_with_section(tmp_path, section))


def test_clave_extra_falla(tmp_path):
    path = _config_with_section(
        tmp_path, 'keywords = []\ndesignation_patterns = []\nplanets = ["planet"]'
    )

    with pytest.raises(ValidationError):
        load_pipeline_config(path)


def test_falta_la_seccion_falla(tmp_path):
    with pytest.raises(ValidationError):
        load_pipeline_config(_config_with_section(tmp_path, None))


@pytest.mark.parametrize("missing", ["keywords", "designation_patterns"])
def test_sin_defaults_en_codigo_cada_clave_es_obligatoria(missing):
    kwargs = {"keywords": [], "designation_patterns": []}
    del kwargs[missing]

    with pytest.raises(ValidationError):
        ExoplanetFilterConfig(**kwargs)
