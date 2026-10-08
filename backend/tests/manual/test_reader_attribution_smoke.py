"""Humo manual de T71.b: atribución de medidas del Reader experimental.

**NO SE LANZA en la suite normal.** Excluido por `addopts = "-m 'not manual'"`
(`pyproject.toml`) y por la fixture autouse `_require_real_claude_opt_in` de
`tests/manual/conftest.py`, que salta cualquier test de este directorio si
`NOCTURNA_ALLOW_REAL_CLAUDE` no está en el entorno -- sin importar desde
dónde se lance pytest ni qué `-m` se pida. Lo lanza el autor a mano, con:

    env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s \\
        tests/manual/test_reader_attribution_smoke.py

**Solo lleva los marcadores `manual` y `anyio`, deliberadamente sin `db`**:
misma regresión que `test_manual_markers_guard.py` congela para
`test_sdk_smoke.py` (T40) y que el resto de `tests/manual/` ya hereda -- un
segundo marcador filtrable en `tests/manual/` reabriría un camino de
selección (`pytest -m db`) que no pasa por `-m 'not manual'`. Este fichero
sigue necesitando PostgreSQL (usa `db_session_factory` vía el reexport de
`tests/manual/conftest.py`), pero eso no lo hace aparecer bajo `-m db`.

## Qué hace

Cuatro llamadas reales al Reader, una por abstract de
`tests/fixtures/t71b/abstracts.json`, con el prompt experimental de
`tests/experiments/reader-measures-exp1.md` (`EXPERIMENT_PROMPT_VERSION`),
usando `AgentRunner` directamente -- el mismo runner que usan `ReadItem`/
`PopularizeReading`/`EditNight` en producción, con `role=AgentRole.READER`
pero `system_prompt`/`prompt_version` experimentales -- construido contra
PostgreSQL real (`nocturna_test`), con un `Run(budget_tokens=40_000)` y una
`BudgetPolicy` de conveniencia para este experimento (`_test_policy`).

Compara la salida real contra lo que el autor confirmó a mano
(`reader_attribution.EXPECTED`/`evaluate`) y, si `NOCTURNA_ALLOW_ARCHIVE_QUERY`
está en el entorno, cruza las medidas `this_work` utilizables contra el NASA
Exoplanet Archive de verdad (`reader_attribution.sigma_rows`, reutilizando
`scripts/exoplanet_viability.py`, T71). Escribe `report.txt` y
`attribution.json` en `NOCTURNA_T71B_OUT_DIR` (o en `tmp_path` si esa
variable no está) e imprime la ruta.

**`NOCTURNA_T71B_OUT_DIR` no debe apuntar dentro de este repositorio.**
`report.txt`/`attribution.json` llevan salida real de Claude (texto e
identificadores de un run real) que no debe acabar en un commit por
accidente vía `git add -A`/`git add .`; usa una ruta fuera del árbol del
repo (por ejemplo, algo bajo `/tmp` o el directorio scratch de la sesión).

## Por qué el paso del archivo necesita `monkeypatch.undo()`, no un flag nuevo

`tests/conftest.py::_no_network` es autouse **sin excepción para `manual`**
(a diferencia de `_no_claude`, que sí comprueba el marcador): bloquea
`httpx.HTTPTransport.handle_request` para *toda* la suite, incluida
`tests/manual/`. `NOCTURNA_ALLOW_ARCHIVE_QUERY` no basta por sí solo para
salir a la red bajo pytest -- la petición real explotaría con
`RuntimeError("test intentando salir a la red")` antes de llegar a
`httpx`. Como `monkeypatch` es una fixture de alcance por test y `_no_network`
también la pide, dentro de este test es la MISMA instancia: pedirla como
parámetro y llamar a `monkeypatch.undo()` revierte, dentro de este test y
solo en este test, los parcheos que `_no_network` (y el espía de `query()`
de más abajo, ya innecesario en ese punto) hicieron -- sin tocar
`conftest.py`, que está fuera del alcance de esta tarea. Deliberado: la
autorización de red sigue siendo `NOCTURNA_ALLOW_ARCHIVE_QUERY`, este truco
solo quita el bloqueo genérico de pytest para que esa autorización tenga
efecto.
"""

from __future__ import annotations

import json
import os
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, time
from pathlib import Path
from time import monotonic
from uuid import UUID

import httpx
import pytest
from claude_agent_sdk import ResultMessage
from experiments import reader_attribution as ra
from fakes.clock import FakeClock
from sqlalchemy import select

