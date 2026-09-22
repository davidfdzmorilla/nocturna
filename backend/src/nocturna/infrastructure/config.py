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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nocturna.infrastructure.arxiv.retry import MAX_JITTER_FACTOR

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
    # reader_estimated_tokens: estimación de coste que `run-item`/T44 pasan a
    # `BudgetGuard.authorize` para el Reader (`ReadItem.estimated_tokens`).
    # No es una regla del guard (por eso no vive en `BudgetPolicy`, que
    # describe el guard, no estimaciones por rol): es lo que se compara
    # contra el presupuesto restante *antes* de saber el gasto real. Si el
    # gasto real de una llamada supera esta estimación, `nightly_tokens` se
    # rebasa en (real − estimado) de esa última llamada -- modo de fallo que
    # ya tenía el diseño de T30 (`authorize` autoriza contra una estimación,
    # nunca contra el gasto real, que no se conoce todavía); T41 no lo
    # empeora, pero el valor de esta clave sí importa para el tamaño del
    # rebase. Ver comentario en config/pipeline.toml. Valor a calibrar en
    # T60, como item_timeout_s.
    reader_estimated_tokens: int = Field(gt=0)
    # popularizer_estimated_tokens: la misma estimación que
    # reader_estimated_tokens, pero para el Popularizer, que `BudgetGuard.
    # authorize` compara contra el presupuesto restante antes de llamarlo. Sin
    # default, por el mismo motivo: que falte ruidosamente si alguien copia un
    # TOML viejo. No vive en `BudgetPolicy` (esa describe reglas del guard, no
    # estimaciones por rol). Ver comentario en config/pipeline.toml.
    popularizer_estimated_tokens: int = Field(gt=0)
    # editor_base_tokens: parte fija de la estimación de coste de la única
    # llamada al Editor por noche. Sin default, mismo motivo que las claves
    # de estimación vecinas: que falte ruidosamente si alguien copia un TOML
    # viejo. `PipelineConfig` la combina con `editor_tokens_per_candidate` y
    # `limits.max_items_per_night` para comprobar que la reserva del Editor
    # basta en el peor caso (ver `PipelineConfig._editor_reserve_covers_worst_case`).
    # Ver comentario en config/pipeline.toml.
    editor_base_tokens: int = Field(gt=0)
    # editor_tokens_per_candidate: coste marginal, por candidato, de la
    # llamada al Editor. Mismo motivo de ausencia de default que
    # editor_base_tokens. Ver comentario en config/pipeline.toml.
    editor_tokens_per_candidate: int = Field(gt=0)

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
    # editor_timeout_s: timeout propio de la llamada al Editor, separado de
    # item_timeout_s (pensado para un único abstract, no para los hasta
    # max_items_per_night candidatos que recibe el Editor en una sola
    # llamada a Opus). Sin default, por el mismo motivo que las claves
    # vecinas: que falte ruidosamente si alguien copia un TOML viejo. Ver
    # comentario en config/pipeline.toml.
    editor_timeout_s: int = Field(gt=0)
    # Cuenta intentos, no éxitos: lo que gasta presupuesto es la llamada, no
    # el acierto. Ver comentario en config/pipeline.toml.
    max_editor_calls_per_night: int = Field(ge=1)
    # Tope de llamadas de Reader/Popularizer por ítem (intento + reintento
    # por JSON inválido). `BudgetGuard` lo multiplica por max_items_per_night
    # para obtener el tope de llamadas de esos roles en toda la noche. Ver
    # comentario en config/pipeline.toml.
    max_calls_per_item: int = Field(ge=1)
    # popularizer_min_interest_score: umbral de `Reading.interest_score` a
    # partir del cual se llama al Popularizer. Es una palanca de gasto (decide
    # cuántas llamadas al Popularizer hay por noche), no una regla del guard,
    # así que vive aquí y no en `BudgetPolicy`. PLAN_TAREAS.md fija en T60
    # «se decide si el interest_score >= 4 es el umbral correcto»: tiene que
    # ser calibrable sin tocar código. Ver comentario en config/pipeline.toml.
    popularizer_min_interest_score: int = Field(ge=1, le=5)
    # max_consecutive_failures: cortacircuitos de fallos consecutivos de
    # AgentRunner (cualquier rol). Mitigación (a) que docs/TECHNICAL_DEBT.md
    # asigna a T44 para la fuga de ~600.000-800.000 tokens/noche de llamadas
    # que mueren antes de un ResultMessage (timeout de socket, cancelación
    # previa a cualquier respuesta, kill del proceso): sin contabilidad de
    # tokens que leer, la única defensa es dejar de intentarlo. No vive en
    # BudgetPolicy (application/budget.py): no es una regla de BudgetGuard
    # sobre tokens, es una decisión de orquestación de RunNight sobre cuándo
    # dejar de llamar. cli.py (T44, paso 3) la inyecta directamente en
    # RunNight. Sin default, mismo motivo que las claves vecinas: que falte
    # ruidosamente si alguien copia un TOML viejo. Ver comentario en
    # config/pipeline.toml.
    max_consecutive_failures: int = Field(gt=0)


