"""Humo manual de T71.c, paso 7: `ReadItem` real con la variante `reader-v3`
(medidas estructuradas por planeta).

**NO SE LANZA en la suite normal.** Excluido por `addopts = "-m 'not manual'"`
(`pyproject.toml`) y por la fixture autouse `_require_real_claude_opt_in` de
`tests/manual/conftest.py`, que salta cualquier test de este directorio si
`NOCTURNA_ALLOW_REAL_CLAUDE` no está en el entorno -- sin importar desde
dónde se lance pytest ni qué `-m` se pida. Lo lanza el autor a mano, con
`ANTHROPIC_API_KEY` ausente del entorno (comprobado dentro del test) y:

    NOCTURNA_ALLOW_REAL_CLAUDE=1 NOCTURNA_T71C_OUT_DIR=<dir fuera del repo> \\
        uv run pytest -m manual -s tests/manual/test_read_item_v3_smoke.py

**`NOCTURNA_T71C_OUT_DIR` no debe apuntar dentro de este repositorio.**
`report.txt` lleva salida real de Claude (medidas atribuidas, texto citado
de los abstracts) que no debe acabar en un commit por accidente vía
`git add -A`/`git add .`; usa una ruta fuera del árbol del repo (por
ejemplo, algo bajo `/tmp` o el directorio scratch de la sesión). Sin esa
variable, se escribe en `tmp_path` (se borra al terminar la sesión de
pytest).

**Solo lleva los marcadores `manual` y `anyio`, deliberadamente sin `db`**:
misma regresión que `test_manual_markers_guard.py` congela para
`test_sdk_smoke.py` (T40) -- un segundo marcador filtrable en
`tests/manual/` reabriría un camino de selección (`pytest -m db`) que no
pasa por `-m 'not manual'`. Este fichero sigue necesitando PostgreSQL (usa
`db_session_factory` vía el reexport de `tests/manual/conftest.py`), pero
eso no lo hace aparecer bajo `-m db`.

## Qué hace

Cuatro llamadas reales al Reader, una por abstract de
`tests/fixtures/t71b/abstracts.json` (los mismos cuatro de T71.b), con
`categories=["astro-ph.EP"]` -- dentro de `[reader] measurement_categories`
de `config/pipeline.toml`, así que las cuatro deben elegir la variante
`reader-v3` -- usando `ReadItem` REAL, el camino de producción, construido
con el mismo helper que usa la CLI: `cli._build_read_item` (composition
root, T71.c), con `AgentSDKProvider()` de verdad y los prompts reales
(`prompts/reader.md`/`prompts/reader-v3.md`) cargados con
`cli._load_reader_prompts()`. A diferencia de
`test_reader_attribution_smoke.py` (T71.b, que ejercita el prompt
*experimental* con `AgentRunner` a pelo) y de
`test_read_item_smoke.py` (T41, que fuerza `measures_categories=frozenset()`
para quedarse en `reader-v2`), este fichero es el primero que ejercita
`ReadItem` de verdad eligiendo la variante `reader-v3` con una llamada real.

Un `Run(budget_tokens=40_000)` y una `BudgetPolicy` de conveniencia para
este humo (`_test_policy`, `editor_reserve_tokens=0`,
`max_items_per_night=4`, `max_calls_per_item=2` -- igual que
`config.limits.max_calls_per_item` real, para que el tope de llamadas de
`BudgetGuard` no diverja del que aplica `cli._build_read_item` por dentro),
contra PostgreSQL real (`nocturna_test`), con un `FakeClock` fijado dentro
de la ventana de ejecución (00:00-04:45).

Bucle propio, secuencial (no `experiments.reader_attribution.run_attribution`,
que ejecuta el prompt *experimental* con `AgentRunner` a pelo, no `ReadItem`):
procesa los cuatro ítems uno a uno y para el resto de la lista, sin más
llamadas, ante dos desenlaces -- mismo criterio que T71.b:

- `BudgetDenied` (lanzado por `BudgetGuard.authorize`, dentro de
  `AgentRunner.run`, y que `ReadItem.__call__` no captura -- sale intacta):
  este ítem y todos los que quedan se registran como `not_run`, sin gastar
  nada más.
- `ReadOutcome.RATE_LIMITED`: la llamada SÍ ocurrió y SÍ se cobró (se
  registra con su gasto real), pero ningún ítem posterior se intenta.

Reutiliza, de `tests/experiments/reader_attribution.py` (T71.b, SIN
modificarlo): `load_abstracts()` para los cuatro abstracts y `evaluate()`
para comparar las medidas atribuidas contra `EXPECTED` -- `evaluate()` pide
una `Sequence` de objetos con los atributos de `MeasurementOut`
(`planet_name`, `parameter`, `value`, `err_plus`, `err_minus`, `unit`,
`limit`, `origin`, `evidence`); `Reading.measurements` (lo que produce
`ReadItem` real) es una tupla de `Measurement` (`domain/entities.py`), no de
`MeasurementOut` (`application/agents/reader_output.py`), pero los cuatro
campos de enum de `Measurement` (`MeasuredParameter`, `MeasurementUnit`,
`MeasurementLimit`, `MeasurementOrigin`) son `StrEnum`: comparan igual que
la cadena (`measurement.parameter == "mass"`) e interpolan igual en un
f-string (`str(MeasurementUnit.M_JUP) == "M_jup"`), así que `evaluate()`
funciona sobre `Measurement` real sin ningún adaptador. **La calidad de la
atribución NO hace fallar este test** (igual que T71.b): el informe anota,
por abstract, un veredicto OK/AVISO/FALLO (FALLO si alguna fila de
`evaluate()` es FALLO, AVISO si no hay FALLO pero sí AVISO, OK en otro
caso), solo para que el autor lo revise a mano.

El informe añade, más allá de lo que ya cubre `evaluate()`: la
`prompt_version` real de cada intento (se espera `reader-v3` en las
cuatro), las medidas conservadas (`Reading.measurements`) y las
DESCARTADAS con su motivo -- capturadas de los registros
`reader.measurement_discarded` (`application/use_cases/read_item.py`, T71.c)
con `caplog`, no releídas de ningún fichero -- y los tokens reales, por
ítem y en total.

**Sin paso de archivo ni red más allá de Claude**: a diferencia de
`test_reader_attribution_smoke.py`, este fichero no cruza nada contra el
NASA Exoplanet Archive (`NOCTURNA_ALLOW_ARCHIVE_QUERY` no aplica aquí). El
informe se escribe en cuanto termina el bucle y se evalúa la atribución,
antes de cualquier assert que pudiera fallar: no hay ningún paso opcional
después que pudiera dejar sin informe cuatro llamadas ya pagadas.

## Asserts de contabilidad (lo único que puede tumbar este test)

- `AgentCallRepository.tokens_used_for_run(run_id)` (lo que de verdad
  cargaría contra el presupuesto de una noche real) debe coincidir
  exactamente con la suma de `ReadItemResult.tokens_spent` de todos los
  ítems procesados (0 para los `not_run`).
- El número de filas `AgentCall` (rol `reader`) para este `run_id` debe
  coincidir con la suma de `ReadItemResult.attempts` y con el número de
  filas `AgentCall` leídas directamente de la tabla.
- Ese número de llamadas no debe superar `max_calls_per_item *
  max_items_per_night` = 2 * 4 = 8 (el tope que ya impone
  `BudgetGuard.check`, regla 9 de `application/budget.py`; se repite aquí
  como comprobación explícita del plan, no confiando en que el guard nunca
  tenga una regresión).
- Todas las filas `AgentCall` de este `run_id` deben llevar
  `prompt_version == READER_V3_PROMPT_VERSION`: los cuatro ítems son
  `astro-ph.EP`, dentro de `[reader] measurement_categories`, así que
  `ReadItem` no debería elegir `reader-v2` para ninguno.
- El gasto total no debe superar `nightly_tokens + reader_v3_estimated_tokens`
  (40 000 + 13 000 = 53 000 con la configuración real de
  `config/pipeline.toml`): la cota literal del plan de T71.c, paso 7, sobre
  lo que puede costar como mucho una llamada de la variante `reader-v3` por
  encima del presupuesto de prueba -- mismo razonamiento que
  `test_reader_attribution_smoke.py` (T71.b) sobre por qué esto no se deduce
  del invariante que garantiza `BudgetGuard.check` antes de cada llamada
  (ese invariante acota lo gastado ANTES de una llamada, nunca lo que esa
  llamada cueste de verdad una vez ejecutada).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, time
from pathlib import Path

import pytest
from experiments import reader_attribution as ra
from fakes.clock import FakeClock
from sqlalchemy import select

from nocturna import cli
from nocturna.application.agents.prompt_loader import READER_V3_PROMPT_VERSION
from nocturna.application.budget import BudgetDenied, BudgetPolicy
from nocturna.application.use_cases.read_item import ReadOutcome
from nocturna.domain.entities import Item, Reading, Run
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm.agent_sdk_provider import AgentSDKProvider

pytestmark = [pytest.mark.manual]

_NO_API_KEY_REMEDY = (
    "ANTHROPIC_API_KEY está definida en el entorno de este proceso. Este test hace "
    "llamadas reales y debe cobrarse contra la suscripción Claude Max (CLI 'claude' "
    "logueado), nunca contra una API key (CLAUDE.md, 'Restricción que gobierna todo "
    "el diseño'). Quita esa variable del entorno antes de repetir con el lanzamiento "
    "documentado en el docstring de este módulo."
)

_READ_ITEM_LOGGER = "nocturna.application.use_cases.read_item"
_DISCARD_EVENT = "reader.measurement_discarded"

# Dentro de la ventana real de ejecución (00:00-04:45), igual que el resto
# de `tests/manual/`.
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_WINDOW_START = time(0, 0)
_WINDOW_HARD_STOP = time(4, 45)

_TEST_RUN_BUDGET_TOKENS = 40_000
_TEST_EDITOR_RESERVE_TOKENS = 0
_TEST_MAX_ITEMS_PER_NIGHT = 4
_TEST_ITEM_TIMEOUT_S = 180

_OUT_DIR_ENV_VAR = "NOCTURNA_T71C_OUT_DIR"


def _test_policy(*, max_turns_per_agent: int, max_calls_per_item: int) -> BudgetPolicy:
    """`max_turns_per_agent`/`max_calls_per_item` se pasan desde fuera
    (`config.limits.*`, ver la llamada en el propio test): un único origen
    para esos dos valores, en vez de literales aquí que pudieran
    desincronizarse de los que de verdad usa `cli._build_read_item` por
    dentro (que los lee de `PipelineConfig`, no de esta `BudgetPolicy` de
    prueba)."""
    return BudgetPolicy(
        nightly_tokens=_TEST_RUN_BUDGET_TOKENS,
        editor_reserve_tokens=_TEST_EDITOR_RESERVE_TOKENS,
        max_items_per_night=_TEST_MAX_ITEMS_PER_NIGHT,
        max_turns_per_agent=max_turns_per_agent,
        max_editor_calls_per_night=1,
        max_calls_per_item=max_calls_per_item,
        item_timeout_s=_TEST_ITEM_TIMEOUT_S,
        editor_timeout_s=300,
        run_timeout_s=600,
        window_start=_WINDOW_START,
        window_hard_stop=_WINDOW_HARD_STOP,
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _work_factory(db_session_factory, run_id, policy: BudgetPolicy):
    """Mismo patrón que `cli.py::_agent_work_factory`, invocado directamente
    (no reimplementado): cada llamada abre una `unit_of_work` nueva contra
    PostgreSQL real, con un `BudgetGuard` construido sobre el reloj fijo de
    este módulo."""
    return cli._agent_work_factory(db_session_factory, run_id, policy, FakeClock(_WITHIN_WINDOW))


def _build_items(abstracts: list[dict[str, str]]) -> list[Item]:
    """`categories=["astro-ph.EP"]` para las cuatro: es la única categoría de
    `[reader] measurement_categories` (`config/pipeline.toml`) hoy, así que
    esto es lo que hace que `ReadItem` elija la variante `reader-v3` para
    los cuatro ítems de este humo."""
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


@dataclass(frozen=True, slots=True)
class _Discard:
    """Un descarte de `reader_measurements.filter_measurements`, leído del
    registro `reader.measurement_discarded` (`extra=...`), no de un
    fichero: `ReadItem` no persiste `DiscardedMeasurement` en ningún sitio,
    así que el log es la única fuente para este informe."""

    reason: str
    detail: str
    planet_name: str | None
    parameter: str | None
    value: float | None
    evidence: str | None


@dataclass(frozen=True, slots=True)
class _ItemRun:
    """Resultado de procesar un abstract con `ReadItem` real (o de no
    haberlo procesado, `outcome="not_run"`, ver el docstring del módulo)."""

    external_id: str
    outcome: str
    attempts: int
    tokens_spent: int
    prompt_version: str | None
    reading: Reading | None
    discarded: tuple[_Discard, ...]
    note: str | None = None


def _discards_from_caplog(records: list[logging.LogRecord]) -> tuple[_Discard, ...]:
    """Filtra los registros `reader.measurement_discarded` capturados para
    UN intento (la llamada limpia `caplog` entre ítems, ver el bucle
    principal) y extrae los campos que puso `extra=...`
    (`ReadItem._log_measurements`)."""
    return tuple(
        _Discard(
            reason=str(record.__dict__.get("reason")),
            detail=str(record.__dict__.get("detail")),
            planet_name=record.__dict__.get("planet_name"),
            parameter=record.__dict__.get("parameter"),
            value=record.__dict__.get("value"),
            evidence=record.__dict__.get("evidence"),
        )
        for record in records
        if record.name == _READ_ITEM_LOGGER and record.__dict__.get("event") == _DISCARD_EVENT
    )


def _overall_status(case_result: ra.CaseResult) -> str:
    """OK/AVISO/FALLO por abstract (tri-estado), a partir de las filas de
    `evaluate()`: FALLO si alguna fila es FALLO, AVISO si no hay FALLO pero
    sí alguna AVISO, OK si todas las filas son OK. `CaseResult.passed`
    (T71.b) solo distingue OK/FALLO -- no basta para lo que pide el plan de
    T71.c, paso 7."""
    statuses = {row.status for row in case_result.rows}
    if "FALLO" in statuses:
        return "FALLO"
    if "AVISO" in statuses:
        return "AVISO"
    return "OK"


def _render_report(item_runs: Sequence[_ItemRun], results: dict[str, ra.CaseResult]) -> str:
    lines: list[str] = [
        f"Informe T71.c, paso 7 -- ReadItem real, variante {READER_V3_PROMPT_VERSION}",
        "=" * 72,
    ]

    for item_run in item_runs:
        lines.append("")
        lines.append(f"--- {item_run.external_id} ---")
        outcome_line = f"outcome: {item_run.outcome}"
        if item_run.note is not None:
            outcome_line += f" ({item_run.note})"
        lines.append(outcome_line)
        lines.append(f"prompt_version: {item_run.prompt_version}")
        lines.append(f"intentos: {item_run.attempts}  tokens_spent: {item_run.tokens_spent}")

        if item_run.reading is not None and item_run.reading.measurements is not None:
            kept = item_run.reading.measurements
            lines.append(f"medidas conservadas ({len(kept)}):")
            if not kept:
                lines.append("  (ninguna)")
            for measurement in kept:
                lines.append(
                    f"  {measurement.planet_name} {measurement.parameter}="
                    f"{measurement.value} {measurement.unit} limit={measurement.limit} "
                    f"origin={measurement.origin} err_plus={measurement.err_plus} "
                    f"err_minus={measurement.err_minus}"
                )
            lines.append(f"medidas descartadas ({len(item_run.discarded)}):")
            if not item_run.discarded:
                lines.append("  (ninguna)")
            for discard in item_run.discarded:
                lines.append(
                    f"  [{discard.reason}] planet_name={discard.planet_name!r} "
                    f"parameter={discard.parameter!r} value={discard.value!r} "
                    f"evidence={discard.evidence!r} -- {discard.detail}"
                )

        case_result = results.get(item_run.external_id)
        if case_result is not None:
            lines.append(
                f"veredicto de atribución (informativo, no bloqueante): "
                f"{_overall_status(case_result)}"
            )
            for row in case_result.rows:
                lines.append(f"  [{row.status}] esperado={row.expected!r}")
                lines.append(f"           atribuido={row.attributed!r} -- {row.reason}")

    lines.append("")
    lines.append(f"Tokens reales totales: {sum(r.tokens_spent for r in item_runs)}")
    lines.append("Tokens reales por ítem:")
    for item_run in item_runs:
        lines.append(
            f"  {item_run.external_id}: {item_run.tokens_spent} tokens "
            f"({item_run.attempts} intento(s), outcome={item_run.outcome})"
        )

    return "\n".join(lines)


@pytest.mark.anyio
async def test_smoke_read_item_v3_llamadas_reales_usan_reader_v3_y_contabilizan_el_gasto(
    caplog: pytest.LogCaptureFixture,
    db_session_factory,
    tmp_path: Path,
) -> None:
    # --- ANTHROPIC_API_KEY ausente del entorno -----------------------------
    if "ANTHROPIC_API_KEY" in os.environ:
        pytest.fail(_NO_API_KEY_REMEDY)

    config = load_pipeline_config()
    policy = _test_policy(
        max_turns_per_agent=config.limits.max_turns_per_agent,
        max_calls_per_item=config.limits.max_calls_per_item,
    )
    prompts = cli._load_reader_prompts()
    abstracts = ra.load_abstracts()
    abstract_by_id = {abstract["external_id"]: abstract for abstract in abstracts}
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
    read_item = cli._build_read_item(
        config=config, work=work, provider=AgentSDKProvider(), prompts=prompts
    )

    # --- las cuatro llamadas reales, secuenciales, una unidad de trabajo
    # de log por ítem (`caplog.clear()`) para poder atribuir cada descarte
    # al ítem correcto ---------------------------------------------------
    caplog.set_level(logging.INFO, logger=_READ_ITEM_LOGGER)

    item_runs: list[_ItemRun] = []
    stop_reason: str | None = None

    for item in items:
        if stop_reason is not None:
            item_runs.append(
                _ItemRun(
                    external_id=item.external_id,
                    outcome="not_run",
                    attempts=0,
                    tokens_spent=0,
                    prompt_version=None,
                    reading=None,
                    discarded=(),
                    note=stop_reason,
                )
            )
            continue

        caplog.clear()
        try:
            result = await read_item(item)
        except BudgetDenied as exc:
            stop_reason = f"no ejecutado: presupuesto ({type(exc).__name__})"
            item_runs.append(
                _ItemRun(
                    external_id=item.external_id,
                    outcome="not_run",
                    attempts=0,
                    tokens_spent=0,
                    prompt_version=None,
                    reading=None,
                    discarded=(),
                    note=stop_reason,
                )
            )
            continue

        discarded = _discards_from_caplog(list(caplog.records))
        item_runs.append(
            _ItemRun(
                external_id=item.external_id,
                outcome=result.outcome.value,
                attempts=result.attempts,
                tokens_spent=result.tokens_spent,
                prompt_version=result.prompt_version,
                reading=result.reading,
                discarded=discarded,
            )
        )

        if result.outcome is ReadOutcome.RATE_LIMITED:
            stop_reason = "no ejecutado: límite de tasa alcanzado en el ítem anterior"

    # --- evaluación contra lo confirmado por el autor (T71.b, EXPECTED) ----
    # Solo para los ítems que de verdad produjeron una Reading con la
    # variante de medidas: `evaluate()` está definido por `external_id`
    # (`ra._CASE_EVALUATORS`), así que un outcome distinto de READ no tiene
    # nada que evaluar.
    results: dict[str, ra.CaseResult] = {
        item_run.external_id: ra.evaluate(
            item_run.external_id,
            item_run.reading.measurements or (),
            abstract_by_id[item_run.external_id]["abstract"],
        )
        for item_run in item_runs
        if item_run.outcome == ReadOutcome.READ.value
        and item_run.reading is not None
        and item_run.reading.measurements is not None
    }

    # --- informe, INMEDIATAMENTE tras procesar/evaluar ----------------------
    # Sin ningún paso opcional después (a diferencia de T71.b, este humo no
    # cruza nada con el NASA Exoplanet Archive, ver el docstring del
    # módulo): las cuatro llamadas reales ya se han pagado en este punto,
    # así que su resultado se escribe a disco antes de arriesgarse a los
    # asserts de contabilidad de más abajo.
    report_path = _output_path(tmp_path, "report.txt")
    report_text = _render_report(item_runs, results)
    report_path.write_text(report_text, encoding="utf-8")
    print(f"\n{report_text}\n")
    print(f"--- informe escrito en: {report_path} ---")

    # --- asserts de contabilidad, únicamente (el plan de T71.c, paso 7, lo
    # exige: la calidad de la atribución es informativa, no bloqueante) -----
    with db_session_factory() as check_session:
        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        db_tokens_used = agent_calls.tokens_used_for_run(run_id)
        db_reader_calls = agent_calls.count_for_run(run_id, AgentRole.READER)
        call_rows = list(
            check_session.execute(
                select(AgentCallRow).where(AgentCallRow.run_id == run_id)
            ).scalars()
        )

    total_tokens_spent = sum(item_run.tokens_spent for item_run in item_runs)
    total_attempts = sum(item_run.attempts for item_run in item_runs)

    assert db_tokens_used == total_tokens_spent, (
        "el acumulado que lee BudgetGuard (AgentCallRepository.tokens_used_for_run) debe "
        "coincidir exactamente con la suma de tokens_spent de todos los ítems procesados"
    )
    assert db_reader_calls == total_attempts == len(call_rows), (
        "el número de intentos de este humo debe coincidir con el número de filas "
        "AgentCall (rol reader) para este run_id"
    )

    max_calls_ceiling = policy.max_calls_per_item * policy.max_items_per_night
    assert total_attempts <= max_calls_ceiling, (
        f"el número de llamadas ({total_attempts}) supera el tope de "
        f"max_calls_per_item * max_items_per_night ({max_calls_ceiling}) que ya debería "
        "haber impuesto BudgetGuard.check (regla 9, application/budget.py)"
    )

    for row in call_rows:
        assert row.prompt_version == READER_V3_PROMPT_VERSION, (
            f"la fila AgentCall {row.id} tiene prompt_version={row.prompt_version!r}; los "
            "cuatro ítems de este humo son astro-ph.EP, dentro de "
            "[reader] measurement_categories, así que ReadItem no debería haber elegido "
            "reader-v2 para ninguno"
        )

    # --- cota literal del plan de T71.c, paso 7 (NO se deduce del invariante
    # que garantiza BudgetGuard.check: ese invariante solo acota lo gastado
    # ANTES de cada llamada, nunca lo que esa llamada cueste de verdad una
    # vez ejecutada) ----------------------------------------------------------
    token_ceiling = _TEST_RUN_BUDGET_TOKENS + config.budget.reader_v3_estimated_tokens
    assert db_tokens_used <= token_ceiling, (
        f"el gasto real ({db_tokens_used}) supera el tope nocturno de la política de "
        f"prueba ({_TEST_RUN_BUDGET_TOKENS}) más la estimación de una llamada de la "
        f"variante reader-v3 ({config.budget.reader_v3_estimated_tokens}), la cota "
        "literal fijada por el plan de T71.c, paso 7"
    )