from nocturna.application.agents.runner import AgentRunner
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.domain.entities import Item, Run
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

pytestmark = [pytest.mark.manual]

_NO_API_KEY_REMEDY = (
    "ANTHROPIC_API_KEY está definida en el entorno de este proceso. Este test hace "
    "llamadas reales y debe cobrarse contra la suscripción Claude Max (CLI 'claude' "
    "logueado), nunca contra una API key (CLAUDE.md, 'Restricción que gobierna todo "
    "el diseño'). Quítala del entorno y repite con: "
    "env -u ANTHROPIC_API_KEY NOCTURNA_ALLOW_REAL_CLAUDE=1 uv run pytest -m manual -s "
    "tests/manual/test_reader_attribution_smoke.py"
)

_ALLOW_ARCHIVE_ENV_VAR = "NOCTURNA_ALLOW_ARCHIVE_QUERY"
_OUT_DIR_ENV_VAR = "NOCTURNA_T71B_OUT_DIR"

# Dentro de la ventana real de ejecución (00:00-04:45), igual que el resto
# de `tests/manual/`.
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_WINDOW_START = time(0, 0)
_WINDOW_HARD_STOP = time(4, 45)

_TEST_RUN_BUDGET_TOKENS = 40_000
_TEST_EDITOR_RESERVE_TOKENS = 0
_TEST_MAX_ITEMS_PER_NIGHT = 4
_TEST_MAX_CALLS_PER_ITEM = 2
_TEST_ITEM_TIMEOUT_S = 180
# Estimación de coste por llamada, como `budget.reader_estimated_tokens` en
# producción (ver `test_read_item_smoke.py`): el prompt experimental es más
# largo que `prompts/reader.md` (pide un quinto campo, `measurements`), así
# que la estimación sube de los 6 000 del Reader real a 7 000, tal y como
# fija el plan aprobado de T71.b.
#
# Los asserts de contabilidad de más abajo no usan un margen arbitrario:
# comprueban el invariante exacto que garantiza `BudgetGuard.check`
# (`application/budget.py`, regla 2/4) -- autoriza una llamada si y solo si
# `tokens_used_for_run(run_id)` (la suma de TODAS las llamadas anteriores)
# más `estimated_tokens` no supera `policy.nightly_tokens` -- y, por
# separado, la cota literal que fija el plan de T71.b sobre el gasto total.
_ESTIMATED_TOKENS = 7_000

# Subtope conservador del cliente del archivo (T71): 4 planetas, como mucho
# unas pocas peticiones de índice + soluciones por lotes.
_ARCHIVE_MAX_REQUESTS = 3
_ARCHIVE_SPACING_S = 2.0