class WindowConfig(BaseModel):
    """Ventana de ejecución nocturna, cruza medianoche por definición."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    start: time
    hard_stop: time
    # Zona IANA en la que se interpretan `start` y `hard_stop`. Sin ella, un
    # proceso con `TZ` no propagada (cron, contenedor en UTC por defecto)
    # dispararía el hard_stop a una hora local incorrecta. Ver comentario en
    # config/pipeline.toml.
    timezone: str

    @model_validator(mode="after")
    def _start_and_hard_stop_differ(self) -> "WindowConfig":
        if self.start == self.hard_stop:
            raise ValueError("window.start no puede ser igual a window.hard_stop")
        return self

    @model_validator(mode="after")
    def _timezone_is_a_valid_iana_zone(self) -> "WindowConfig":
        try:
            ZoneInfo(self.timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"window.timezone '{self.timezone}' no es una zona IANA válida"
            ) from exc
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
    # retry_max_attempts / retry_base_delay_s / retry_max_elapsed_s: política
    # de reintento de `infrastructure/arxiv/retry.py::RetryPolicy` ante
    # fallos transitorios de la API de arXiv (406, 429, 5xx, errores de
    # transporte -- ver el docstring de `client.py`, motivado por el 406 del
    # 2026-09-21). Sin default en código, mismo motivo que las claves
    # vecinas: que falte ruidosamente si alguien copia un TOML viejo. Ver
    # comentario en config/pipeline.toml.
    retry_max_attempts: int = Field(ge=1)
    retry_base_delay_s: float = Field(gt=0)
    retry_max_elapsed_s: float = Field(gt=0)

    @model_validator(mode="after")
    def _page_size_within_max_results(self) -> "ArxivConfig":
        if self.page_size > self.max_results_per_fetch:
            raise ValueError("page_size no puede ser mayor que max_results_per_fetch")
        return self

    @model_validator(mode="after")
    def _retry_max_elapsed_covers_worst_case(self) -> "ArxivConfig":
        """`retry_max_elapsed_s` debe cubrir el PEOR CASO (con jitter) de
        las esperas entre los `retry_max_attempts` intentos, no el backoff
        nominal sin jitter: `_default_jitter`
        (`infrastructure/arxiv/retry.py`) multiplica cada espera hasta por
        `MAX_JITTER_FACTOR` (1,5), así que el peor caso real es
        `base_delay_s * MAX_JITTER_FACTOR * (2**(max_attempts-1) - 1)`, no
        `base_delay_s * (2**(max_attempts-1) - 1)` -- una config con
        `retry_max_elapsed_s` entre ambos valores pasaría una validación
        contra el backoff nominal pero podría cortarse por `max_elapsed_s`
        en una secuencia real con jitter desfavorable. Mismo espíritu que
        `PipelineConfig._editor_reserve_covers_worst_case`: debe fallar al
        cargar, de día, no descubrirse a las 00:05.
        """
        worst_case = (
            self.retry_base_delay_s * MAX_JITTER_FACTOR * (2 ** (self.retry_max_attempts - 1) - 1)
        )
        if self.retry_max_elapsed_s < worst_case:
            raise ValueError(
                "retry_max_elapsed_s "
                f"({self.retry_max_elapsed_s}) no cubre el peor caso, con jitter, de las "
                f"esperas entre retry_max_attempts intentos (retry_base_delay_s * "
                f"MAX_JITTER_FACTOR * (2**(retry_max_attempts-1) - 1) = {worst_case})"
            )
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

    @model_validator(mode="after")
    def _editor_reserve_covers_worst_case(self) -> "PipelineConfig":
        """La reserva del Editor debe cubrir el peor caso de candidatos.

        `budget` y `limits` viven en secciones distintas de `pipeline.toml`
        y este es el único modelo que ve ambas a la vez; el validador vive
        aquí y no en `BudgetConfig` por eso. El peor caso es que los
        `limits.max_items_per_night` ítems de la noche lleguen todos como
        candidatos al Editor: si `editor_base_tokens +
        max_items_per_night * editor_tokens_per_candidate` no cabe en
        `editor_reserve_tokens`, quien calibre `max_items_per_night` o las
        estimaciones por candidato en T60 sin subir la reserva a la vez
        debe ver la carga de la configuración fallar de día, no descubrirlo
        a las 04:00 con la noche entera pagada y el Editor sin presupuesto
        para publicar nada.
        """
        worst_case = (
            self.budget.editor_base_tokens
            + self.limits.max_items_per_night * self.budget.editor_tokens_per_candidate
        )
        if worst_case > self.budget.editor_reserve_tokens:
            raise ValueError(
                "editor_reserve_tokens "
                f"({self.budget.editor_reserve_tokens}) no cubre el peor caso de "
                f"editor_base_tokens + max_items_per_night * editor_tokens_per_candidate "
                f"({self.budget.editor_base_tokens} + {self.limits.max_items_per_night} * "
                f"{self.budget.editor_tokens_per_candidate} = {worst_case})"
            )
        return self


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
    """Ajustes de proceso: conexión a datos, ruta de configuración y CORS de la API.

    Ninguna clave de gasto vive aquí: esas se leen exclusivamente de
    `pipeline.toml` a través de `load_pipeline_config`. `cors_origins` es un
    ajuste de proceso de la API de lectura (T50), no del pipeline: por eso
    vive aquí y no en `PipelineConfig`.
    """

    model_config = SettingsConfigDict(env_prefix="NOCTURNA_", env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://nocturna:nocturna@localhost:5433/nocturna"
    config_path: Path = Field(default_factory=_default_config_path)
    # Orígenes permitidos por CORS en la API de lectura (T50). Nunca "*": la
    # API sirve datos de solo lectura pero identificar qué candidatos NO se
    # publicaron ya es información sensible (ver api/routes/findings.py), y
    # un origen abierto facilita justo ese tipo de scraping cruzado.
    # Sobreescribible con NOCTURNA_CORS_ORIGINS, en formato JSON -- pydantic-
    # settings parsea los tipos complejos (aquí, list[str]) como JSON, no
    # como lista separada por comas: NOCTURNA_CORS_ORIGINS='["http://a",
    # "http://b"]' funciona, NOCTURNA_CORS_ORIGINS='http://a,http://b' falla
    # con SettingsError al construir Settings(). Esto importa más allá de la
    # API: `cli.py` también construye `Settings()` para el pipeline nocturno
    # (`run-night`, `run-item`), así que un .env mal escrito con comas rompe
    # también el arranque de esos comandos, no solo la API.
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])
