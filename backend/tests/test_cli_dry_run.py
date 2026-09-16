"""Tests de `nocturna.cli` para el subcomando `run-night`, sin PostgreSQL.

Se dividen en dos ficheros:

- Este (`tests/test_cli_dry_run.py`): comportamiento que no necesita tocar
  PostgreSQL ni una fuente arXiv -- el camino sin `--dry-run` (no debe hacer
  nada) y el cálculo del valor por defecto de `--since`.
- `tests/db/test_cli_dry_run_db.py`: todo lo que exige ejecutar `main()` de
  verdad hasta el final -- ingesta con una fuente arXiv falsa, propagación
  de `--since`/`--categories`, errores de arXiv y la comprobación de que
  `claude_agent_sdk` no se importa durante `--dry-run`. `cli.py` no ofrece
  ningún parámetro para sustituir `ArxivClient` o la sesión de base de datos
  por un doble (`_run_ingest` construye su propio `httpx.AsyncClient` y su
  propia `unit_of_work` internamente, ver docstring de ese módulo), así que
  la única costura disponible sin tocarlo es sustituir `httpx.AsyncClient`
  por uno que sirva `httpx.MockTransport` -- y eso obliga a esos tests a
  correr contra la base de datos real de test, porque `_run_ingest` persiste
  de verdad. De ahí la separación.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest

from nocturna import cli
from nocturna.application.budget import BudgetPolicy
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.domain.entities import Item
from nocturna.infrastructure.config import load_pipeline_config

_NOW = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

REPO_ROOT = Path(__file__).resolve().parents[2]
REAL_PIPELINE_TOML = REPO_ROOT / "config" / "pipeline.toml"

# Misma forma que `config/pipeline.toml` / `tests/test_config.py::BASE_TOML`,
# usada aquí solo para variar `budget.weekly_reset_weekday` sin depender de
# otro fichero de test.
_BASE_TOML = """
[budget]
nightly_tokens = 300000
editor_reserve_tokens = 60000
weekly_reset_weekday = "{weekday}"
weekly_reset_hour = 0
reset_day_multiplier = 1.0

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


def _item(*, external_id: str, abstract: str) -> Item:
    return Item(
        source="arxiv",
        external_id=external_id,
        title=f"Título de {external_id}",
        abstract=abstract,
        categories=["astro-ph.EP"],
        published_at=_NOW,
        fetched_at=_NOW,
    )


