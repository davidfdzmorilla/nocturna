"""Punto de entrada de línea de comandos de Nocturna.

Composition root del proyecto: el único módulo que ata las implementaciones
concretas de infraestructura (`ArxivClient`, `SqlAlchemyItemRepository`,
`create_db_engine`) a los puertos de dominio y a los casos de uso de
`application/`. `argparse` de la biblioteca estándar, sin `typer` ni
`click`: dos flags no los justifican.

Solo existe el subcomando `run-night`. No hay `run-item`: es alcance de T41
y `ddd-conventions` prohíbe los stubs — un subcomando que hoy solo sabría
fallar es peor que su ausencia.
"""

import argparse
import asyncio
import sys
from collections.abc import Sequence
from datetime import UTC, date, datetime, time, timedelta

import httpx

from nocturna.application.use_cases.ingest_arxiv import IngestArxiv, IngestResult
from nocturna.infrastructure.arxiv.atom import ArxivFeedError
from nocturna.infrastructure.arxiv.client import (
    MIN_REQUEST_INTERVAL_S,
    ArxivClient,
    ArxivUnavailable,
)
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
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
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return _run_night(args)


if __name__ == "__main__":
    sys.exit(main())
