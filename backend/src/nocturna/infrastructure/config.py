"""Carga y validación de la configuración del pipeline nocturno.

Lee `config/pipeline.toml` con `tomllib` (biblioteca estándar) y lo valida
contra modelos Pydantic estrictos: ninguna clave de gasto tiene valor por
defecto, así que una clave ausente o mal escrita hace fallar la carga en
lugar de degradar a un presupuesto "seguro" inventado.
"""

import tomllib
from datetime import time
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_WeeklyResetWeekday = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


class BudgetConfig(BaseModel):
    """Presupuesto de tokens de la noche y del reinicio semanal."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    nightly_tokens: int = Field(gt=0)
    editor_reserve_tokens: int = Field(ge=0)
    weekly_reset_weekday: _WeeklyResetWeekday
    weekly_reset_hour: int = Field(ge=0, le=23)
    reset_day_multiplier: float = Field(ge=1.0)

    @model_validator(mode="after")
    def _reserve_within_nightly_budget(self) -> "BudgetConfig":
        if self.editor_reserve_tokens >= self.nightly_tokens:
            raise ValueError("editor_reserve_tokens debe ser menor que nightly_tokens")
        return self


class LimitsConfig(BaseModel):
    """Límites duros de ítems, turnos y tiempos de ejecución."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    max_items_per_night: int = Field(gt=0)
    max_turns_per_agent: int = Field(gt=0)
    item_timeout_s: int = Field(gt=0)
    run_timeout_s: int = Field(gt=0)


class WindowConfig(BaseModel):
    """Ventana de ejecución nocturna, cruza medianoche por definición."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: time
    hard_stop: time

    @model_validator(mode="after")
    def _start_and_hard_stop_differ(self) -> "WindowConfig":
        if self.start == self.hard_stop:
            raise ValueError("window.start no puede ser igual a window.hard_stop")
        return self


class ModelsConfig(BaseModel):
    """Modelo Claude asignado a cada rol de agente."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    reader: str
    popularizer: str
    editor: str


class ArxivConfig(BaseModel):
    """Categorías arXiv de las que se ingesta contenido y límites de paginación."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    categories: list[str] = Field(min_length=1)
    page_size: int = Field(gt=0)
    max_results_per_fetch: int = Field(gt=0)

    @model_validator(mode="after")
    def _page_size_within_max_results(self) -> "ArxivConfig":
        if self.page_size > self.max_results_per_fetch:
            raise ValueError("page_size no puede ser mayor que max_results_per_fetch")
        return self


class SourcesConfig(BaseModel):
    """Fuentes de ingesta configuradas."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    arxiv: ArxivConfig


class LLMConfig(BaseModel):
    """Proveedor LLM activo."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: Literal["agent_sdk"]


class PipelineConfig(BaseModel):
    """Configuración completa del pipeline, agregando todas las secciones."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    budget: BudgetConfig
    limits: LimitsConfig
    window: WindowConfig
    models: ModelsConfig
    sources: SourcesConfig
    llm: LLMConfig


def _repo_root() -> Path:
    # backend/src/nocturna/infrastructure/config.py -> raíz del repositorio.
    return Path(__file__).resolve().parents[4]


def _default_config_path() -> Path:
    return _repo_root() / "config" / "pipeline.toml"


def load_pipeline_config(path: Path | None = None) -> PipelineConfig:
    """Carga y valida `pipeline.toml`.

    Propaga `FileNotFoundError` si el fichero no existe y `ValidationError`
    si el contenido no cumple el esquema. Nunca degrada a una configuración
    por defecto.
    """
    resolved_path = path if path is not None else _default_config_path()
    with resolved_path.open("rb") as f:
        data = tomllib.load(f)
    return PipelineConfig.model_validate(data)


class Settings(BaseSettings):
    """Ajustes de proceso: solo conexión a datos y ruta de configuración.

    Ninguna clave de gasto vive aquí: esas se leen exclusivamente de
    `pipeline.toml` a través de `load_pipeline_config`.
    """

    model_config = SettingsConfigDict(env_prefix="NOCTURNA_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://nocturna:nocturna@localhost:5433/nocturna"
    config_path: Path = Field(default_factory=_default_config_path)