def test_dry_run_imprime_vista_previa_del_abstract_truncada_en_los_largos(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`Hecho cuando` de T20 exige que `--dry-run` muestre los abstracts: sin
    esta vista previa bajo cada ítem, el reporte solo listaría títulos y no
    lo cumpliría. Un abstract largo se recorta a `_ABSTRACT_PREVIEW_CHARS`
    caracteres con marca de truncamiento (`…`); uno corto se imprime entero
    y sin la marca."""
    long_abstract = "A" * 250
    short_abstract = "Resumen corto que no necesita truncarse."
    result = IngestResult(
        fetched=2,
        new=2,
        duplicates=0,
        skipped=0,
        truncated=False,
        items=[
            _item(external_id="2609.00001", abstract=long_abstract),
            _item(external_id="2609.00002", abstract=short_abstract),
        ],
    )

    cli._print_dry_run_report(result)

    captured = capsys.readouterr().out
    lines = captured.splitlines()
    assert f"    {'A' * 200}…" in lines
    assert f"    {short_abstract}" in lines
    assert "A" * 201 not in captured


def test_run_night_sin_dry_run_devuelve_2_y_menciona_t44(
    capsys: pytest.CaptureFixture[str],
) -> None:
    code = cli.main(["run-night"])

    captured = capsys.readouterr()
    assert code == 2
    assert "T44" in captured.err
    assert captured.out == ""


def test_run_night_sin_dry_run_no_toca_configuracion_ni_red_ni_base_de_datos(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ninguna de las piezas que solo hacen falta para una ingesta real
    (configuración, cliente HTTP, motor de base de datos) debe invocarse
    cuando falta `--dry-run`: se sustituyen todas por dobles que revientan
    si alguien las llama, y `main()` debe devolver 2 sin tocarlas."""

    def _boom(*args, **kwargs):
        raise AssertionError("no debería llamarse sin --dry-run")

    monkeypatch.setattr(cli, "Settings", _boom)
    monkeypatch.setattr(cli, "load_pipeline_config", _boom)
    monkeypatch.setattr(cli, "create_db_engine", _boom)
    monkeypatch.setattr(httpx, "AsyncClient", _boom)

    assert cli.main(["run-night"]) == 2


def test_valor_por_defecto_de_since_no_se_calcula_al_construir_el_parser() -> None:
    """El default de `--since` en el parser es `None`, no un `datetime` ya
    calculado: si se calculara al construir el parser, quedaría fijado al
    momento de importar el módulo, no al de ejecutar `run-night`."""
    parser = cli._build_parser()

    args = parser.parse_args(["run-night", "--dry-run"])

    assert args.since is None


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        # Caso normal, a media tarde.
        (datetime(2026, 9, 16, 10, 0, tzinfo=UTC), datetime(2026, 9, 15, 0, 0, tzinfo=UTC)),
        # Justo en la medianoche: "ayer" sigue siendo el día natural anterior.
        (datetime(2026, 9, 16, 0, 0, 0, tzinfo=UTC), datetime(2026, 9, 15, 0, 0, tzinfo=UTC)),
        # Cruce de año.
        (datetime(2026, 1, 1, 12, 0, tzinfo=UTC), datetime(2025, 12, 31, 0, 0, tzinfo=UTC)),
    ],
    ids=["media-tarde", "medianoche-exacta", "cruce-de-año"],
)
def test_default_since_es_ayer_a_medianoche_utc(now: datetime, expected: datetime) -> None:
    result = cli._default_since(now)

    assert result == expected
    assert result.tzinfo is UTC


# --- budget_policy_from_config: traducción campo a campo --------------------


def test_budget_policy_from_config_traduce_todos_los_campos_de_gasto() -> None:
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    policy = cli.budget_policy_from_config(config)

    assert policy.nightly_tokens == config.budget.nightly_tokens
    assert policy.editor_reserve_tokens == config.budget.editor_reserve_tokens
    assert policy.max_items_per_night == config.limits.max_items_per_night
    assert policy.max_turns_per_agent == config.limits.max_turns_per_agent
    assert policy.max_editor_calls_per_night == config.limits.max_editor_calls_per_night
    assert policy.max_calls_per_item == config.limits.max_calls_per_item
    assert policy.item_timeout_s == config.limits.item_timeout_s
    assert policy.run_timeout_s == config.limits.run_timeout_s
    assert policy.window_start == config.window.start
    assert policy.window_hard_stop == config.window.hard_stop
    assert policy.weekly_reset_hour == config.budget.weekly_reset_hour
    assert policy.reset_day_multiplier == config.budget.reset_day_multiplier


def test_budget_policy_from_config_devuelve_una_budget_policy() -> None:
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    policy = cli.budget_policy_from_config(config)

    assert isinstance(policy, BudgetPolicy)


@pytest.mark.parametrize(
    ("weekday_name", "expected_int"),
    [
        ("monday", 0),
        ("tuesday", 1),
        ("wednesday", 2),
        ("thursday", 3),
        ("friday", 4),
        ("saturday", 5),
        ("sunday", 6),
    ],
)
def test_budget_policy_from_config_traduce_weekly_reset_weekday_a_convencion_de_datetime(
    tmp_path: Path, weekday_name: str, expected_int: int
) -> None:
    """`pipeline.toml` guarda el día como texto; `BudgetPolicy` lo espera en
    la convención de `datetime.weekday()` (lunes=0 ... domingo=6). Cubre los
    siete días, no solo el "monday" que ya usa `config/pipeline.toml` de
    verdad."""
    path = tmp_path / "pipeline.toml"
    path.write_text(_BASE_TOML.format(weekday=weekday_name))
    config = load_pipeline_config(path)

    policy = cli.budget_policy_from_config(config)

    assert policy.weekly_reset_weekday == expected_int


