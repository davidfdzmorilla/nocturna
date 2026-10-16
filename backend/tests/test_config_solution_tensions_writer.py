"""`[solution_tensions] extreme_sigma` y el interruptor del redactor del archivo (T85.e)."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from nocturna import cli
from nocturna.application.agents.prompt_loader import (
    EDITOR_PROMPT_VERSION,
    EDITOR_V4_PROMPT_VERSION,
    WRITER_ARCHIVE_PROMPT_VERSION,
    load_prompt,
)
from nocturna.infrastructure.config import load_pipeline_config

REAL_PIPELINE_TOML = Path(__file__).resolve().parents[2] / "config" / "pipeline.toml"


def _config_with(tmp_path: Path, *, replace: str, by: str):
    text = REAL_PIPELINE_TOML.read_text()
    assert replace in text
    path = tmp_path / "pipeline.toml"
    path.write_text(text.replace(replace, by, 1))
    return load_pipeline_config(path)


def test_el_toml_real_trae_extreme_sigma_50_y_el_redactor_encendido() -> None:
    # Activado el 2026-10-14 tras el merge de T85.w (commit `chore(config)`).
    config = load_pipeline_config(REAL_PIPELINE_TOML)
    assert config.solution_tensions.extreme_sigma == 50
    assert config.solution_tensions.writer_enabled is True


def test_sin_extreme_sigma_la_configuracion_falla(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        _config_with(tmp_path, replace="extreme_sigma = 50", by="")


@pytest.mark.parametrize("value", ["3.0", "2", "0", "-1"])
def test_extreme_sigma_debe_ser_mayor_que_el_umbral(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValidationError):
        _config_with(tmp_path, replace="extreme_sigma = 50", by=f"extreme_sigma = {value}")


def test_editor_prompt_version_sigue_a_writer_enabled(tmp_path: Path) -> None:
    on = load_pipeline_config(REAL_PIPELINE_TOML)
    off = _config_with(tmp_path, replace="writer_enabled = true", by="writer_enabled = false")
    assert cli.editor_prompt_version(off) == EDITOR_PROMPT_VERSION == "editor-v3"
    assert cli.editor_prompt_version(on) == EDITOR_V4_PROMPT_VERSION == "editor-v4"


def test_los_prompts_nuevos_existen_y_editor_v3_no_conoce_solution_tension() -> None:
    assert "<solution_tension>" in load_prompt(WRITER_ARCHIVE_PROMPT_VERSION)
    assert "`solution_tension`" in load_prompt(EDITOR_V4_PROMPT_VERSION)
    assert "solution_tension" not in load_prompt(EDITOR_PROMPT_VERSION)
