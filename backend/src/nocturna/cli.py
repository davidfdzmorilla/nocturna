"""Punto de entrada de línea de comandos de Nocturna.

Composition root del proyecto: el único módulo que ata las implementaciones
concretas de infraestructura (`ArxivClient`, `SqlAlchemyItemRepository`,
`create_db_engine`) a los puertos de dominio y a los casos de uso de
`application/`. `argparse` de la biblioteca estándar, sin `typer` ni
`click`: dos flags no los justifican.

Es también el único módulo autorizado a ver a la vez `PipelineConfig`
(Pydantic, `infrastructure/config.py`) y `BudgetPolicy` (dataclass,
`application/budget.py`): `application/budget.py` no puede importar
`infrastructure/` (regla 13 de su docstring), así que la traducción entre
ambos tipos vive aquí, en `budget_policy_from_config`, y en ningún otro
sitio.

Solo existe el subcomando `run-night`. No hay `run-item`: es alcance de T41
y `ddd-conventions` prohíbe los stubs — un subcomando que hoy solo sabría
fallar es peor que su ausencia.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx

from nocturna.application.budget import (
    BudgetPolicy,
    effective_nightly_tokens,
    is_within_window,
    seconds_until_hard_stop,
)
from nocturna.application.use_cases.ingest_arxiv import IngestArxiv, IngestResult
from nocturna.infrastructure.arxiv.atom import ArxivFeedError
from nocturna.infrastructure.arxiv.client import (
    MIN_REQUEST_INTERVAL_S,
    ArxivClient,
    ArxivUnavailable,
)
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.clock import SystemClock
from nocturna.infrastructure.config import PipelineConfig, Settings, load_pipeline_config
from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository
from nocturna.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
    unit_of_work,
)

# Timeout explícito de las peticiones HTTP a arXiv. No es una clave de
# `pipeline.toml`: es un detalle del cliente HTTP, no una decisión de
# negocio que el autor necesite calibrar.
_HTTP_TIMEOUT_S = 30.0

# Longitud de la vista previa del abstract en el reporte de --dry-run. Con
# ~100 ítems por noche, volcar el abstract completo hace la salida
# ilegible; este recorte basta para revisar una noche de un vistazo.
_ABSTRACT_PREVIEW_CHARS = 200

_RUN_NIGHT_WITHOUT_DRY_RUN_MESSAGE = (
    "run-night sin --dry-run todavía no hace nada: el orquestador nocturno "
    "(Reader, Popularizer, Editor) es la tarea T44 y no existe todavía. "
    "Usa --dry-run para la ingesta real y persistida de arXiv, sin llamar a ningún agente."
)

# `pipeline.toml` guarda `budget.weekly_reset_weekday` como nombre de día en
# texto (validado contra ese mismo literal en `infrastructure/config.py`);
# `BudgetPolicy.weekly_reset_weekday` sigue la convención de
# `datetime.weekday()` (lunes=0 ... domingo=6, ver docstring de
# `application/budget.py`). Esta tabla es la traducción entre ambos, y vive
# aquí porque es la única pieza de `budget_policy_from_config` que no es una
# copia directa de un campo.
_WEEKDAY_TO_INT: dict[str, int] = {
    "monday": 0,
    "tuesday": 1,
    "wednesday": 2,
    "thursday": 3,
    "friday": 4,
    "saturday": 5,
    "sunday": 6,
}


def budget_policy_from_config(config: PipelineConfig) -> BudgetPolicy:
    """Traduce `PipelineConfig` (Pydantic, infraestructura) a `BudgetPolicy`
    (dataclass, aplicación), campo a campo y con argumentos nombrados.

    Deliberadamente sin `**vars()` ni bucles genéricos sobre los campos de
    `config`: si mañana se añade una clave de gasto a `pipeline.toml` y
    nadie la mapea aquí, `BudgetPolicy(...)` debe fallar por argumento
    obligatorio ausente, no colarse con un valor por defecto inventado.
    """
    return BudgetPolicy(
        nightly_tokens=config.budget.nightly_tokens,
        editor_reserve_tokens=config.budget.editor_reserve_tokens,
        max_items_per_night=config.limits.max_items_per_night,
        max_turns_per_agent=config.limits.max_turns_per_agent,
        max_editor_calls_per_night=config.limits.max_editor_calls_per_night,
        max_calls_per_item=config.limits.max_calls_per_item,
        item_timeout_s=config.limits.item_timeout_s,
        run_timeout_s=config.limits.run_timeout_s,
        window_start=config.window.start,
        window_hard_stop=config.window.hard_stop,
        weekly_reset_weekday=_WEEKDAY_TO_INT[config.budget.weekly_reset_weekday],
        weekly_reset_hour=config.budget.weekly_reset_hour,
        reset_day_multiplier=config.budget.reset_day_multiplier,
    )


def system_clock_from_config(config: PipelineConfig) -> SystemClock:
    """Construye el `SystemClock` de la ventana de ejecución, en la zona de
    `window.timezone` (`config/pipeline.toml`)."""
    return SystemClock(ZoneInfo(config.window.timezone))


def _parse_since(value: str) -> datetime:
    """Convierte 'YYYY-MM-DD' en un `datetime` aware a las 00:00 UTC."""
    try:
        parsed = date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"'{value}' no es una fecha YYYY-MM-DD") from exc
    return datetime.combine(parsed, time.min, tzinfo=UTC)


def _parse_categories(value: str) -> list[str]:
    """Convierte 'a,b,c' en una lista de categorías no vacías."""
    categories = [category.strip() for category in value.split(",") if category.strip()]
    if not categories:
        raise argparse.ArgumentTypeError("no puede estar vacío")
    return categories


def _default_since(now: datetime) -> datetime:
    """Ayer a las 00:00 UTC, tomando `now` (aware, UTC) como referencia."""
    return datetime.combine(now.date() - timedelta(days=1), time.min, tzinfo=UTC)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nocturna", description="Pipeline nocturno de Nocturna.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_night = subparsers.add_parser(
        "run-night",
        help="Ejecuta (o simula) la noche de análisis.",
        description=(
            "Ejecuta la noche de análisis: ingesta de arXiv y, cuando exista T44, los agentes."
        ),
    )
    run_night.add_argument(
        "--dry-run",
        action="store_true",
        help=(
            "Ingesta real de arXiv y persistencia en base de datos, sin llamar a ningún "
            "agente LLM. Lo 'dry' es la ausencia de LLM, no la ausencia de escritura: "
            "los ítems nuevos se guardan igual que en una ejecución completa."
        ),
    )
    run_night.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        metavar="YYYY-MM-DD",
        help="Fecha desde la que ingerir (00:00 UTC). Por defecto: ayer a las 00:00 UTC.",
    )
    run_night.add_argument(
        "--categories",
        type=_parse_categories,
        default=None,
        metavar="a,b,c",
        help=(
            "Categorías arXiv separadas por comas, para probar una categoría distinta "
            "una noche sin editar config/pipeline.toml. Por defecto: "
            "sources.arxiv.categories de pipeline.toml."
        ),
    )
    return parser


async def _run_ingest(
    *,
    since: datetime,
    categories: Sequence[str],
    config: PipelineConfig,
    settings: Settings,
) -> IngestResult:
    """Compone y ejecuta la ingesta de arXiv dentro de una única unidad de trabajo."""
    async with httpx.AsyncClient(timeout=_HTTP_TIMEOUT_S) as http:
        limiter = RateLimiter(MIN_REQUEST_INTERVAL_S)
        client = ArxivClient(
            http,
            limiter=limiter,
            page_size=config.sources.arxiv.page_size,
            now=lambda: datetime.now(UTC),
        )
        engine = create_db_engine(settings)
        factory = create_session_factory(engine)
        with unit_of_work(factory) as session:
            repo = SqlAlchemyItemRepository(session)
            ingest = IngestArxiv(source=client, items=repo)
            return await ingest(
                since=since,
                categories=categories,
                max_results=config.sources.arxiv.max_results_per_fetch,
            )


def _abstract_preview(abstract: str) -> str:
    """Primeros `_ABSTRACT_PREVIEW_CHARS` caracteres del abstract, con marca
    de truncamiento. El abstract completo ya está persistido; esto es solo
    para revisar una noche de un vistazo por consola."""
    if len(abstract) <= _ABSTRACT_PREVIEW_CHARS:
        return abstract
    return abstract[:_ABSTRACT_PREVIEW_CHARS].rstrip() + "…"


def _print_dry_run_report(result: IngestResult) -> None:
    # `result.items` es lo que devolvió arXiv, incluidos los ítems que ya
    # estaban en base: el listado no distingue nuevos de duplicados, así
    # que se etiqueta como tal para no inducir a error. Los contadores del
    # resumen son los que sí distinguen.
    print(f"arXiv devolvió {result.fetched} ítems (incluye los que ya estaban en base):")
    for item in result.items:
        categories = ", ".join(item.categories)
        print(
            f"  {item.external_id} · {item.published_at.isoformat()} · {item.title} · {categories}"
        )
        print(f"    {_abstract_preview(item.abstract)}")
    print()
    print(
        "Resumen: "
        f"fetched={result.fetched} new={result.new} duplicates={result.duplicates} "
        f"skipped={result.skipped}"
    )
    if result.truncated:
        print(
            "AVISO: la ingesta se truncó por max_results_per_fetch; puede haber más ítems "
            "sin ingerir esta noche."
        )


def _print_budget_plan(policy: BudgetPolicy, timezone: str, now: datetime) -> None:
    """Plan de gasto de la noche: lo que `CLAUDE.md` pide de `--dry-run`
    ("ingesta + plan de gasto, sin llamar a agentes"), sin instanciar el
    guarda de gasto de `application/` (necesita un `Run` que en `--dry-run`
    no existe) ni tocar la base de datos. Solo usa las funciones puras de
    `application/budget.py` (incluida `seconds_until_hard_stop`, la misma
    que usa `BudgetGuard.seconds_until_hard_stop`; no hay una segunda copia
    de ese cálculo en este módulo) y la hora del `SystemClock`.
    """
    effective_tokens = effective_nightly_tokens(policy, now)
    reader_popularizer_available = effective_tokens - policy.editor_reserve_tokens
    within_window = is_within_window(now, policy.window_start, policy.window_hard_stop)
    seconds_left = seconds_until_hard_stop(now, policy.window_start, policy.window_hard_stop)

    print()
    print("Plan de gasto de la noche:")
    print(
        f"  presupuesto nocturno efectivo: {effective_tokens} tokens "
        f"(reserva del Editor: {policy.editor_reserve_tokens} tokens)"
    )
    print(
        f"  disponible para Reader/Popularizer: {reader_popularizer_available} tokens "
        f"(ya con la reserva del Editor restada)"
    )
    print(
        f"  disponible para el Editor: {effective_tokens} tokens (presupuesto completo, sin restar)"
    )
    print(
        f"  ventana configurada: {policy.window_start.isoformat()}–"
        f"{policy.window_hard_stop.isoformat()} ({timezone})"
    )
    if within_window:
        print(f"  dentro de la ventana ahora mismo: sí (quedan {seconds_left} s para el hard_stop)")
    else:
        print("  dentro de la ventana ahora mismo: no")


def _run_night(args: argparse.Namespace) -> int:
    if not args.dry_run:
        print(_RUN_NIGHT_WITHOUT_DRY_RUN_MESSAGE, file=sys.stderr)
        return 2

    settings = Settings()
    config = load_pipeline_config()
    since = args.since if args.since is not None else _default_since(datetime.now(UTC))
    categories = args.categories if args.categories is not None else config.sources.arxiv.categories

    try:
        result = asyncio.run(
            _run_ingest(since=since, categories=categories, config=config, settings=settings)
        )
    except (ArxivUnavailable, ArxivFeedError) as exc:
        print(f"error consultando arXiv: {exc}", file=sys.stderr)
        return 1

    _print_dry_run_report(result)

    policy = budget_policy_from_config(config)
    clock = system_clock_from_config(config)
    _print_budget_plan(policy, config.window.timezone, clock.now())

    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _run_night(args)


if __name__ == "__main__":
    sys.exit(main())