# --- system_clock_from_config: zona de window.timezone -----------------------


def test_system_clock_from_config_usa_la_zona_de_window_timezone() -> None:
    config = load_pipeline_config(REAL_PIPELINE_TOML)

    clock = cli.system_clock_from_config(config)

    assert clock._timezone == ZoneInfo(config.window.timezone)


def test_system_clock_from_config_con_otra_zona_del_toml(tmp_path: Path) -> None:
    path = tmp_path / "pipeline.toml"
    content = _BASE_TOML.format(weekday="monday").replace(
        'timezone = "Europe/Madrid"', 'timezone = "UTC"'
    )
    path.write_text(content)
    config = load_pipeline_config(path)

    clock = cli.system_clock_from_config(config)

    assert clock._timezone == ZoneInfo("UTC")


# --- _print_budget_plan -------------------------------------------------------


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 300_000,
        "editor_reserve_tokens": 60_000,
        "max_items_per_night": 40,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 2,
        "item_timeout_s": 180,
        "run_timeout_s": 16_200,
        "window_start": time(0, 0),
        "window_hard_stop": time(4, 45),
        "weekly_reset_weekday": 0,
        "weekly_reset_hour": 0,
        "reset_day_multiplier": 1.0,
    }
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


def test_print_budget_plan_muestra_el_disponible_de_reader_ya_con_la_reserva_restada(
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _policy(nightly_tokens=300_000, editor_reserve_tokens=60_000)
    now = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)

    cli._print_budget_plan(policy, "UTC", now)

    captured = capsys.readouterr().out
    assert "presupuesto nocturno efectivo: 300000 tokens" in captured
    assert "reserva del Editor: 60000 tokens" in captured
    assert "disponible para Reader/Popularizer: 240000 tokens" in captured
    assert "disponible para el Editor: 300000 tokens" in captured


def test_print_budget_plan_dentro_de_la_ventana_muestra_los_segundos_restantes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _policy(window_start=time(0, 0), window_hard_stop=time(4, 45))
    now = datetime(2026, 1, 1, 4, 44, 30, tzinfo=UTC)

    cli._print_budget_plan(policy, "UTC", now)

    captured = capsys.readouterr().out
    assert "dentro de la ventana ahora mismo: sí (quedan 30 s para el hard_stop)" in captured


def test_print_budget_plan_fuera_de_la_ventana_no_anuncia_segundos_restantes(
    capsys: pytest.CaptureFixture[str],
) -> None:
    policy = _policy(window_start=time(0, 0), window_hard_stop=time(4, 45))
    now = datetime(2026, 1, 1, 4, 46, 0, tzinfo=UTC)

    cli._print_budget_plan(policy, "UTC", now)

    captured = capsys.readouterr().out
    assert "dentro de la ventana ahora mismo: no" in captured
    assert "quedan" not in captured


def test_print_budget_plan_aplica_el_reset_day_multiplier_al_presupuesto_efectivo(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`_print_budget_plan` pasa `now` por `effective_nightly_tokens`, no
    `policy.nightly_tokens` a pelo: la noche del reinicio semanal el plan
    debe reflejar el presupuesto ya multiplicado."""
    policy = _policy(
        nightly_tokens=300_000,
        editor_reserve_tokens=60_000,
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=2.0,
    )
    monday = datetime(2026, 1, 5, 10, 0, tzinfo=UTC)
    assert monday.weekday() == 0

    cli._print_budget_plan(policy, "UTC", monday)

    captured = capsys.readouterr().out
    assert "presupuesto nocturno efectivo: 600000 tokens" in captured
    assert "disponible para Reader/Popularizer: 540000 tokens" in captured
