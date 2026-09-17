"""Tests de `RunNight` (T44, paso 2) y de la apertura de `Run` de `run-night`
(`cli._current_or_new_run_night`, T44, paso 3) contra PostgreSQL real.

Complementa `tests/db/test_cli_run_night_db.py`, que ejercita `run-night` de
punta a punta vía `nocturna.cli::main` (`argparse`, ingesta arXiv servida
por `httpx.MockTransport`). Este fichero construye `RunNight` directamente
con `FakeLLMProvider`, igual que `tests/db/test_edit_night_db.py` construye
`EditNight` -- sin pasar por `main()`, con un `ingest` en memoria que no
persiste nada, así los `Item` que cada test siembra a mano son los únicos
candidatos de la noche -- y ejercita `cli._current_or_new_run_night` como
función aislada, no como parte de una noche completa: dos costuras
distintas, dos ficheros distintos.

`RunNight` no cierra el `Run` (ver su docstring, "Lo que este módulo NO
hace"): los tests que necesitan comprobar la fila `runs` ya cerrada llaman a
`cli._finish_run`, el mismo cerrador que usa `_run_night_for_real` -- se
importa y se llama tal cual, nunca se reimplementa aquí.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from uuid import UUID

import pytest
from factories import aware, make_agent_call, make_item, make_run
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from sqlalchemy import select

from nocturna import cli
from nocturna.application.agents.prompt_loader import (
    EDITOR_PROMPT_VERSION,
    POPULARIZER_PROMPT_VERSION,
    READER_PROMPT_VERSION,
    load_prompt,
)
from nocturna.application.budget import BudgetPolicy, effective_nightly_tokens
from nocturna.application.use_cases.edit_night import EditNight
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.popularize_reading import PopularizeReading
from nocturna.application.use_cases.read_item import ReadItem
from nocturna.application.use_cases.run_night import RunNight
from nocturna.domain.entities import ItemStatus, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.models import FindingRow, ItemRow, RunRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

pytestmark = pytest.mark.anyio

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)
_MODEL_SONNET = "claude-sonnet-test"
_MODEL_OPUS = "claude-opus-test"


def _policy(**overrides: object) -> BudgetPolicy:
    """Mismo helper que `tests/db/test_edit_night_db.py::_policy`: valores
    de `BudgetPolicy` fijados a mano, no leídos de `config/pipeline.toml`,
    para que estos tests no dependan de la calibración real de T60."""
    defaults: dict[str, object] = {
        "nightly_tokens": 300_000,
        "editor_reserve_tokens": 60_000,
        "max_items_per_night": 10,
        "max_turns_per_agent": 3,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 2,
        "item_timeout_s": 180,
        "editor_timeout_s": 300,
        "run_timeout_s": 16_200,
        "window_start": time(0, 0),
        "window_hard_stop": time(4, 45),
        "weekly_reset_weekday": 0,
        "weekly_reset_hour": 0,
        "reset_day_multiplier": 1.0,
    }
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


def _valid_reading_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "summary": "Resumen generado por el FakeLLMProvider.",
        "objects": ["NGC 1234"],
        "claims": ["Una afirmación de prueba."],
        "interest_score": 5,
    }
    defaults.update(overrides)
    return defaults


def _valid_popularizer_json(**overrides: object) -> dict:
    defaults: dict[str, object] = {
        "title": "Un titular generado por el FakeLLMProvider",
        "level_curious": "Nivel curioso de prueba.",
        "level_amateur": "Nivel aficionado de prueba.",
        "level_technical": "Nivel técnico de prueba.",
    }
    defaults.update(overrides)
    return defaults


async def _empty_ingest() -> IngestResult:
    """`RunNight` exige un `ingest` por constructor; este no persiste nada,
    así que los `Item` sembrados a mano por cada test son los únicos
    candidatos de la noche -- mismo motivo que
    `tests/db/test_cli_run_night_db.py::_patch_empty_arxiv_feed`, sin tocar
    `httpx` porque aquí no se pasa por `_run_ingest`."""
    return IngestResult(fetched=0, new=0, duplicates=0, skipped=0, truncated=False, items=[])


def _build_run_night(
    *,
    db_session_factory,
    run_id: UUID,
    policy: BudgetPolicy,
    clock: FakeClock,
    fake: FakeLLMProvider,
) -> RunNight:
    work = cli._agent_work_factory(db_session_factory, run_id, policy, clock)
    read_item = ReadItem(
        work=work,
        provider=fake,
        system_prompt=load_prompt("reader"),
        prompt_version=READER_PROMPT_VERSION,
        model=_MODEL_SONNET,
        max_turns=policy.max_turns_per_agent,
        estimated_tokens=1_500,
        max_attempts=policy.max_calls_per_item,
    )
    popularize = PopularizeReading(
        work=work,
        provider=fake,
        system_prompt=load_prompt("popularizer"),
        prompt_version=POPULARIZER_PROMPT_VERSION,
        model=_MODEL_SONNET,
        max_turns=policy.max_turns_per_agent,
        estimated_tokens=1_500,
        max_attempts=policy.max_calls_per_item,
        min_interest_score=4,
    )
    edit_night = EditNight(
        work=work,
        provider=fake,
        clock=clock,
        system_prompt=load_prompt("editor"),
        prompt_version=EDITOR_PROMPT_VERSION,
        model=_MODEL_OPUS,
        max_turns=policy.max_turns_per_agent,
        max_attempts=policy.max_editor_calls_per_night,
        base_tokens=1_000,
        tokens_per_candidate=200,
    )
    return RunNight(
        work=work,
        clock=clock,
        ingest=_empty_ingest,
        read_item=read_item,
        popularize=popularize,
        edit_night=edit_night,
        run_id=run_id,
        max_items=policy.max_items_per_night,
        max_consecutive_failures=5,
        deadline_s=3_600,
    )


# --- 1. Noche completa de extremo a extremo --------------------------------


async def test_noche_completa_persiste_findings_items_y_cuadra_tokens_usados(
    db_session_factory: object,
) -> None:
    """Dos `Item` `NEW`: el Editor publica uno y descarta el otro. Al cerrar
    el `Run` con `cli._finish_run` (el mismo cerrador que usa
    `_run_night_for_real`), la fila `runs` queda `completed`, los `Finding`
    llevan `confidence`/`published_at` solo el aprobado, los `Item` quedan
    `published`/`discarded`, y `runs.tokens_used` coincide exactamente con
    la suma de `agent_calls.tokens_in + tokens_out` de ese Run -- la
    columna es una caché desnormalizada (`SqlAlchemyRunRepository.save`),
    y este test es quien comprueba que, al final de una noche real, esa
    caché no se ha desincronizado."""
    policy = _policy()
    clock = FakeClock(_WITHIN_WINDOW)

    item_a = make_item(external_id="2601.00020")
    item_b = make_item(external_id="2601.00021")
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyRunRepository(session).add(
            run := make_run(started_at=_WITHIN_WINDOW, budget_tokens=policy.nightly_tokens)
        )
        SqlAlchemyItemRepository(session).add_many([item_a, item_b])
        session.flush()
        run_id = run.id
        item_a_id = item_a.id
        item_b_id = item_b.id

    fake = FakeLLMProvider()
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1000, tokens_out=200)
    fake.respond(AgentRole.READER, json=_valid_reading_json(), tokens_in=1100, tokens_out=210)
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=800, tokens_out=150
    )
    fake.respond(
        AgentRole.POPULARIZER, json=_valid_popularizer_json(), tokens_in=820, tokens_out=160
    )
    fake.respond(
        AgentRole.EDITOR,
        json={
            "publish": [
                {"item_id": str(item_a_id), "confidence": 0.75, "reason": "hallazgo relevante"}
            ]
        },
        tokens_in=1200,
        tokens_out=90,
    )

    night = _build_run_night(
        db_session_factory=db_session_factory, run_id=run_id, policy=policy, clock=clock, fake=fake
    )

    result = await night()

    assert result.status is RunStatus.COMPLETED
    assert result.items_read == 2
    assert result.candidates == 2
    assert result.findings_published == 1

    cli._finish_run(
        db_session_factory,
        run_id,
        result.status,
        clock.now(),
        notes=result.notes,
        items_fetched=result.items_fetched,
        items_read=result.items_read,
        findings_published=result.findings_published,
    )

    with db_session_factory() as check_session:
        run_row = check_session.get(RunRow, run_id)
        assert run_row is not None
        assert run_row.status is RunStatus.COMPLETED
        assert run_row.finished_at is not None

        item_a_row = check_session.get(ItemRow, item_a_id)
        item_b_row = check_session.get(ItemRow, item_b_id)
        assert item_a_row is not None and item_a_row.status is ItemStatus.PUBLISHED
        assert item_b_row is not None and item_b_row.status is ItemStatus.DISCARDED

        findings = list(check_session.execute(select(FindingRow)).scalars().all())
        assert len(findings) == 2
        published = next(f for f in findings if f.item_id == item_a_id)
        discarded = next(f for f in findings if f.item_id == item_b_id)
        assert published.confidence == 0.75
        assert published.published_at is not None
        assert discarded.confidence is None
        assert discarded.published_at is None

        agent_calls = SqlAlchemyAgentCallRepository(check_session)
        tokens_used_from_calls = agent_calls.tokens_used_for_run(run_id)
        assert tokens_used_from_calls == 1000 + 200 + 1100 + 210 + 800 + 150 + 820 + 160 + 1200 + 90
        assert run_row.tokens_used == tokens_used_from_calls, (
            "runs.tokens_used (caché desnormalizada) debe cuadrar con la suma real de "
            "agent_calls al cerrar la noche"
        )


# --- 2. Contadores monótonos: actualizarlos a mitad de noche no pisa tokens_used


def test_actualizar_contadores_a_mitad_de_noche_no_pisa_tokens_used(
    db_session_factory: object,
) -> None:
    """`RunRepository.save` (`infrastructure/db/repositories.py`) escribe
    `tokens_used` tal cual traiga la entidad `Run` en memoria -- por eso
    cualquier camino que reutilizara una instancia de `Run` leída ANTES de
    que `record_agent_call` incrementara `tokens_used` pisaría ese
    incremento al guardar. Este test reproduce la secuencia real de una
    noche: dos llamadas contabilizadas cada una en su propia
    `unit_of_work` (mismo patrón que `BudgetGuard.record_call`: relee el
    Run, lo actualiza, lo guarda -- nunca reutiliza una instancia vieja),
    y después `cli._finish_run` actualizando los contadores monótonos
    (`items_fetched`/`items_read`/`findings_published`) que `RunNight`
    propone al final. `_finish_run` relee el `Run` fresco justo antes de
    guardarlo (ver su docstring en `cli.py`), así que `tokens_used` debe
    seguir siendo la suma de las dos llamadas, no `0` ni un valor a medio
    camino -- si algún día `_finish_run` (o cualquier otro cerrador)
    empezara a reutilizar una instancia de `Run` capturada antes de esas
    dos llamadas, este test lo detectaría."""
    policy = _policy()

    with unit_of_work(db_session_factory) as session:
        SqlAlchemyRunRepository(session).add(
            run := make_run(started_at=_WITHIN_WINDOW, budget_tokens=policy.nightly_tokens)
        )
        session.flush()
        run_id = run.id

    call_tokens = [(500, 100), (300, 50)]
    for tokens_in, tokens_out in call_tokens:
        with unit_of_work(db_session_factory) as session:
            runs = SqlAlchemyRunRepository(session)
            agent_calls = SqlAlchemyAgentCallRepository(session)
            current = runs.get(run_id)
            assert current is not None
            call = make_agent_call(run_id, tokens_in=tokens_in, tokens_out=tokens_out)
            agent_calls.add(call)
            current.record_agent_call(call)
            runs.save(current)

    expected_tokens_used = sum(tokens_in + tokens_out for tokens_in, tokens_out in call_tokens)

    cli._finish_run(
        db_session_factory,
        run_id,
        RunStatus.PARTIAL,
        _WITHIN_WINDOW,
        items_fetched=5,
        items_read=3,
        findings_published=1,
    )

    with db_session_factory() as check_session:
        run_row = check_session.get(RunRow, run_id)
        assert run_row is not None
        assert run_row.status is RunStatus.PARTIAL
        assert run_row.items_fetched == 5
        assert run_row.items_read == 3
        assert run_row.findings_published == 1
        assert run_row.tokens_used == expected_tokens_used, (
            "actualizar los contadores monótonos a mitad/al final de la noche no debe "
            "pisar tokens_used con una instancia de Run desactualizada"
        )


# --- 3. Run huérfano en RUNNING: se cierra killed, se abre uno nuevo -------


def test_current_or_new_run_night_cierra_el_huerfano_y_abre_uno_nuevo_sin_integrity_error(
    db_session_factory: object,
) -> None:
    """`cli._current_or_new_run_night`, aislada de una noche completa:
    contra un `Run` `RUNNING` ya en base (el rastro de un `run-night`
    anterior que murió sin cerrarlo), lo cierra `killed` y abre uno nuevo
    `running`, sin violar `uq_runs_status_running` (índice único parcial
    que solo admite una fila `running` a la vez) -- el `session.flush()`
    explícito entre el `UPDATE` que cierra el huérfano y el `INSERT` del
    Run nuevo es justo lo que evita el `IntegrityError` que este test
    comprobaría en rojo si faltara."""
    policy = _policy()
    clock = FakeClock(_WITHIN_WINDOW)

    orphan = make_run(started_at=aware(-120), budget_tokens=250_000)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyRunRepository(session).add(orphan)

    new_run_id = cli._current_or_new_run_night(db_session_factory, policy, clock)

    assert new_run_id != orphan.id

    with db_session_factory() as check_session:
        orphan_row = check_session.get(RunRow, orphan.id)
        assert orphan_row is not None
        assert orphan_row.status is RunStatus.KILLED
        assert orphan_row.finished_at is not None
        assert "quedó RUNNING de un proceso anterior" in orphan_row.notes

        new_row = check_session.get(RunRow, new_run_id)
        assert new_row is not None
        assert new_row.status is RunStatus.RUNNING
        assert new_row.finished_at is None


# --- 4. budget_tokens del Run nuevo es el presupuesto EFECTIVO -------------


def test_current_or_new_run_night_usa_el_presupuesto_efectivo_no_nightly_tokens_a_pelo(
    db_session_factory: object,
) -> None:
    """Cierra, para `_current_or_new_run_night`, la misma decisión abierta
    nº 57 de `docs/OPEN_DECISIONS.md` que ya cierra
    `tests/db/test_cli_run_night_db.py` para el camino completo de
    `main(["run-night"])` -- aquí con un `reset_day_multiplier` distinto de
    `1.0` en el día/hora de reinicio semanal, para que el test no pase por
    casualidad con `policy.nightly_tokens` escrito a pelo: si
    `_current_or_new_run_night` usara ese literal en vez de
    `effective_nightly_tokens(policy, now)`, `budget_tokens` sería
    `100_000`, no `250_000`."""
    now = _WITHIN_WINDOW
    policy = _policy(
        nightly_tokens=100_000,
        reset_day_multiplier=2.5,
        weekly_reset_weekday=now.weekday(),
        weekly_reset_hour=now.hour,
    )
    clock = FakeClock(now)
    expected = effective_nightly_tokens(policy, now)
    assert expected == 250_000, "control del propio test: el multiplicador debe aplicarse"

    new_run_id = cli._current_or_new_run_night(db_session_factory, policy, clock)

    with db_session_factory() as check_session:
        new_row = check_session.get(RunRow, new_run_id)
        assert new_row is not None
        assert new_row.budget_tokens == expected


# --- 5. Aviso de presupuesto completo, por stderr, en cada apertura -------


def test_current_or_new_run_night_avisa_por_stderr_de_presupuesto_completo(
    db_session_factory: object,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Revisión de T44, punto 5: `_current_or_new_run_night` nunca reutiliza
    un `Run` `RUNNING` (a diferencia de `_current_or_new_run`, de
    `run-item`), así que cada llamada abre un presupuesto de noche
    completo -- dos invocaciones de `run-night` en la misma ventana gastan
    2 × `nightly_tokens` sin que nada lo note. `_run_item` ya avisaba por
    `stderr` de esto mismo al abrir su propio `Run`; esta función no
    avisaba de nada. El aviso debe aparecer en cada llamada, con o sin
    huérfano que cerrar antes."""
    policy = _policy()
    clock = FakeClock(_WITHIN_WINDOW)

    new_run_id = cli._current_or_new_run_night(db_session_factory, policy, clock)

    captured = capsys.readouterr()
    assert str(new_run_id) in captured.err
    assert "presupuesto de noche completo" in captured.err
    assert "nightly_tokens" in captured.err
