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

**T41, paso 8: `run-item <item_id>`.** Segundo subcomando, para depurar el
Reader sobre un único `Item` sin esperar a la noche completa (T44). Es
también el único sitio del proyecto que implementa `AgentWorkFactory`
(`application/unit_of_work.py`) contra SQLAlchemy de verdad
(`_agent_work_factory`): ese puerto existe precisamente para que
`application/` pueda pedir unidades de trabajo sin importar `Session`, y la
implementación concreta -- que sí importa `Session` -- vive aquí, en el
composition root, y en ningún otro sitio.

Códigos de salida de `run-item`: `0` éxito, `1` lectura fallida (JSON
inválido agotado tras los reintentos, un `LLMTimeout`/`LLMRateLimited` o
cualquier otro `LLMError` del proveedor -- `ReadItem` (T41) captura los tres
y los traduce a `ReadItemResult.outcome`; ninguno llega a `cli.py` como
excepción, ver `_read_one_item`), `2` UUID de `item_id` mal formado (lo
produce `argparse` al fallar la conversión de tipo, no código propio), `3`
el ítem no existe o no está en `NEW`, `4` denegación de `BudgetGuard`
(incluida la ventana de ejecución fuera de horario -- sin ninguna bandera
para saltarla, `budget-guard-review` § 1; el `Run` que esta invocación haya
creado se cierra con `terminal_status_for(exc.reason)`, `KILLED` para
`OUTSIDE_WINDOW` y `PARTIAL` para el resto, nunca un literal a mano). El
proveedor se construye sin `mcp_servers` ni `allowed_tools`: inyectar MCP
haría que `query()` emita varios `ResultMessage` y que `AgentSDKProvider` se
quede solo con el gasto del último (`docs/TECHNICAL_DEBT.md`, "obligatorio
revisar antes de que T41 pase `mcp_servers` a ningún rol").

**Presupuesto de noche entre invocaciones sueltas de `run-item`.** Cada
invocación sin un `Run` `RUNNING` vivo crea el suyo propio con
`budget_tokens = effective_nightly_tokens(...)` -- un presupuesto de noche
completo y fresco -- y lo cierra al terminar (`_current_or_new_run`,
`_finish_run`). Un bucle de shell que invoque `run-item` varias veces por
fuera de una noche de `run-night` (T44) pasa por `BudgetGuard` en cada
llamada (nada se salta el guarda), pero cada invocación abre su propio
presupuesto: `nightly_tokens` deja de ser un tope agregado de esa secuencia,
y solo `window.hard_stop` la acota. `_run_item` avisa de esto por `stderr`
cada vez que abre un `Run` nuevo. Se valoraron dos alternativas para acotar
el conjunto -- crear el `Run` descontando lo ya consumido esa noche, o
reutilizar el último `Run` cerrado de la "noche en curso" -- y se descartaron
para esta tarea: ambas exigen definir qué cuenta como "noche en curso" para
un `Run` ya cerrado (¿cuánto tiempo desde `finished_at` sigue siendo la misma
noche? ¿qué pasa si cruza `window.hard_stop`?), una pregunta que no tiene
respuesta en `pipeline.toml` hoy y que abriría más ambigüedad de la que
resuelve. Queda como aviso explícito, no como límite duro; registrada como
decisión abierta para quien la retome.
"""

import argparse
import asyncio
import logging
import sys
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session, sessionmaker

from nocturna.application.agents.prompt_loader import READER_PROMPT_VERSION, load_prompt
from nocturna.application.budget import (
    BudgetDenied,
    BudgetGuard,
    BudgetPolicy,
    DenyReason,
    effective_nightly_tokens,
    is_within_window,
    seconds_until_hard_stop,
    terminal_status_for,
)
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.application.use_cases.ingest_arxiv import IngestArxiv, IngestResult
from nocturna.application.use_cases.read_item import ReadItem, ReadItemResult, ReadOutcome
from nocturna.domain.entities import Item, Run, RunStatus
from nocturna.domain.errors import InvalidTransition
from nocturna.domain.llm import AgentRole, LLMProvider
from nocturna.infrastructure.arxiv.atom import ArxivFeedError
from nocturna.infrastructure.arxiv.client import (
    MIN_REQUEST_INTERVAL_S,
    ArxivClient,
    ArxivUnavailable,
)
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.clock import SystemClock
from nocturna.infrastructure.config import PipelineConfig, Settings, load_pipeline_config
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
    unit_of_work,
)

# `AgentSDKProvider` (infrastructure/llm/agent_sdk_provider.py) NO se importa
# aquí arriba: un test de T20 comprueba que `claude_agent_sdk` no entra en
# `sys.modules` durante `run-night --dry-run`, y un import a nivel de módulo
# lo rompería. `_run_item` lo importa dentro de su propio cuerpo, el único
# camino que de verdad lo necesita.

_logger = logging.getLogger(__name__)

# Timeout explícito de las peticiones HTTP a arXiv. No es una clave de
# `pipeline.toml`: es un detalle del cliente HTTP, no una decisión de
# negocio que el autor necesite calibrar.
_HTTP_TIMEOUT_S = 30.0

# Longitud de la vista previa del abstract en el reporte de --dry-run, y de
# la vista previa del `summary` en el reporte de `run-item`. Con ~100 ítems
# por noche, volcar el texto completo hace la salida ilegible; este recorte
# basta para revisar un ítem (o una noche) de un vistazo por consola.
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

    run_item = subparsers.add_parser(
        "run-item",
        help="Lee un único Item con el Reader, para depurar.",
        description=(
            "Ejecuta el Reader sobre un único Item por su id, dentro de la ventana de "
            "ejecución y del presupuesto de la noche. Pensado para depurar; no sustituye "
            "a run-night (T44)."
        ),
    )
    run_item.add_argument(
        "item_id",
        type=UUID,
        metavar="ITEM_ID",
        help="UUID del Item a leer. Un UUID mal formado es un error de argumentos (código 2).",
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


def _abstract_preview(text: str) -> str:
    """Primeros `_ABSTRACT_PREVIEW_CHARS` caracteres de `text`, con marca de
    truncamiento. El texto completo ya está persistido (el abstract de un
    Item, el summary de una Reading); esto es solo para revisar un ítem o
    una noche de un vistazo por consola."""
    if len(text) <= _ABSTRACT_PREVIEW_CHARS:
        return text
    return text[:_ABSTRACT_PREVIEW_CHARS].rstrip() + "…"


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


# --- run-item -----------------------------------------------------------


def _current_or_new_run(
    session_factory: sessionmaker[Session], policy: BudgetPolicy, clock: SystemClock
) -> tuple[UUID, bool]:
    """Reutiliza el `Run` `RUNNING` de la noche en curso, o crea uno nuevo.

    Devuelve `(run_id, reused)`. `reused=True` significa que ya había un Run
    `RUNNING` -- la noche de T44 en marcha -- y `run-item` no debe cerrarlo:
    es la noche de otro quien lo abrió y quien decide cuándo se cierra.
    `reused=False` significa que este proceso lo ha creado y es responsable
    de cerrarlo él mismo al terminar (`_finish_run`).

    `budget_tokens` sale de `effective_nightly_tokens`, igual que hará T44:
    ya lleva aplicado, si toca, el `reset_day_multiplier` de la noche.
    """
    with unit_of_work(session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        current = runs.current()
        if current is not None:
            return current.id, True
        now = clock.now()
        run = Run(started_at=now, budget_tokens=effective_nightly_tokens(policy, now))
        runs.add(run)
        return run.id, False


def _agent_work_factory(
    session_factory: sessionmaker[Session],
    run_id: UUID,
    policy: BudgetPolicy,
    clock: SystemClock,
) -> AgentWorkFactory:
    """`AgentWorkFactory` (`application/unit_of_work.py`) contra SQLAlchemy real.

    Único sitio del proyecto donde `application/` (a través de `AgentWork`)
    toca SQLAlchemy: cada llamada a la función devuelta abre una
    `unit_of_work` nueva y construye el `BudgetGuard` de `run_id` y los
    cuatro repositorios que necesita un caso de uso de agente (`ReadItem`,
    T41; `PopularizeReading`/`EditNight`, T42/T43 más adelante).
    """

    @contextmanager
    def _open() -> Generator[AgentWork, None, None]:
        with unit_of_work(session_factory) as session:
            runs = SqlAlchemyRunRepository(session)
            agent_calls = SqlAlchemyAgentCallRepository(session)
            guard = BudgetGuard(
                run_id=run_id,
                policy=policy,
                runs=runs,
                agent_calls=agent_calls,
                clock=clock,
            )
            yield AgentWork(
                guard=guard,
                runs=runs,
                items=SqlAlchemyItemRepository(session),
                readings=SqlAlchemyReadingRepository(session),
                agent_calls=agent_calls,
            )

    return _open


def _finish_run(
    session_factory: sessionmaker[Session], run_id: UUID, status: RunStatus, at: datetime
) -> None:
    """Cierra el `Run` que este proceso de `run-item` creó.

    Cerrarlo es obligatorio: dejarlo `RUNNING` bloquearía la noche siguiente
    contra el índice único parcial `uq_runs_status_running`
    (`infrastructure/db/models.py`). Nunca deja escapar una excepción
    propia -- un fallo al cerrar se registra con `logging` y no debe
    enmascarar la excepción original que ya estaba en vuelo en el llamador
    (`_run_item`, camino de `except Exception`).
    """
    try:
        with unit_of_work(session_factory) as session:
            runs = SqlAlchemyRunRepository(session)
            run = runs.get(run_id)
            if run is None:
                raise LookupError(f"no existe Run con id={run_id} al intentar cerrarlo")
            run.finish(status, at)
            runs.save(run)
    except Exception:
        _logger.exception(
            "no se pudo cerrar el Run %s como %s; quedará RUNNING y bloqueará la próxima "
            "noche (uq_runs_status_running); requiere intervención manual",
            run_id,
            status.value,
        )


def _print_budget_denial(exc: BudgetDenied) -> None:
    print(
        f"BudgetGuard denegó la llamada al Reader: {exc.reason.value} "
        f"(quedan {exc.remaining_tokens} tokens)",
        file=sys.stderr,
    )
    if exc.reason is DenyReason.OUTSIDE_WINDOW:
        print(
            "fuera de la ventana de ejecución (window.start/window.hard_stop en "
            "config/pipeline.toml). No hay ninguna bandera para saltarla; edita esa "
            "sección si necesitas depurar run-item de día.",
            file=sys.stderr,
        )


def _print_read_item_report(
    *, item: Item, result: ReadItemResult, work: AgentWorkFactory, run_id: UUID
) -> None:
    reading = result.reading
    assert reading is not None, "READ solo se reporta cuando ReadItem produjo una Reading"

    with work() as w:
        remaining = w.guard.remaining_for(AgentRole.READER)

    print(f"{item.external_id} · {item.title}")
    print(f"  modelo={reading.model} · prompt={READER_PROMPT_VERSION} · intentos={result.attempts}")
    print(f"  tokens gastados={result.tokens_spent} · tokens restantes para el Reader={remaining}")
    print(f"  interest_score={reading.interest_score} · reading_id={reading.id} · run_id={run_id}")
    print(f"  resumen: {_abstract_preview(reading.summary)}")


def _read_one_item(
    *,
    item: Item,
    work: AgentWorkFactory,
    provider: LLMProvider,
    config: PipelineConfig,
    run_id: UUID,
) -> tuple[int, bool, RunStatus | None]:
    """Ejecuta el Reader sobre `item` y traduce el resultado a
    `(exit_code, had_reading, run_status_override)`.

    Toda la configuración del Reader (modelo, turnos, versión de prompt,
    estimación de coste, intentos) sale de `config`, nunca hardcodeada
    (T41, paso 8).

    `run_status_override` solo se rellena para una denegación de
    `BudgetGuard`: es `terminal_status_for(exc.reason)`, el estado terminal
    que le corresponde al motivo de la denegación -- `KILLED` para
    `OUTSIDE_WINDOW`, `PARTIAL` para el resto (`application/budget.py`).
    `_run_item` lo usa en vez del literal `PARTIAL` a mano para cerrar el
    `Run` que él mismo creó; `None` en cualquier otro camino, para que
    `_run_item` siga decidiendo `COMPLETED`/`PARTIAL` por `had_reading` como
    hasta ahora.
    """
    read_item = ReadItem(
        work=work,
        provider=provider,
        system_prompt=load_prompt("reader"),
        prompt_version=READER_PROMPT_VERSION,
        model=config.models.reader,
        max_turns=config.limits.max_turns_per_agent,
        estimated_tokens=config.budget.reader_estimated_tokens,
        max_attempts=config.limits.max_calls_per_item,
    )
    try:
        result = asyncio.run(read_item(item))
    except InvalidTransition:
        # El ítem existe pero no está en NEW: ya se leyó, descartó, publicó
        # o falló antes. `ReadItem` lo comprueba antes de tocar el
        # presupuesto (ver su docstring), así que no se ha gastado nada.
        print(
            f"el Item {item.external_id} ya está en estado '{item.status.value}'; "
            "no se puede releer",
            file=sys.stderr,
        )
        return 3, False, None
    except BudgetDenied as exc:
        _print_budget_denial(exc)
        return 4, False, terminal_status_for(exc.reason)

    if result.outcome is not ReadOutcome.READ:
        # JSON inválido agotados los reintentos (`max_calls_per_item`), un
        # timeout, un límite de tasa o cualquier otro error del proveedor:
        # `ReadItem` (T41) capturó la excepción y la tradujo aquí a un
        # `outcome` distinto de `READ`, sin dejarla escapar como `LLMError`.
        # El ítem ya se marcó `failed` dentro de `ReadItem` si el motivo fue
        # agotar los reintentos por JSON inválido; en los demás casos queda
        # `NEW` para reintentarse otra noche (ver `ReadItem._record_terminal_failure`).
        print(
            f"lectura fallida para {item.external_id}: {result.outcome.value} "
            f"(intentos={result.attempts}, tokens gastados={result.tokens_spent})",
            file=sys.stderr,
        )
        return 1, False, None

    _print_read_item_report(item=item, result=result, work=work, run_id=run_id)
    return 0, True, None


def _run_item(args: argparse.Namespace) -> int:
    # Import perezoso: un test de T20 comprueba que `claude_agent_sdk` no
    # entra en `sys.modules` durante `run-night --dry-run`; `AgentSDKProvider`
    # solo se necesita en este subcomando, el único que llama de verdad a un
    # agente.
    from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

    settings = Settings()
    config = load_pipeline_config()
    policy = budget_policy_from_config(config)
    clock = system_clock_from_config(config)
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    run_id, run_reused = _current_or_new_run(session_factory, policy, clock)
    if not run_reused:
        # No hay un Run RUNNING vivo (la noche de T44 no está en marcha):
        # esta invocación abre el suyo propio, con un presupuesto de noche
        # completo (`effective_nightly_tokens`). Formalmente pasa por
        # BudgetGuard en cada llamada, pero un bucle de invocaciones de
        # run-item sobre varios ítems NO está acotado por nightly_tokens
        # como conjunto: cada una gasta su propio tope. Ver el docstring del
        # módulo, "Presupuesto de noche entre invocaciones sueltas".
        print(
            f"run-item abre un Run nuevo ({run_id}) con presupuesto de noche completo; "
            "un bucle de invocaciones de run-item no está acotado por nightly_tokens en "
            "conjunto, solo window.hard_stop lo limita. Usa run-night (T44) para un tope "
            "real de noche.",
            file=sys.stderr,
        )
    work = _agent_work_factory(session_factory, run_id, policy, clock)

    exit_code = 1
    had_reading = False
    status_override: RunStatus | None = None
    try:
        with work() as w:
            item = w.items.get(args.item_id)

        if item is None:
            print(f"no existe ningún Item con id={args.item_id}", file=sys.stderr)
            exit_code = 3
        else:
            exit_code, had_reading, status_override = _read_one_item(
                item=item,
                work=work,
                provider=AgentSDKProvider(),
                config=config,
                run_id=run_id,
            )
    except Exception:
        # Camino de salida imprevisto: el Run que este proceso creó no debe
        # quedar RUNNING (bloquearía la noche siguiente). Se cierra como
        # FAILED sin enmascarar la excepción original, que se sigue
        # propagando tal cual.
        if not run_reused:
            _finish_run(session_factory, run_id, RunStatus.FAILED, clock.now())
        raise
    else:
        if not run_reused:
            status = (
                status_override
                if status_override is not None
                else (RunStatus.COMPLETED if had_reading else RunStatus.PARTIAL)
            )
            _finish_run(session_factory, run_id, status, clock.now())

    return exit_code


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "run-item":
        return _run_item(args)
    return _run_night(args)


if __name__ == "__main__":
    sys.exit(main())
