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

Códigos de salida de `run-item`: `0` noche completa (el Editor se ejecutó
-- con o sin publicaciones -- o no había ningún candidato que ofrecerle),
`1` lectura fallida (JSON inválido agotado tras los reintentos, un
`LLMTimeout`/`LLMRateLimited` o cualquier otro `LLMError` del proveedor --
`ReadItem` (T41) captura los tres y los traduce a `ReadItemResult.outcome`;
ninguno llega a `cli.py` como excepción, ver `_read_one_item`), `2` UUID de
`item_id` mal formado (lo produce `argparse` al fallar la conversión de
tipo, no código propio), `3` el ítem no existe o no está en `NEW`, `4`
denegación de `BudgetGuard` con un motivo **distinto** de
`EDITOR_ALREADY_CALLED` (incluida la ventana de ejecución fuera de horario
-- sin ninguna bandera para saltarla, `budget-guard-review` § 1; el `Run`
que esta invocación haya creado se cierra con
`terminal_status_for(exc.reason)`, `KILLED` para `OUTSIDE_WINDOW` y
`PARTIAL` para el resto, nunca un literal a mano; desde T42 puede venir del
Reader o del Popularizer, y desde T43 también del Editor,
`_print_budget_denial` nombra el rol), `5` lectura correcta pero
divulgación fallida (T42: el mismo abanico de `PopularizeOutcome` que el
`1`, pero después de una lectura que sí tuvo éxito -- distingue "no se pudo
leer" de "se leyó pero no se pudo divulgar" sin releer la base), `6` (T43)
el Editor falló habiendo candidatos que ofrecerle -- JSON inválido agotado
tras los reintentos, timeout, límite de tasa, error del proveedor, o
`EDITOR_ALREADY_CALLED` (que **no** pasa por `terminal_status_for`, ver "T43,
paso 5" más abajo) -- cierra `PARTIAL` en los dos casos. El proveedor se
construye sin `mcp_servers` ni `allowed_tools`: inyectar MCP haría que
`query()` emita varios `ResultMessage` y que `AgentSDKProvider` se quede
solo con el gasto del último (`docs/TECHNICAL_DEBT.md`, "obligatorio
revisar antes de que T41 pase `mcp_servers` a ningún rol").

**T42, paso 8: se encadena el Popularizer tras una lectura con éxito.** La
`Reading` se persiste y se confirma en su propia unidad de trabajo, dentro
de `ReadItem.__call__`, antes de que `_read_one_item` invoque siquiera
`PopularizeReading`: ningún fallo del Popularizer (`BudgetDenied` incluido)
puede perder una `Reading` ya escrita.

**T43, paso 5: se encadena el Editor tras el Popularizer.** Es la única
forma de ejercitar el Editor antes del orquestador nocturno (T44). Los dos
caminos de éxito de `_popularize_one_reading` -- divulgado, y descartado
por `interest_score` bajo -- dejan de decidir `COMPLETED` por su cuenta:
delegan el `(exit_code, run_status)` entero en la nueva `_edit_one_night`,
que pasa a ser la única función de este módulo que puede devolver
`RunStatus.COMPLETED` (salda la deuda anotada en `docs/TECHNICAL_DEBT.md`,
T42, "el arreglo del Run resiste a medias": antes de este paso esos dos
`return` eran correctos solo porque el Popularizer era la última etapa).
Sin candidatos (`EditOutcome.NO_CANDIDATES` -- el caso "descartado por score
bajo", que nunca deja un `Finding` que ofrecer), `EditNight` no autoriza
nada, coste cero, y la noche cierra `COMPLETED` igual: encaja solo con el
mismo patrón. `max_attempts` del Editor se ata a
`limits.max_editor_calls_per_night` (`config/pipeline.toml`), para que el
bucle de reintento de `AgentRunner` y el tope de `BudgetGuard` nunca puedan
discrepar entre sí -- un segundo intento pasa por su propio `authorize`,
que ve `1 < 2` y autoriza; un tercero sería denegado con
`EDITOR_ALREADY_CALLED`. Ese motivo es el único de `BudgetDenied` que **no**
pasa por `terminal_status_for`: esa función lanza `ValueError` a propósito
para él (`application/budget.py`, docstring de `terminal_status_for`)
porque "el Editor ya agotó sus intentos" es un fin de noche normal, no una
anomalía que deba forzar el cierre del Run desde ahí -- es responsabilidad
de quien orquesta, aquí en `_edit_one_night`, distinguirlo antes de llamar
a esa función. Cierra la decisión abierta nº 72 de
`docs/OPEN_DECISIONS.md`: el arreglo va en el sitio de llamada, no en
`budget.py`.

**T44, paso 3: `run-night` sin `--dry-run` ejecuta la noche completa.**
`_run_night` es ahora solo un despachador entre `_run_night_dry_run` (el
camino de siempre, con la ingesta y el plan de gasto) y
`_run_night_for_real`, que instala `configure_json_logging()` (la única
llamada de todo el proceso, `infrastructure/logging.py`); construye, en
este orden, todo lo que puede fallar por sí solo, antes de comprometerse
con ningún `Run` -- `AgentSDKProvider()` (puede lanzar
`ApiKeyInEnvironment`), los tres prompts (`load_prompt`, puede lanzar
`FileNotFoundError`) y `deadline_s`/`deadline_reason`
(`_deadline_for_run_night`, ver más abajo) --; solo entonces adopta o
cierra un `Run` huérfano y crea el de esta noche
(`_current_or_new_run_night`, ver más abajo); construye los tres casos de
uso ya resueltos -- `ReadItem`, `PopularizeReading`, `EditNight`, cada uno
con su modelo/prompt/turnos/estimación de coste leídos de
`config/pipeline.toml`, nunca hardcodeados -- y con ellos `RunNight`
(`application/use_cases/run_night.py`, T44 paso 2, vía `_build_run_night`),
y lo ejecuta en un único `asyncio.run`. `RunNight` no cierra el `Run` ni
decide `Run.status` (ver su docstring): ese reparto de responsabilidades
sigue siendo de `cli.py`, el composition root, igual que en `run-item`.

Revisión de T44, punto 3: antes de este orden, `_current_or_new_run_night`
iba primero y `AgentSDKProvider()`/`load_prompt(...)` venían después,
fuera de cualquier `try`. Un fichero de prompt ausente (o
`ApiKeyInEnvironment`) dejaba un `Run` `RUNNING` colgado -- y, mientras
tanto, expuesto a que un `run-item` intermedio lo **adoptara** y gastara
contra su presupuesto. La construcción de `ReadItem`/`PopularizeReading`/
`EditNight` sí sigue yendo después de abrir el `Run` -- los tres exigen
`work` (`AgentWorkFactory`), que solo existe una vez que `run_id` existe
-- pero eso no importa: ninguno de los tres hace nada que pueda fallar en
su constructor (asignación de atributos), a diferencia de `AgentSDKProvider()`
y `load_prompt(...)`, que sí se han adelantado.

Códigos de salida de `run-night` sin `--dry-run` (los de `run-item`, 1–6,
no cambian): `0` `RunNightResult.status is COMPLETED`, `7` `PARTIAL`, `8`
`KILLED` (`_EXIT_CODE_BY_RUN_STATUS`), `1` una excepción escapó de
`RunNight` -- se cierra el `Run` como `FAILED` en `_run_night_for_real`,
con el traceback completo en el log JSON, sin enmascarar la excepción ni
relanzarla (ver el docstring de esa función).

`deadline_s`, el presupuesto de tiempo que `RunNight` reparte entre su
vigía de `hard_stop` y su cuerpo, sale de `_deadline_for_run_night(policy,
clock.now())`: `min(seconds_until_hard_stop(...), policy.run_timeout_s)`
-- calculado en `cli.py`, no en `RunNight` (que solo conoce segundos,
nunca horas de pared, ver su docstring): la primera vez que
`limits.run_timeout_s` tiene un consumidor real. Esa misma función
devuelve `deadline_reason` (`"hard_stop"` o `"run_timeout"`, según cuál de
los dos mandó), que viaja hasta `RunNight` para que `notes` diga cuál de
los dos cortó la noche si el vigía dispara (revisión de T44, punto 2: con
`run_timeout_s = 16_200` s y la ventana completa en 17_100 s, antes de
esta revisión una noche que arrancara a las 00:00 la cortaba el timeout de
ejecución pero `notes` decía `end=hard_stop` igual). `Run.budget_tokens`
se fija con `effective_nightly_tokens(policy, clock.now())`
(`_current_or_new_run_night`), nunca con `policy.nightly_tokens` a pelo ni
con un literal -- cierra la decisión abierta nº 57 de
`docs/OPEN_DECISIONS.md` por la opción (a), con un test que lo congela.

**`Run` huérfano al arrancar `run-night`.** A diferencia de `run-item`
(que adopta un `Run` `RUNNING` vivo porque puede ser una noche de verdad
en marcha), `run-night` nunca deja tras de sí un `Run` `RUNNING` a medio
camino -- su propio cierre pasa siempre por `_finish_run` --, así que
cualquier `RUNNING` que `_current_or_new_run_night` encuentre al arrancar
es, por construcción, el rastro de un proceso anterior que murió sin
cerrarlo. Se cierra como `KILLED` con una nota explícita y un `warning`
ruidoso, y se abre un `Run` nuevo a continuación: resuelve la decisión
abierta nº 20 de `docs/OPEN_DECISIONS.md` para `run-night` (`run-item` no
cambia) y salda, para este camino, la deuda de T41 sobre
`uq_runs_status_running`.

**`EditNight.run_id` frente al `run_id` del `BudgetGuard` de `work`.**
`EditNight` (T43) no valida por sí sola que el `run_id` que recibe en
`__call__` coincide con el Run que vigila el guard de la unidad de trabajo
que usa (`docs/TECHNICAL_DEBT.md`, T43: "`EditNight(run_id)` desacoplada
del `BudgetGuard`"). Con `RunNight` como segundo llamante de `EditNight`
-- junto a `_edit_one_night`, el camino de `run-item` --, `_build_run_night`
es el único punto del proyecto donde a la vez se conoce el `run_id` con el
que se construyó ese `BudgetGuard` y el `run_id` que `RunNight` reenviará
a `EditNight.__call__`; ahí vive la comprobación explícita, con
`ValueError` si divergen (ver su docstring).

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
from collections.abc import Awaitable, Callable, Generator, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.orm import Session, sessionmaker

from nocturna.application.agents.prompt_loader import (
    EDITOR_PROMPT_VERSION,
    POPULARIZER_PROMPT_VERSION,
    READER_PROMPT_VERSION,
    load_prompt,
)
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
from nocturna.application.use_cases.edit_night import EditNight, EditNightResult, EditOutcome
from nocturna.application.use_cases.ingest_arxiv import IngestArxiv, IngestResult
from nocturna.application.use_cases.popularize_reading import (
    PopularizeOutcome,
    PopularizeReading,
    PopularizeResult,
)
from nocturna.application.use_cases.read_item import ReadItem, ReadOutcome
from nocturna.application.use_cases.run_night import RunNight, RunNightResult
from nocturna.domain.clock import Clock
from nocturna.domain.entities import Item, Reading, Run, RunStatus
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
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
    unit_of_work,
)
from nocturna.infrastructure.logging import configure_json_logging

# `AgentSDKProvider` (infrastructure/llm/agent_sdk_provider.py) NO se importa
# aquí arriba: un test de T20 comprueba que `claude_agent_sdk` no entra en
# `sys.modules` durante `run-night --dry-run`, y un import a nivel de módulo
# lo rompería. `_run_item` y `_run_night_for_real` lo importan dentro de su
# propio cuerpo, los dos únicos caminos que de verdad lo necesitan.

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

#: Traduce `RunNightResult.status` (T44, paso 2) al código de salida de
#: `run-night` sin `--dry-run` (T44, paso 3; ver el docstring del módulo).
#: `RunStatus.FAILED` no aparece aquí a propósito: `RunNight` nunca lo
#: propone (`application/use_cases/run_night.py`, `_STATUS_RANK` solo cubre
#: `COMPLETED`/`PARTIAL`/`KILLED`) -- `FAILED` lo decide `_run_night_for_real`
#: directamente cuando una excepción escapa de `RunNight`, sin pasar por
#: esta tabla (ver "T44, paso 3" en el docstring del módulo).
_EXIT_CODE_BY_RUN_STATUS: dict[RunStatus, int] = {
    RunStatus.COMPLETED: 0,
    RunStatus.PARTIAL: 7,
    RunStatus.KILLED: 8,
}

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
        editor_timeout_s=config.limits.editor_timeout_s,
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


def _deadline_for_run_night(policy: BudgetPolicy, now: datetime) -> tuple[int, str]:
    """Calcula `deadline_s` para `RunNight` y qué lo produjo.

    `deadline_s = min(seconds_until_hard_stop(now, ...), policy.run_timeout_s)`
    -- la misma fórmula desde que T44 le dio su primer consumidor a
    `limits.run_timeout_s` --, pero devuelta junto con la etiqueta del
    motivo que mandó, para que `RunNight._build_killed_result` pueda
    escribir en `notes` cuál de los dos cortó la noche si el vigía dispara
    (revisión de T44, punto 2): `"hard_stop"` si queda menos tiempo hasta
    `window.hard_stop` que `run_timeout_s` (el caso real de una noche que
    arranca tarde, o que arranca a tiempo con `run_timeout_s` mayor o
    igual que la ventana completa), `"run_timeout"` si `run_timeout_s` es
    estrictamente menor que lo que queda de ventana (el caso real de hoy:
    `run_timeout_s = 16_200` s, `window.start`/`window.hard_stop` = 4h45 =
    17_100 s -- una noche que arranca a las 00:00 la corta el timeout de
    ejecución, no la ventana, y `notes` debe decirlo así, no `hard_stop`).

    Extraída a una función de nivel de módulo, en vez de quedar inline en
    `_run_night_for_real`, precisamente para poder congelar esta distinción
    con un test que no necesite construir el resto del composition root
    (`Settings`, motor de base de datos...); antes de esta revisión, la
    fórmula sin la etiqueta sí se caracterizaba de forma aislada
    (`test_run_night_hard_stop.py`, test 19) pero la etiqueta no se
    comprobaba en ningún sitio.
    """
    seconds_left = seconds_until_hard_stop(now, policy.window_start, policy.window_hard_stop)
    if seconds_left <= policy.run_timeout_s:
        return seconds_left, "hard_stop"
    return policy.run_timeout_s, "run_timeout"


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
            "Ejecuta la noche de análisis: ingesta de arXiv, Reader, Popularizer y Editor, "
            "en ese orden y dentro del presupuesto de la noche (--dry-run se queda solo en "
            "la ingesta y el plan de gasto, sin llamar a ningún agente)."
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


def _would_read_count(config: PipelineConfig, settings: Settings) -> int:
    """Cuántos ítems leería el Reader esta noche
    (`ItemRepository.next_unread(limits.max_items_per_night)`), lectura pura
    y sin ningún agente: parte del plan de gasto de `--dry-run` (T44, paso
    8). Distinto de `IngestResult.new` -- que solo cuenta lo ingerido *esta*
    noche --: incluye también la cola acumulada de noches anteriores
    (`docs/OPEN_DECISIONS.md`, T20/T44, "los ítems new que nunca se leen se
    acumulan"). Abre su propio motor/sesión, igual que `_run_ingest`: no hay
    ningún motor compartido entre las piezas de `--dry-run`.
    """
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)
    with unit_of_work(session_factory) as session:
        items = SqlAlchemyItemRepository(session)
        return len(items.next_unread(config.limits.max_items_per_night))


def _print_budget_plan(
    policy: BudgetPolicy, timezone: str, now: datetime, *, would_read: int
) -> None:
    """Plan de gasto de la noche: lo que `CLAUDE.md` pide de `--dry-run`
    ("ingesta + plan de gasto, sin llamar a agentes"), sin instanciar el
    guarda de gasto de `application/` (necesita un `Run` que en `--dry-run`
    no existe) ni tocar la base de datos por su cuenta. Solo usa las
    funciones puras de `application/budget.py` (incluida
    `seconds_until_hard_stop`, la misma que usa
    `BudgetGuard.seconds_until_hard_stop`; no hay una segunda copia de ese
    cálculo en este módulo) y la hora del `SystemClock`. `would_read` es la
    única pieza que sí exige una lectura de base de datos
    (`_would_read_count`, T44 paso 8): se calcula fuera y se recibe ya
    resuelta para que esta función se mantenga pura y determinista.
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
    print(f"  ítems que se leerían esta noche: {would_read}")
    print(
        f"  ventana configurada: {policy.window_start.isoformat()}–"
        f"{policy.window_hard_stop.isoformat()} ({timezone})"
    )
    if within_window:
        print(f"  dentro de la ventana ahora mismo: sí (quedan {seconds_left} s para el hard_stop)")
    else:
        print("  dentro de la ventana ahora mismo: no")


def _current_or_new_run_night(
    session_factory: sessionmaker[Session], policy: BudgetPolicy, clock: SystemClock
) -> UUID:
    """Abre el `Run` de la noche para `run-night` (T44, paso 3).

    A diferencia de `_current_or_new_run` (`run-item`, T41), que reutiliza
    un `Run` `RUNNING` porque puede ser una noche de verdad en marcha (el
    camino de depuración que T41 exige preservar), `run-night` nunca deja
    tras de sí un `Run` `RUNNING` a medio camino -- su propio cierre pasa
    siempre por `_finish_run` (paso 3 de esta tarea) --, así que cualquier
    `RUNNING` que `runs.current()` encuentre al arrancar es, por
    construcción, el rastro de un proceso anterior que murió sin cerrarlo:
    un huérfano. Se cierra como `KILLED`, con
    `notes="cerrado por run-night: quedó RUNNING de un proceso anterior"` y
    un `warning` ruidoso en el log JSON (`configure_json_logging`, ya
    instalado por `_run_night_for_real` antes de llegar aquí), y se abre un
    `Run` nuevo a continuación -- resuelve la decisión abierta nº 20 de
    `docs/OPEN_DECISIONS.md` para este camino (no para `run-item`, que sigue
    igual) y salda, para `run-night`, la deuda de T41 sobre
    `uq_runs_status_running`.

    `budget_tokens` del `Run` nuevo sale de `effective_nightly_tokens`,
    nunca de `policy.nightly_tokens` a pelo ni de un literal: cierra la
    decisión abierta nº 57 de `docs/OPEN_DECISIONS.md` por la opción (a) (un
    test lo congela).

    Esta función **siempre** abre un presupuesto de noche completo -- a
    diferencia de `_current_or_new_run` (`run-item`), nunca reutiliza un
    `Run` `RUNNING` vivo -- así que es, junto a `_run_item`, el único
    camino por el que una noche puede gastar 2 × `nightly_tokens`: dos
    invocaciones de `run-night` en la misma ventana (un reintento manual
    tras una noche `PARTIAL`, una unidad systemd con `Restart=`, un cron
    mal puesto) abren dos `Run` de presupuesto completo cada uno, sin que
    nada lo note (revisión de T44, punto 5; ver también el docstring del
    módulo, "Presupuesto de noche entre invocaciones sueltas de
    run-item"). Se avisa por `stderr`, igual que `_run_item` avisa cada
    vez que abre un `Run` nuevo; no se implementa ningún límite duro
    nuevo aquí -- si hay que impedir reabrir presupuesto completo dentro
    de la misma ventana, es una decisión de diseño para el autor.
    """
    now = clock.now()
    with unit_of_work(session_factory) as session:
        runs = SqlAlchemyRunRepository(session)
        orphan = runs.current()
        if orphan is not None:
            orphan.finish(
                RunStatus.KILLED,
                now,
                notes="cerrado por run-night: quedó RUNNING de un proceso anterior",
            )
            runs.save(orphan)
            # `uq_runs_status_running` (índice único parcial, T41) solo
            # admite una fila `running` a la vez. `Session.flush()` no
            # garantiza que las filas no relacionadas se escriban en el
            # orden en que se tocaron en Python: sin este `flush()`
            # explícito, el `INSERT` del Run nuevo de abajo podría llegar a
            # la base antes que el `UPDATE` que cierra el huérfano, violando
            # el índice dentro de la misma transacción.
            session.flush()
            _logger.warning(
                "run.orphan_closed",
                extra={
                    "event": "run.orphan_closed",
                    "run_id": str(orphan.id),
                    "started_at": orphan.started_at.isoformat(),
                },
            )
        run = Run(started_at=now, budget_tokens=effective_nightly_tokens(policy, now))
        runs.add(run)
        run_id = run.id
        budget_tokens = run.budget_tokens

    print(
        f"run-night abre un Run nuevo ({run_id}) con presupuesto de noche completo "
        f"({budget_tokens} tokens); dos invocaciones de run-night en la misma ventana "
        "(un reintento manual, una unidad systemd con Restart=, un cron mal puesto) "
        "gastan cada una su propio nightly_tokens, sin que nada lo note -- solo "
        "window.hard_stop las acota.",
        file=sys.stderr,
    )
    return run_id


def _build_run_night(
    *,
    work: AgentWorkFactory,
    work_run_id: UUID,
    clock: Clock,
    ingest: Callable[[], Awaitable[IngestResult]],
    read_item: ReadItem,
    popularize: PopularizeReading,
    edit_night: EditNight,
    run_id: UUID,
    max_items: int,
    max_consecutive_failures: int,
    deadline_s: int,
    deadline_reason: str = "hard_stop",
) -> RunNight:
    """Construye `RunNight`, comprobando explícitamente que `run_id` -- el
    que `RunNight` reenviará a `EditNight.__call__` -- coincide con
    `work_run_id`, el `run_id` con el que se construyó el `BudgetGuard` de
    `work` (el argumento que recibió `_agent_work_factory`).

    Cierra la deuda de T43 (`docs/TECHNICAL_DEBT.md`, "`EditNight(run_id)`
    desacoplada del `BudgetGuard`"): `EditNight` no valida por sí sola que
    el `run_id` que recibe en `__call__` coincide con el Run que vigila el
    guard de la unidad de trabajo que usa. Con `RunNight` como segundo
    llamante de `EditNight` -- junto a `cli.py::_edit_one_night`, el camino
    de `run-item` --, esta función es el único punto del proyecto donde a
    la vez se conocen ambos valores, así que la comprobación va aquí. Hoy
    `work_run_id` y `run_id` son siempre la misma variable en el único
    sitio que llama a esta función (`_run_night_for_real`); el aserto
    documenta la invariante en vez de confiar en que dos parámetros con el
    mismo valor no diverjan nunca, y protege sobre todo contra un futuro
    cambio en la gestión de `Run` huérfanos (`_current_or_new_run_night`)
    que sí maneja dos UUID distintos a la vez (el huérfano que cierra, el
    nuevo que abre).

    `deadline_reason` (por defecto `"hard_stop"`, revisión de T44 punto 2)
    se reenvía tal cual a `RunNight`: es la etiqueta que
    `_build_killed_result` fuerza en `notes` si el vigía dispara --
    `"run_timeout"` cuando `_deadline_for_run_night` decidió que mandaba
    `limits.run_timeout_s` en vez de la ventana de ejecución. `RunNight` no
    ve `BudgetPolicy` y no puede decidirlo por su cuenta.
    """
    if work_run_id != run_id:
        raise ValueError(
            f"run_id de RunNight ({run_id}) no coincide con el run_id del BudgetGuard de "
            f"work ({work_run_id})"
        )
    return RunNight(
        work=work,
        clock=clock,
        ingest=ingest,
        read_item=read_item,
        popularize=popularize,
        edit_night=edit_night,
        run_id=run_id,
        max_items=max_items,
        max_consecutive_failures=max_consecutive_failures,
        deadline_s=deadline_s,
        deadline_reason=deadline_reason,
    )


def _print_run_night_report(result: RunNightResult) -> None:
    """Informe humano de la noche completa, por `stdout` -- igual que el
    resto de informes de este módulo (`_print_dry_run_report`,
    `_print_read_item_report`...): la distinción `stdout`/`stderr` de este
    módulo es informe legible frente a telemetría estructurada
    (`docs/OPEN_DECISIONS.md`, T20, nº 42), no `run-item` frente a
    `run-night`.
    """
    print()
    print("Resumen de la noche:")
    print(f"  estado: {result.status.value}")
    print(
        f"  ítems: ingeridos={result.items_fetched} leídos={result.items_read} "
        f"fallidos={result.items_failed}"
    )
    print(f"  candidatos={result.candidates} publicados={result.findings_published}")
    print(f"  tokens gastados={result.tokens_used}")
    print(f"  notas: {result.notes}")


def _run_night(args: argparse.Namespace) -> int:
    if args.dry_run:
        return _run_night_dry_run(args)
    return _run_night_for_real(args)


def _run_night_dry_run(args: argparse.Namespace) -> int:
    """Ingesta real de arXiv, persistida, más el plan de gasto -- sin llamar
    a ningún agente ni instanciar `RunNight`. Comportamiento sin cambios
    respecto a antes de T44 salvo la línea nueva de `_would_read_count` en
    el plan de gasto (T44, paso 8)."""
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
    would_read = _would_read_count(config, settings)
    _print_budget_plan(policy, config.window.timezone, clock.now(), would_read=would_read)

    return 0


def _run_night_for_real(args: argparse.Namespace) -> int:
    """Ejecuta la noche completa: ingesta, Reader, Popularizer, Editor, en
    ese orden (T44, paso 3; ver el docstring del módulo, "T44, paso 3").

    Códigos de salida (los de `run-item`, 1–6, no cambian): `0`
    `RunNightResult.status is COMPLETED`, `7` `PARTIAL`, `8` `KILLED`
    (`_EXIT_CODE_BY_RUN_STATUS`), `1` una excepción escapó de `RunNight` --
    se cierra el `Run` como `FAILED` aquí mismo, con el traceback completo
    en el log JSON (`_logger.exception`, nunca silenciado) antes de
    devolver el código, sin enmascarar la excepción original (no se
    relanza: un cron nocturno no debe terminar en un traceback crudo sin
    que quede registro estructurado de qué pasó).
    """
    # Import perezoso, igual que en `_run_item`: un test de T20 comprueba
    # que `claude_agent_sdk` no entra en `sys.modules` durante `--dry-run`,
    # y este es el único camino de `run-night` que de verdad llama a un
    # agente.
    from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

    configure_json_logging()

    settings = Settings()
    config = load_pipeline_config()
    policy = budget_policy_from_config(config)
    clock = system_clock_from_config(config)
    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    since = args.since if args.since is not None else _default_since(datetime.now(UTC))
    categories = args.categories if args.categories is not None else config.sources.arxiv.categories

    # Todo lo que puede fallar por sí solo, construido ANTES de abrir
    # ningún `Run` (revisión de T44, punto 3): `AgentSDKProvider()` puede
    # lanzar `ApiKeyInEnvironment`, `load_prompt` puede lanzar
    # `FileNotFoundError` si falta un fichero en
    # `application/agents/prompts/`. Antes de este orden, `_current_or_new_run_night`
    # iba primero, y un fallo aquí dejaba un `Run` `RUNNING` colgado --
    # bloqueando la noche siguiente y expuesto, mientras tanto, a que un
    # `run-item` intermedio lo adoptara y gastara contra su presupuesto.
    provider = AgentSDKProvider()
    reader_prompt = load_prompt("reader")
    popularizer_prompt = load_prompt("popularizer")
    editor_prompt = load_prompt("editor")
    deadline_s, deadline_reason = _deadline_for_run_night(policy, clock.now())

    run_id = _current_or_new_run_night(session_factory, policy, clock)
    work = _agent_work_factory(session_factory, run_id, policy, clock)

    read_item = ReadItem(
        work=work,
        provider=provider,
        system_prompt=reader_prompt,
        prompt_version=READER_PROMPT_VERSION,
        model=config.models.reader,
        max_turns=config.limits.max_turns_per_agent,
        estimated_tokens=config.budget.reader_estimated_tokens,
        max_attempts=config.limits.max_calls_per_item,
    )
    popularize = PopularizeReading(
        work=work,
        provider=provider,
        system_prompt=popularizer_prompt,
        prompt_version=POPULARIZER_PROMPT_VERSION,
        model=config.models.popularizer,
        max_turns=config.limits.max_turns_per_agent,
        estimated_tokens=config.budget.popularizer_estimated_tokens,
        max_attempts=config.limits.max_calls_per_item,
        min_interest_score=config.limits.popularizer_min_interest_score,
    )
    edit_night = EditNight(
        work=work,
        provider=provider,
        clock=clock,
        system_prompt=editor_prompt,
        prompt_version=EDITOR_PROMPT_VERSION,
        model=config.models.editor,
        max_turns=config.limits.max_turns_per_agent,
        max_attempts=config.limits.max_editor_calls_per_night,
        base_tokens=config.budget.editor_base_tokens,
        tokens_per_candidate=config.budget.editor_tokens_per_candidate,
    )

    async def _ingest() -> IngestResult:
        return await _run_ingest(
            since=since, categories=categories, config=config, settings=settings
        )

    night = _build_run_night(
        work=work,
        work_run_id=run_id,
        clock=clock,
        ingest=_ingest,
        read_item=read_item,
        popularize=popularize,
        edit_night=edit_night,
        run_id=run_id,
        max_items=config.limits.max_items_per_night,
        max_consecutive_failures=config.limits.max_consecutive_failures,
        deadline_s=deadline_s,
        deadline_reason=deadline_reason,
    )

    try:
        result = asyncio.run(night())
    except Exception:
        # Ninguna excepción de `RunNight` debe dejar el Run `RUNNING`
        # bloqueando la noche siguiente. Se cierra `FAILED` aquí mismo, con
        # el traceback completo en el log JSON -- sin enmascarar la
        # excepción original, que queda íntegra en `exc_info` -- y sin
        # relanzarla: a diferencia de `_run_item` (camino interactivo de
        # depuración, donde un traceback en la terminal es aceptable),
        # `run-night` es un proceso de cron nocturno que debe terminar con
        # un código de salida decidido y un registro estructurado, nunca
        # con una traza cruda sin cerrar el Run.
        _logger.exception(
            "night.failed",
            extra={"event": "night.failed", "run_id": str(run_id)},
        )
        _finish_run(session_factory, run_id, RunStatus.FAILED, clock.now())
        return 1

    _finish_run(
        session_factory,
        run_id,
        result.status,
        clock.now(),
        notes=result.notes,
        items_fetched=result.items_fetched,
        items_read=result.items_read,
        findings_published=result.findings_published,
    )
    _print_run_night_report(result)
    return _EXIT_CODE_BY_RUN_STATUS[result.status]


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
    cinco repositorios que necesita un caso de uso de agente (`ReadItem`,
    T41; `PopularizeReading`, T42; `EditNight`, T43).
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
                findings=SqlAlchemyFindingRepository(session),
                agent_calls=agent_calls,
            )

    return _open


def _finish_run(
    session_factory: sessionmaker[Session],
    run_id: UUID,
    status: RunStatus,
    at: datetime,
    *,
    notes: str | None = None,
    items_fetched: int | None = None,
    items_read: int | None = None,
    findings_published: int | None = None,
) -> None:
    """Cierra el `Run` que este proceso creó, de `run-item` o de `run-night`.

    Cerrarlo es obligatorio: dejarlo `RUNNING` bloquearía la noche siguiente
    contra el índice único parcial `uq_runs_status_running`
    (`infrastructure/db/models.py`). Nunca deja escapar una excepción
    propia -- un fallo al cerrar se registra con `logging` y no debe
    enmascarar la excepción original que ya estaba en vuelo en el llamador
    (`_run_item`/`_run_night_for_real`, camino de `except Exception`).

    `notes`/`items_fetched`/`items_read`/`findings_published` son `None`
    por defecto: el camino de `run-item` (T41) no los toca, exactamente
    igual que antes de T44. Cuando se pasan (`_run_night_for_real`, T44
    paso 3), escriben los contadores monótonos y las notas de
    `RunNightResult` que `RunNight` deja sin tocar a propósito (ver su
    docstring, "Lo que este módulo NO hace") antes de cerrar el `Run` con
    `Run.finish()`. `Run.finish(..., notes=None)` no toca `Run.notes` (ver
    `domain/entities.py`), así que pasar `notes=None` desde `run-item`
    reproduce exactamente el comportamiento de antes de este cambio.
    """
    try:
        with unit_of_work(session_factory) as session:
            runs = SqlAlchemyRunRepository(session)
            run = runs.get(run_id)
            if run is None:
                raise LookupError(f"no existe Run con id={run_id} al intentar cerrarlo")
            if items_fetched is not None:
                run.items_fetched = items_fetched
            if items_read is not None:
                run.items_read = items_read
            if findings_published is not None:
                run.findings_published = findings_published
            run.finish(status, at, notes=notes)
            runs.save(run)
    except Exception:
        _logger.exception(
            "no se pudo cerrar el Run %s como %s; quedará RUNNING y bloqueará la próxima "
            "noche (uq_runs_status_running); requiere intervención manual",
            run_id,
            status.value,
        )


def _print_budget_denial(exc: BudgetDenied, *, role: str) -> None:
    print(
        f"BudgetGuard denegó la llamada al {role}: {exc.reason.value} "
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
    *,
    item: Item,
    reading: Reading,
    attempts: int,
    tokens_spent: int,
    work: AgentWorkFactory,
    run_id: UUID,
) -> None:
    """Recibe `reading` ya estrechada por el llamador (`_read_one_item`), en
    vez de un `ReadItemResult` con `reading: Reading | None` -- así no hace
    falta un segundo `assert`/comprobación redundante aquí para lo mismo que
    ya comprobó `_read_one_item` antes de llamar."""
    with work() as w:
        remaining = w.guard.remaining_for(AgentRole.READER)

    print(f"{item.external_id} · {item.title}")
    print(f"  modelo={reading.model} · prompt={READER_PROMPT_VERSION} · intentos={attempts}")
    print(f"  tokens gastados={tokens_spent} · tokens restantes para el Reader={remaining}")
    print(f"  interest_score={reading.interest_score} · reading_id={reading.id} · run_id={run_id}")
    print(f"  resumen: {_abstract_preview(reading.summary)}")


def _print_popularize_report(
    *, result: PopularizeResult, work: AgentWorkFactory, config: PipelineConfig
) -> None:
    finding = result.finding
    assert finding is not None, (
        "POPULARIZED solo se reporta cuando PopularizeReading produjo un Finding"
    )

    with work() as w:
        remaining = w.guard.remaining_for(AgentRole.POPULARIZER)

    print(
        f"  Popularizer: modelo={config.models.popularizer} · "
        f"prompt={POPULARIZER_PROMPT_VERSION} · intentos={result.attempts}"
    )
    print(
        f"  tokens gastados={result.tokens_spent} · "
        f"tokens restantes para el Popularizer={remaining}"
    )
    print(f"  finding_id={finding.id} · sin publicar (confidence=None, published_at=None)")
    print(f"  titular: {finding.title}")
    print(f"  nivel curioso: {_abstract_preview(finding.level_curious)}")


def _popularize_one_reading(
    *,
    item: Item,
    reading: Reading,
    work: AgentWorkFactory,
    provider: LLMProvider,
    config: PipelineConfig,
    clock: Clock,
    run_id: UUID,
) -> tuple[int, RunStatus]:
    """Ejecuta el Popularizer sobre `reading` y traduce el resultado a
    `(exit_code, run_status)`.

    `run_status` es, en el mismo `return` que fija `exit_code`, el estado
    terminal con el que `_run_item` debe cerrar el `Run` que él mismo haya
    creado -- nunca un valor que el llamador tenga que inferir después a
    partir de si hubo o no una `Reading` (ver el docstring de
    `_read_one_item` para el porqué). `4` si `BudgetGuard` deniega la
    llamada al Popularizer (`terminal_status_for(exc.reason)`, igual que en
    el Reader), `5` para cualquier otro desenlace que no sea `POPULARIZED`
    (JSON inválido agotados los reintentos, timeout, límite de tasa o error
    del proveedor) -- ese `5` cierra `PARTIAL`: el Reader tuvo éxito pero el
    ciclo completo del ítem no, y `Run.status` no debe decir lo contrario
    (bug de T42 fijado por
    `test_popularizer_fallido_devuelve_5_deja_la_reading_intacta`). Los dos
    desenlaces que SÍ son éxito -- se divulgó, o se descartó por
    `interest_score` bajo -- ya no deciden `COMPLETED` por su cuenta (T43,
    paso 5): ninguno de los dos es un fallo, pero ambos delegan el
    `(exit_code, run_status)` entero en `_edit_one_night`, la única función
    de este módulo que puede devolver `RunStatus.COMPLETED` (ver el
    docstring del módulo, "T43, paso 5"). Toda la configuración del
    Popularizer (modelo, turnos, versión de prompt, estimación de coste,
    intentos, umbral) sale de `config`, nunca hardcodeada (T42, paso 8).
    """
    popularize = PopularizeReading(
        work=work,
        provider=provider,
        system_prompt=load_prompt("popularizer"),
        prompt_version=POPULARIZER_PROMPT_VERSION,
        model=config.models.popularizer,
        max_turns=config.limits.max_turns_per_agent,
        estimated_tokens=config.budget.popularizer_estimated_tokens,
        max_attempts=config.limits.max_calls_per_item,
        min_interest_score=config.limits.popularizer_min_interest_score,
    )
    try:
        result = asyncio.run(popularize(item=item, reading=reading))
    except BudgetDenied as exc:
        _print_budget_denial(exc, role="Popularizer")
        return 4, terminal_status_for(exc.reason)

    if result.outcome is PopularizeOutcome.SKIPPED_LOW_SCORE:
        print(
            f"descartado: interest_score={reading.interest_score} < "
            f"umbral={config.limits.popularizer_min_interest_score} "
            "(no se llama al Popularizer)"
        )
        return _edit_one_night(
            work=work, provider=provider, config=config, clock=clock, run_id=run_id
        )

    if result.outcome is not PopularizeOutcome.POPULARIZED:
        # JSON inválido agotados los reintentos, un timeout, un límite de
        # tasa o cualquier otro error del proveedor: `PopularizeReading`
        # (T42) capturó la excepción y la tradujo aquí a un `outcome`
        # distinto de `POPULARIZED`, sin dejarla escapar. El ítem ya se
        # descartó dentro de `PopularizeReading` si el motivo fue agotar los
        # reintentos por JSON inválido; en los demás casos queda `READ` para
        # reintentarse otra noche.
        print(
            f"divulgación fallida para {item.external_id}: {result.outcome.value} "
            f"(intentos={result.attempts}, tokens gastados={result.tokens_spent})",
            file=sys.stderr,
        )
        return 5, RunStatus.PARTIAL

    _print_popularize_report(result=result, work=work, config=config)
    return _edit_one_night(work=work, provider=provider, config=config, clock=clock, run_id=run_id)


def _print_edit_night_report(
    *, result: EditNightResult, work: AgentWorkFactory, config: PipelineConfig
) -> None:
    with work() as w:
        remaining = w.guard.remaining_for(AgentRole.EDITOR)

    print(
        f"  Editor: modelo={config.models.editor} · prompt={EDITOR_PROMPT_VERSION} · "
        f"intentos={result.attempts}"
    )
    print(f"  tokens gastados={result.tokens_spent} · tokens restantes para el Editor={remaining}")
    print(
        f"  candidatos={result.candidates} · publicados={len(result.published)} · "
        f"descartados={len(result.discarded)}"
    )
    for finding in result.published:
        reason = result.reasons.get(finding.item_id, "")
        print(
            f"    publicado: {finding.title} "
            f"(finding_id={finding.id}, confidence={finding.confidence}, motivo: {reason})"
        )
    if result.unknown_item_ids:
        print(
            f"  AVISO: el Editor aprobó {len(result.unknown_item_ids)} item_id que no "
            f"están entre los candidatos, ignorados: {', '.join(result.unknown_item_ids)}",
            file=sys.stderr,
        )


def _edit_one_night(
    *,
    work: AgentWorkFactory,
    provider: LLMProvider,
    config: PipelineConfig,
    clock: Clock,
    run_id: UUID,
) -> tuple[int, RunStatus]:
    """Ejecuta el Editor sobre los candidatos pendientes de `run_id` y traduce
    el resultado a `(exit_code, run_status)`.

    Única función de este módulo que puede devolver `RunStatus.COMPLETED`
    (T43, paso 5; ver el docstring del módulo). `max_attempts` se ata a
    `limits.max_editor_calls_per_night` -- nunca a `max_calls_per_item`, que
    es del Reader/Popularizer -- para que el bucle de reintento de
    `AgentRunner` y el tope de `BudgetGuard` no puedan discrepar.

    `0` si el Editor decidió la noche (con o sin publicaciones,
    `EditOutcome.EDITED`) o si no había ningún candidato
    (`EditOutcome.NO_CANDIDATES`, coste cero): en ambos casos la noche hizo
    lo que tenía que hacer, `COMPLETED`. `4` si `BudgetGuard` deniega la
    llamada al Editor por un motivo distinto de `EDITOR_ALREADY_CALLED`
    (`terminal_status_for(exc.reason)`, igual que en el Reader y el
    Popularizer). `6` en los dos casos que dejan candidatos sin decidir: el
    Editor falló (JSON inválido agotados los reintentos, timeout, límite de
    tasa o error del proveedor) o `EDITOR_ALREADY_CALLED` -- este último
    **no** pasa por `terminal_status_for` (esa función lanza `ValueError` a
    propósito para ese motivo, ver su docstring en `application/budget.py`,
    y `docs/OPEN_DECISIONS.md` nº 72): se distingue aquí, en el sitio de
    llamada, antes de invocarla.
    """
    edit_night = EditNight(
        work=work,
        provider=provider,
        clock=clock,
        system_prompt=load_prompt("editor"),
        prompt_version=EDITOR_PROMPT_VERSION,
        model=config.models.editor,
        max_turns=config.limits.max_turns_per_agent,
        max_attempts=config.limits.max_editor_calls_per_night,
        base_tokens=config.budget.editor_base_tokens,
        tokens_per_candidate=config.budget.editor_tokens_per_candidate,
    )
    try:
        result = asyncio.run(edit_night(run_id=run_id))
    except BudgetDenied as exc:
        if exc.reason is DenyReason.EDITOR_ALREADY_CALLED:
            # No cierra la noche por `terminal_status_for` (le lanzaría
            # `ValueError` a propósito, ver el docstring de esta función):
            # "el Editor ya agotó sus intentos" es un fin de noche normal,
            # no una anomalía -- pero con candidatos sin decidir, tampoco es
            # un `COMPLETED`.
            print(
                f"BudgetGuard denegó la llamada al Editor: {exc.reason.value} "
                f"(quedan {exc.remaining_tokens} tokens); el Editor ya agotó sus intentos "
                "de esta noche (limits.max_editor_calls_per_night)",
                file=sys.stderr,
            )
            return 6, RunStatus.PARTIAL
        _print_budget_denial(exc, role="Editor")
        return 4, terminal_status_for(exc.reason)

    if result.outcome is EditOutcome.NO_CANDIDATES:
        print("Editor: sin candidatos esta noche (ningún Finding pendiente de publicar)")
        return 0, RunStatus.COMPLETED

    if result.outcome is not EditOutcome.EDITED:
        # JSON inválido agotados los reintentos, timeout, límite de tasa o
        # error del proveedor: `EditNight` (T43) capturó la excepción y la
        # tradujo aquí a un `outcome` distinto de `EDITED`. Había
        # candidatos y ninguno se decidió: la noche se queda corta.
        print(
            f"decisión del Editor fallida: {result.outcome.value} "
            f"(candidatos={result.candidates}, intentos={result.attempts}, "
            f"tokens gastados={result.tokens_spent})",
            file=sys.stderr,
        )
        return 6, RunStatus.PARTIAL

    _print_edit_night_report(result=result, work=work, config=config)
    return 0, RunStatus.COMPLETED


class _ReadItemContractViolated(RuntimeError):
    """`ReadItem.__call__` devolvió `ReadOutcome.READ` sin una `Reading`.

    `ReadItemResult` (T41) declara `reading: Reading | None`, pero su
    propio contrato es que `READ` siempre trae una: esto es un bug de
    `ReadItem`, nunca una entrada de usuario ni un desenlace esperable de
    `run-item`, así que no se traduce a ningún código de salida. `raise`,
    no `assert`: `python -O` desactiva `assert` y este contrato debe
    sobrevivir a esa bandera, igual que las excepciones de dominio
    (`domain/errors.py`, "nunca con `assert`"). No es una excepción de
    dominio -- no hay ninguna regla de negocio que viole, es un contrato
    entre dos módulos de `application`/`cli` -- así que no vive en
    `domain/errors.py`; mismo patrón que `ApiKeyInEnvironment` en
    `infrastructure/llm/agent_sdk_provider.py`.
    """


def _read_one_item(
    *,
    item: Item,
    work: AgentWorkFactory,
    provider: LLMProvider,
    config: PipelineConfig,
    clock: Clock,
    run_id: UUID,
) -> tuple[int, RunStatus]:
    """Ejecuta el Reader sobre `item` y, si produce una `Reading`, encadena el
    Popularizer sobre ella. Traduce el resultado a `(exit_code, run_status)`.

    Toda la configuración del Reader (modelo, turnos, versión de prompt,
    estimación de coste, intentos) sale de `config`, nunca hardcodeada
    (T41, paso 8).

    `run_status` es, en el mismo `return` que fija `exit_code`, el estado
    terminal con el que `_run_item` debe cerrar el `Run` que él mismo haya
    creado. Antes de T42 este valor era un booleano, `had_reading` --
    "¿produjo una Reading el Reader?" -- y `_run_item` cerraba `COMPLETED`
    si era `True`, `PARTIAL` si no, con un `status_override` aparte para la
    única excepción conocida entonces (denegación de `BudgetGuard`). T42
    añadió una segunda etapa (el Popularizer) sin repensar ese booleano:
    "hubo Reading" pasó a ser cierto incluso cuando la divulgación fallaba,
    y `_run_item` cerraba `COMPLETED` una noche que solo hizo la mitad del
    trabajo -- el bug que fija
    `test_popularizer_fallido_devuelve_5_deja_la_reading_intacta`. Aquí ya
    no hay booleano que reinterpretar ni `override` opcional que se pueda
    olvidar: cuando el Reader tiene éxito, este método delega el
    `(exit_code, run_status)` entero en `_popularize_one_reading`, tal
    cual, sin mezclarlo con nada decidido aquí. T43 añadió el Editor como
    tercera etapa siguiendo el mismo patrón -- la función que decide el
    desenlace de esa etapa decide también, en el mismo `return`, el
    `run_status` que le corresponde; no hay un valor por defecto que
    "olvidarse de sobrescribir" (ver el docstring del módulo, "T43, paso
    5"). `clock` viaja hasta `_edit_one_night`, que lo necesita para
    `Finding.publish(confidence, at)`.

    La `Reading` se persiste y se confirma en su propia unidad de trabajo
    dentro de `ReadItem.__call__`, antes de que este método siquiera
    construya `PopularizeReading`: ningún fallo del Popularizer puede
    perder la lectura ya escrita (T42, paso 8).
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
        return 3, RunStatus.PARTIAL
    except BudgetDenied as exc:
        _print_budget_denial(exc, role="Reader")
        return 4, terminal_status_for(exc.reason)

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
        return 1, RunStatus.PARTIAL

    reading = result.reading
    if reading is None:
        raise _ReadItemContractViolated("ReadItem devolvió ReadOutcome.READ sin Reading")
    _print_read_item_report(
        item=item,
        reading=reading,
        attempts=result.attempts,
        tokens_spent=result.tokens_spent,
        work=work,
        run_id=run_id,
    )
    return _popularize_one_reading(
        item=item,
        reading=reading,
        work=work,
        provider=provider,
        config=config,
        clock=clock,
        run_id=run_id,
    )


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

    # `status` es el estado terminal con el que se cierra el Run que este
    # proceso haya creado (si `run_reused`, nadie lo cierra aquí, ver
    # `_current_or_new_run`). El valor inicial es el que corresponde al
    # camino "item is None" más abajo, el único que se decide en este
    # método en vez de en `_read_one_item`/`_popularize_one_reading`/
    # `_edit_one_night`; cualquier otro camino lo sobrescribe explícitamente.
    exit_code = 1
    status = RunStatus.PARTIAL
    try:
        with work() as w:
            item = w.items.get(args.item_id)

        if item is None:
            print(f"no existe ningún Item con id={args.item_id}", file=sys.stderr)
            exit_code = 3
        else:
            exit_code, status = _read_one_item(
                item=item,
                work=work,
                provider=AgentSDKProvider(),
                config=config,
                clock=clock,
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