def _test_policy(*, max_turns_per_agent: int) -> BudgetPolicy:
    """`max_turns_per_agent` se pasa desde fuera (`config.limits.max_turns_per_agent`,
    ver la llamada en el propio test): un único origen para ese valor, en
    vez de un `1` fijo aquí que pudiera desincronizarse del que de verdad
    usa el `AgentRunner` (`max_turns=config.limits.max_turns_per_agent`)."""
    return BudgetPolicy(
        nightly_tokens=_TEST_RUN_BUDGET_TOKENS,
        editor_reserve_tokens=_TEST_EDITOR_RESERVE_TOKENS,
        writer_reserve_tokens=0,
        max_items_per_night=_TEST_MAX_ITEMS_PER_NIGHT,
        max_turns_per_agent=max_turns_per_agent,
        max_editor_calls_per_night=1,
        max_writer_calls_per_night=0,
        max_calls_per_item=_TEST_MAX_CALLS_PER_ITEM,
        item_timeout_s=_TEST_ITEM_TIMEOUT_S,
        editor_timeout_s=300,
        run_timeout_s=600,
        window_start=_WINDOW_START,
        window_hard_stop=_WINDOW_HARD_STOP,
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _work_factory(db_session_factory, run_id: UUID, policy: BudgetPolicy) -> AgentWorkFactory:
    """Mismo patrón que `cli.py::_agent_work_factory` y el resto de
    `tests/manual/`: cada llamada abre una `unit_of_work` nueva contra
    PostgreSQL real, con un `BudgetGuard` construido sobre el reloj fijo de
    este módulo."""
    clock = FakeClock(_WITHIN_WINDOW)

    @contextmanager
    def _open() -> Generator[AgentWork, None, None]:
        with unit_of_work(db_session_factory) as session:
            runs = SqlAlchemyRunRepository(session)
            agent_calls = SqlAlchemyAgentCallRepository(session)
            guard = BudgetGuard(
                run_id=run_id, policy=policy, runs=runs, agent_calls=agent_calls, clock=clock
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


def _build_items(abstracts: list[dict[str, str]]) -> list[Item]:
    return [
        Item(
            source="arxiv",
            external_id=abstract["external_id"],
            title=abstract["title"],
            abstract=abstract["abstract"],
            categories=["astro-ph.EP"],
            published_at=_WITHIN_WINDOW,
            fetched_at=_WITHIN_WINDOW,
        )
        for abstract in abstracts
    ]


def _output_path(tmp_path: Path, name: str) -> Path:
    out_dir_raw = os.environ.get(_OUT_DIR_ENV_VAR)
    out_dir = Path(out_dir_raw) if out_dir_raw else tmp_path
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / name


def _tokens_for_captured_call(messages: list[object]) -> int:
    """Tokens reales (`tokens_in + tokens_out`) de una llamada real a
    `query()`, calculados con `agent_sdk_provider._extract_tokens` -- la
    misma función que usa `AgentSDKProvider.run_agent` para decidir qué
    graba `record_call` -- sobre el último `ResultMessage` capturado por el
    espía de este test. No reimplementa la lógica de `usage`/`model_usage`
    (cachés, camelCase, ver el docstring de módulo de
    `agent_sdk_provider.py`): la reutiliza tal cual, así que este número
    coincide exactamente con lo que queda en `AgentCallRow` para esa
    llamada.

    Devuelve `0` si no se capturó ningún `ResultMessage` (una llamada que
    falló antes de completarse): no ocurre en un intento `ok`/
    `invalid_output`, y este humo no ejercita deliberadamente timeouts ni
    límites de tasa reales.
    """
    result_messages = [m for m in messages if isinstance(m, ResultMessage)]
    if not result_messages:
        return 0
    last = result_messages[-1]
    tokens_in, tokens_out = agent_sdk_provider._extract_tokens(
        usage=last.usage, model_usage=last.model_usage
    )
    return tokens_in + tokens_out


@pytest.mark.anyio
async def test_smoke_reader_attribution_llamadas_reales_producen_informe_y_gasto_contabilizado(
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
    tmp_path: Path,
) -> None:
    # --- ANTHROPIC_API_KEY ausente del entorno -----------------------------
    if "ANTHROPIC_API_KEY" in os.environ:
        pytest.fail(_NO_API_KEY_REMEDY)

    # --- espía transparente delante de `query()`, para el volcado de usage -
    # Una entrada por cada llamada real a `query()` (= cada intento, de
    # cualquier ítem, en el orden en que ocurren): `run_attribution` es
    # secuencial, así que agrupar en una lista plana y luego repartir por
    # `record.attempts` reconstruye qué intentos pertenecen a qué abstract.
    captured_calls: list[list[object]] = []
    real_query = agent_sdk_provider.query

    async def _spying_query(*args: object, **kwargs: object):
        messages: list[object] = []
        captured_calls.append(messages)
        inner = real_query(*args, **kwargs)
        try:
            async for message in inner:
                messages.append(message)
                yield message
        finally:
            await inner.aclose()

    monkeypatch.setattr(agent_sdk_provider, "query", _spying_query, raising=True)

    config = load_pipeline_config()
    policy = _test_policy(max_turns_per_agent=config.limits.max_turns_per_agent)
    abstracts = ra.load_abstracts()
    abstract_by_id = {a["external_id"]: a["abstract"] for a in abstracts}
    items = _build_items(abstracts)

    # --- Items y Run reales, en su propia unidad de trabajo -----------------
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyItemRepository(session).add_many(items)

        runs = SqlAlchemyRunRepository(session)
        run = Run(started_at=_WITHIN_WINDOW, budget_tokens=_TEST_RUN_BUDGET_TOKENS)
        runs.add(run)
        session.flush()
        run_id = run.id

    work = _work_factory(db_session_factory, run_id, policy)

    runner = AgentRunner(
        work=work,
        provider=AgentSDKProvider(),
        role=AgentRole.READER,
        model=config.models.reader,
        system_prompt=ra.load_experiment_prompt(),
        prompt_version=ra.EXPERIMENT_PROMPT_VERSION,
        estimated_tokens=_ESTIMATED_TOKENS,
        max_turns=config.limits.max_turns_per_agent,
        max_attempts=_TEST_MAX_CALLS_PER_ITEM,
    )

    # --- las cuatro llamadas reales, secuenciales ---------------------------
    started_at = monotonic()
    records = await ra.run_attribution(runner, items)
    duration_ms = int((monotonic() - started_at) * 1000)

    print(
        f"\n--- T71.b: {len(records)} abstracts procesados en {duration_ms} ms, run_id={run_id} ---"
    )
    for attempt_index, messages in enumerate(captured_calls, start=1):
        print(f"\n--- intento real #{attempt_index}: tipo de cada mensaje capturado ---")
        for message in messages:
            print(type(message).__name__)
        result_messages = [m for m in messages if isinstance(m, ResultMessage)]
        if result_messages:
            last = result_messages[-1]
            print(f"--- intento real #{attempt_index}: usage crudo --- {last.usage!r}")
            print(f"--- intento real #{attempt_index}: output_text crudo ---\n{last.result!r}")
        else:
            print(f"(intento real #{attempt_index}: no se recibió ningún ResultMessage)")

    assert sum(r.attempts for r in records) == len(captured_calls), (
        "cada intento de run_attribution debe corresponder a exactamente una llamada real a query()"
    )

    # --- evaluación contra lo confirmado por el autor -----------------------
    results = {
        record.external_id: ra.evaluate(
            record.external_id, record.measurements or [], abstract_by_id[record.external_id]
        )
        for record in records
        if record.outcome == "ok"
    }

    # --- informe y volcado, INMEDIATAMENTE tras run_attribution/evaluate ----
    # Sin `sigma_rows_list`/`archive_error` todavía (el cruce con el archivo,
    # más abajo, es opcional y puede fallar): las cuatro llamadas reales ya
    # se han pagado en este punto, así que su resultado se escribe a disco
    # antes de arriesgarse a nada más -- un fallo posterior (red, asserts de
    # contabilidad) no debe dejar sin `report.txt`/`attribution.json` un
    # experimento que sí costó dinero real.
    report_path = _output_path(tmp_path, "report.txt")
    attribution_path = _output_path(tmp_path, "attribution.json")

    def _write_report(sigma_rows_list: list[ra.SigmaRow] | None, archive_error: str | None) -> str:
        text = ra.render_report(records, results, sigma_rows_list, archive_error)
        report_path.write_text(text, encoding="utf-8")
        attribution_path.write_text(
            json.dumps(
                ra.dump_json(records, results, sigma_rows_list, archive_error),
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return text

    report_text = _write_report(None, None)
    print(f"\n{report_text}\n")
    print(f"--- informe escrito en: {report_path} ---")
    print(f"--- atribución JSON escrita en: {attribution_path} ---")

    # --- asserts de contabilidad, únicamente (el plan de T71.b lo exige: la
    # exactitud de la atribución es el resultado del experimento, no algo
    # que este test deba forzar en verde) -----------------------------------
    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        db_tokens_used = agent_calls.tokens_used_for_run(run_id)
        db_reader_calls = agent_calls.count_for_run(run_id, AgentRole.READER)
        call_rows = list(
            check_session.execute(
                select(AgentCallRow).where(AgentCallRow.run_id == run_id)
            ).scalars()
        )

    total_tokens_spent = sum(record.tokens_spent for record in records)
    total_attempts = sum(record.attempts for record in records)

    assert db_tokens_used == total_tokens_spent, (
        "el acumulado que lee BudgetGuard (AgentCallRepository.tokens_used_for_run) debe "
        "coincidir exactamente con la suma de tokens_spent de todos los AttemptRecord"
    )
    assert db_reader_calls == total_attempts == len(call_rows), (
        "el número de intentos reportado por run_attribution debe coincidir con el "
        "número de filas AgentCall (rol reader) para este run_id"
    )

    # --- invariante exacto que garantiza BudgetGuard.check -------------------
    # `captured_calls` está en el orden real de ocurrencia (el espía lo llena
    # de forma síncrona, una entrada por cada llamada real a `query()`, y
    # `run_attribution` es secuencial): es "el orden de creación" de las
    # filas `AgentCall`, aunque la tabla no tenga una columna de marca de
    # tiempo por la que ordenar un `SELECT`. `BudgetGuard.check` autorizó
    # cada llamada real solo porque, en ese momento,
    # `tokens_used_for_run(run_id)` -- la suma de TODAS las llamadas
    # anteriores, nunca de la que se está autorizando -- más
    # `_ESTIMATED_TOKENS` no superaba `policy.nightly_tokens`
    # (`application/budget.py::BudgetGuard.check`, regla 2/4). En particular
    # eso vale para la última llamada real hecha: la suma de todas las demás
    # (todas menos la última) más `_ESTIMATED_TOKENS` es la cota que el
    # guard garantiza de verdad -- no un múltiplo arbitrario de la
    # estimación.
    per_call_tokens = [_tokens_for_captured_call(messages) for messages in captured_calls]
    if per_call_tokens:
        tokens_before_last_call = sum(per_call_tokens[:-1])
        assert tokens_before_last_call + _ESTIMATED_TOKENS <= policy.nightly_tokens, (
            f"el guard debería haber denegado la última llamada real: lo ya gastado "
            f"antes de ella ({tokens_before_last_call}) más la estimación de esa "
            f"llamada ({_ESTIMATED_TOKENS}) supera el tope nocturno de la política de "
            f"prueba ({policy.nightly_tokens})"
        )

    # --- cota literal del plan de T71.b (NO se deduce del invariante de
    # arriba: el guard solo acota lo gastado ANTES de cada llamada, nunca lo
    # que esa llamada cueste de verdad una vez ejecutada -- el plan fija
    # aparte que el gasto total no debe rebasar el tope más una llamada) ----
    assert db_tokens_used <= policy.nightly_tokens + _ESTIMATED_TOKENS, (
        f"el gasto real ({db_tokens_used}) supera el tope nocturno de la política de "
        f"prueba ({policy.nightly_tokens}) más la estimación de una llamada "
        f"({_ESTIMATED_TOKENS}), la cota literal fijada por el plan de T71.b; revisa si "
        "`_ESTIMATED_TOKENS` sigue siendo una estimación razonable para el prompt "
        "experimental"
    )

    # --- cruce opcional con el NASA Exoplanet Archive, AL FINAL -------------
    # Los resultados pagados (records/results) ya están en disco y los
    # asserts de contabilidad ya pasaron: nada de lo que ocurra a partir de
    # aquí debe poder tumbar el test ni dejar sin informe un experimento que
    # ya costó dinero real. `NOCTURNA_ALLOW_ARCHIVE_QUERY` autoriza la salida
    # a red; `monkeypatch.undo()` (ver docstring del módulo) quita el
    # bloqueo genérico de `_no_network` que, de otro modo, seguiría en pie
    # aunque la variable esté presente.
    if _ALLOW_ARCHIVE_ENV_VAR in os.environ:
        monkeypatch.undo()
        attributed = {
            record.external_id: record.measurements
            for record in records
            if record.measurements is not None
        }
        sigma_rows_list: list[ra.SigmaRow] | None = None
        archive_error: str | None = None
        # Carga del módulo FUERA del `try` de red: es un `importlib` sin
        # efectos secundarios (ver su docstring), y `ArchiveClientError`
        # tiene que existir ya para nombrarla en el `except` de abajo -- si
        # el propio `import` fallara, sería un error de programación de este
        # test, no algo que este humo deba tragarse silenciosamente.
        ev = ra._load_exoplanet_viability_module()
        try:
            with httpx.Client() as http:
                archive_client = ev.ArchiveClient(
                    http, max_requests=_ARCHIVE_MAX_REQUESTS, spacing_s=_ARCHIVE_SPACING_S
                )
                sigma_rows_list = ra.sigma_rows(attributed, archive_client)
        except (ev.ArchiveClientError, httpx.HTTPError) as exc:
            # Cualquier fallo al hablar con el archivo (HTTP != 200, VOTABLE
            # de error, tope de `max_requests`, o un fallo de transporte de
            # `httpx` que `ArchiveClient` no envuelve, ver su docstring) se
            # anota en el informe en vez de propagarse: los resultados
            # pagados de las cuatro llamadas reales no dependen de que el
            # archivo responda.
            archive_error = f"{type(exc).__name__}: {exc}"
            print(f"--- cruce con el NASA Exoplanet Archive falló: {archive_error} ---")

        report_text = _write_report(sigma_rows_list, archive_error)
        print(f"\n--- informe reescrito con el cruce del archivo ---\n{report_text}\n")
        print(f"--- informe reescrito en: {report_path} ---")
        print(f"--- atribución JSON reescrita en: {attribution_path} ---")
