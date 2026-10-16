"""`[sources.arxiv] max_catchup_days` y `max_results_per_fetch` (T93, D4)."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from nocturna.infrastructure.config import load_pipeline_config

REAL_PIPELINE_TOML = Path(__file__).resolve().parents[2] / "config" / "pipeline.toml"


def _toml_con(tmp_path: Path, valor: int) -> Path:
    text = REAL_PIPELINE_TOML.read_text(encoding="utf-8")
    text = re.sub(r"^max_catchup_days = .*\n", "", text, flags=re.MULTILINE)
    text = text.replace("[sources.arxiv]\n", f"[sources.arxiv]\nmax_catchup_days = {valor}\n", 1)
    path = tmp_path / "pipeline.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_el_toml_real_fija_max_catchup_days_en_7() -> None:
    assert load_pipeline_config(REAL_PIPELINE_TOML).sources.arxiv.max_catchup_days == 7


def test_el_toml_real_fija_max_results_per_fetch_en_800() -> None:
    assert load_pipeline_config(REAL_PIPELINE_TOML).sources.arxiv.max_results_per_fetch == 800


@pytest.mark.parametrize("valor", [1, 3, 14])
def test_max_catchup_days_dentro_de_rango_se_carga(tmp_path: Path, valor: int) -> None:
    config = load_pipeline_config(_toml_con(tmp_path, valor))

    assert config.sources.arxiv.max_catchup_days == valor


@pytest.mark.parametrize("valor", [0, 15, -1])
def test_max_catchup_days_fuera_de_rango_falla(tmp_path: Path, valor: int) -> None:
    with pytest.raises(ValidationError, match="max_catchup_days"):
        load_pipeline_config(_toml_con(tmp_path, valor))
