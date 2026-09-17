"""Tests de `infrastructure/logging.py`.

Ningún test toca la red ni la base de datos; se ejercita `_JsonFormatter`
directamente (sin pasar por `sys.stderr`) salvo el test que verifica que
`configure_json_logging` escribe ahí y no en `stdout`, y el test de
`night.item` (T44, paso 4) que hace correr `RunNight` de verdad (sin base
de datos, `FakeLLMProvider`, `.claude/skills/testing-without-claude`) para
comprobar la forma real de ese evento -- por eso vive aquí y no en
`test_run_night.py`: es una prueba sobre el *formato* del log, no sobre el
comportamiento de `RunNight`.
"""

import json
import logging
import sys
import uuid
from datetime import UTC, datetime, time
from uuid import uuid4

import pytest
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryFindingRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    make_work_factory,
)

from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.application.use_cases import run_night as run_night_module
from nocturna.application.use_cases.edit_night import EditNight
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.popularize_reading import PopularizeReading
from nocturna.application.use_cases.read_item import ReadItem
from nocturna.application.use_cases.run_night import RunNight
from nocturna.domain.entities import Item, Run
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.logging import _JsonFormatter, configure_json_logging


def _make_record(
    *, msg: str = "hola", level: int = logging.INFO, args: tuple = (), extra: dict | None = None
) -> logging.LogRecord:
    record = logging.LogRecord(
        name="nocturna.test",
        level=level,
        pathname=__file__,
        lineno=1,
        msg=msg,
        args=args,
        exc_info=None,
    )
    for key, value in (extra or {}).items():
        setattr(record, key, value)
    return record


def test_cada_registro_es_una_linea_json_parseable_con_los_campos_base():
    record = _make_record(msg="mensaje de prueba")

    line = _JsonFormatter().format(record)
    payload = json.loads(line)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "nocturna.test"
    assert payload["event"] is None
    assert payload["message"] == "mensaje de prueba"
    # ts es ISO 8601 parseable.
    datetime.fromisoformat(payload["ts"])


def test_los_campos_de_extra_se_vuelcan_en_el_json():
    record = _make_record(
        msg="llamada al Reader",
        extra={"event": "agent_call", "run_id": "abc-123", "tokens_in": 500},
    )

    payload = json.loads(_JsonFormatter().format(record))

    assert payload["event"] == "agent_call"
    assert payload["run_id"] == "abc-123"
    assert payload["tokens_in"] == 500


def test_un_uuid_en_extra_no_revienta_la_serializacion():
    item_id = uuid.uuid4()
    record = _make_record(msg="ítem procesado", extra={"item_id": item_id})

    payload = json.loads(_JsonFormatter().format(record))

    assert payload["item_id"] == str(item_id)


def test_un_datetime_en_extra_no_revienta_la_serializacion():
    when = datetime(2026, 9, 17, 3, 30, tzinfo=UTC)
    record = _make_record(msg="corte de hard_stop", extra={"hard_stop_at": when})

    payload = json.loads(_JsonFormatter().format(record))

    assert payload["hard_stop_at"] == str(when)


def test_una_excepcion_en_extra_no_revienta_la_serializacion():
    record = _make_record(msg="fallo capturado", extra={"cause": ValueError("boom")})

    payload = json.loads(_JsonFormatter().format(record))

    assert "boom" in payload["cause"]


def test_exc_info_se_incluye_como_texto_formateado():
    try:
        raise RuntimeError("algo revento")
    except RuntimeError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="nocturna.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=1,
        msg="fallo con traceback",
        args=(),
        exc_info=exc_info,
    )

    payload = json.loads(_JsonFormatter().format(record))

    assert "RuntimeError" in payload["exc_info"]
    assert "algo revento" in payload["exc_info"]


def test_un_objeto_cuyo_str_revienta_no_tumba_el_formateador():
    class _Radioactive:
        def __str__(self) -> str:
            raise RuntimeError("str() también revienta")

    record = _make_record(msg="caso extremo", extra={"payload": _Radioactive()})

    line = _JsonFormatter().format(record)
    payload = json.loads(line)

    assert payload["message"] == "caso extremo"
    assert payload.get("log_serialization_error") is True


def test_mensaje_no_formateable_no_tumba_el_formateador():
    # "%s %s" con un solo argumento: record.getMessage() lanza TypeError.
    record = _make_record(msg="%s %s", args=("solo uno",))

    line = _JsonFormatter().format(record)
    payload = json.loads(line)

    assert "no formateable" in payload["message"]


def test_configure_json_logging_escribe_json_en_stderr_no_en_stdout(capsys):
    configure_json_logging(level=logging.INFO)
    logger = logging.getLogger("nocturna.test.configure")

    logger.info("evento de prueba", extra={"event": "smoke"})

    captured = capsys.readouterr()
    assert captured.out == ""
    lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(lines) == 1
    payload = json.loads(lines[0])
    assert payload["event"] == "smoke"
    assert payload["message"] == "evento de prueba"


def test_configure_json_logging_es_idempotente_no_duplica_handlers(capsys):
    configure_json_logging(level=logging.INFO)
    configure_json_logging(level=logging.INFO)
    logger = logging.getLogger("nocturna.test.configure_idempotente")

    logger.info("una sola línea")

    captured = capsys.readouterr()
    lines = [line for line in captured.err.splitlines() if line.strip()]
    assert len(lines) == 1


@pytest.fixture(autouse=True)
def _restore_root_logger_handlers():
    """Los tests de `configure_json_logging` mutan el logger raíz; se
    restaura tras cada test para no filtrar handlers a otros ficheros de
    test que corran en el mismo proceso de pytest."""
    root = logging.getLogger()
    original_handlers = list(root.handlers)
    original_level = root.level
    yield
    root.handlers.clear()
    root.handlers.extend(original_handlers)
    root.setLevel(original_level)


# --- 21. night.item: un registro por ítem y fase, sin duplicar AgentCall --

_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)

#: Campos que son de `agent_calls` (la fuente de verdad del gasto, docstring
#: de `run_night.py`, "items_failed es una métrica de la fase A") y que
#: `night.item` NO debe duplicar por intento: un log JSON no es una segunda
#: base de datos del gasto.
_AGENT_CALL_ONLY_FIELDS = frozenset({"tokens_in", "tokens_out", "model", "prompt_version"})

#: Campos exigidos en todo registro `night.item` (más `interest_score` en la
#: fase del Reader con desenlace `READ`, o `finding_id` en la del
#: Popularizer con desenlace `POPULARIZED` -- ninguno de los dos es
#: universal, así que no entran en este conjunto base).
_REQUIRED_NIGHT_ITEM_FIELDS = frozenset(
    {"item_id", "external_id", "phase", "outcome", "attempts", "tokens_spent", "duration_ms"}
)


def _night_item_policy() -> BudgetPolicy:
    return BudgetPolicy(
        nightly_tokens=100_000,
        editor_reserve_tokens=10_000,
        max_items_per_night=10,
        max_turns_per_agent=3,
        max_editor_calls_per_night=2,
        max_calls_per_item=5,
        item_timeout_s=180,
        editor_timeout_s=300,
        run_timeout_s=16_200,
        window_start=time(0, 0),
        window_hard_stop=time(4, 45),
        weekly_reset_weekday=0,
        weekly_reset_hour=0,
        reset_day_multiplier=1.0,
    )


def _night_item_item() -> Item:
    return Item(
        source="arxiv",
        external_id=f"2501.{uuid4().hex[:5]}",
        title="Un título de prueba",
        abstract="Un abstract de prueba con contenido suficiente para el Reader.",
        categories=["astro-ph.GA"],
        published_at=_WITHIN_WINDOW,
        fetched_at=_WITHIN_WINDOW,
    )


async def _run_minimal_night_for_night_item_log() -> None:
    """Una noche mínima -- un ítem, leído y divulgado con éxito, sin llegar
    al Editor -- solo para observar la forma real de los dos `night.item`
    que produce (fase Reader, fase Popularizer). Construida a mano, no
    importada de `test_run_night.py`: este fichero prueba el *formato* del
    log, no el comportamiento de `RunNight`, y no debe acoplarse a los
    helpers de ese otro fichero de test."""
    item = _night_item_item()
    policy = _night_item_policy()
    run = Run(started_at=_WITHIN_WINDOW, budget_tokens=policy.nightly_tokens)
    runs = InMemoryRunRepository(run)
    items = InMemoryItemRepository(item)
    readings = InMemoryReadingRepository()
    findings = InMemoryFindingRepository()
    agent_calls = InMemoryAgentCallRepository()
    clock = FakeClock(_WITHIN_WINDOW)
    guard = BudgetGuard(
        run_id=run.id, policy=policy, runs=runs, agent_calls=agent_calls, clock=clock
    )
    work = make_work_factory(
        guard=guard,
        runs=runs,
        items=items,
        readings=readings,
        findings=findings,
        agent_calls=agent_calls,
    )

    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.READER,
        json={
            "summary": "Resumen de prueba",
            "objects": ["NGC 1234"],
            "claims": ["Una afirmación de prueba"],
            "interest_score": 5,
        },
        tokens_in=1000,
        tokens_out=200,
    )
    fake.respond(
        AgentRole.POPULARIZER,
        json={
            "title": "Un titular de prueba",
            "level_curious": "Nivel curioso",
            "level_amateur": "Nivel aficionado",
            "level_technical": "Nivel técnico",
        },
        tokens_in=800,
        tokens_out=150,
    )
    fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=10)

    read_item = ReadItem(
        work=work,
        provider=fake,
        system_prompt="prompt del Reader",
        prompt_version="reader-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=500,
        max_attempts=2,
    )
    popularize = PopularizeReading(
        work=work,
        provider=fake,
        system_prompt="prompt del Popularizer",
        prompt_version="popularizer-v1",
        model="claude-sonnet-test",
        max_turns=3,
        estimated_tokens=500,
        max_attempts=2,
        min_interest_score=4,
    )
    edit_night = EditNight(
        work=work,
        provider=fake,
        clock=clock,
        system_prompt="prompt del Editor",
        prompt_version="editor-v1",
        model="claude-opus-test",
        max_turns=3,
        max_attempts=2,
        base_tokens=1_000,
        tokens_per_candidate=200,
    )

    async def _ingest() -> IngestResult:
        return IngestResult(
            fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[item]
        )

    run_night = RunNight(
        work=work,
        clock=clock,
        ingest=_ingest,
        read_item=read_item,
        popularize=popularize,
        edit_night=edit_night,
        run_id=run.id,
        max_items=10,
        max_consecutive_failures=5,
        deadline_s=16_200,
    )
    await run_night()


@pytest.mark.anyio
async def test_night_item_un_registro_por_item_y_fase_sin_duplicar_agent_call(caplog, monkeypatch):
    # `tests/db/` corre antes que este fichero en orden alfabético al
    # lanzar la suite completa, y su fixture de migraciones invoca
    # `alembic/env.py::fileConfig`, que deshabilita cualquier logger ya
    # existente y no declarado en `alembic.ini` -- incluido el de
    # `run_night.py`. Mismo parche que `test_agent_runner.py`/
    # `test_read_item.py`/`test_json_repair.py`/`test_run_night.py`.
    monkeypatch.setattr(run_night_module._logger, "disabled", False)

    with caplog.at_level(logging.INFO, logger="nocturna.application.use_cases.run_night"):
        await _run_minimal_night_for_night_item_log()

    night_item_records = [
        record for record in caplog.records if record.__dict__.get("event") == "night.item"
    ]
    # Un ítem, dos fases (Reader y Popularizer) con éxito en ambas: dos
    # registros `night.item`, ni uno más -- la fase del Editor no genera
    # `night.item` (no lee ítems uno a uno, ver `night.editor`).
    assert len(night_item_records) == 2
    phases = {record.__dict__["phase"] for record in night_item_records}
    assert phases == {"reader", "popularizer"}

    for record in night_item_records:
        payload = json.loads(_JsonFormatter().format(record))

        for field in _REQUIRED_NIGHT_ITEM_FIELDS:
            assert field in payload, f"falta el campo obligatorio '{field}' en {payload}"

        duplicated = _AGENT_CALL_ONLY_FIELDS & payload.keys()
        assert duplicated == set(), (
            f"night.item no debe duplicar campos por intento que ya vive en AgentCall "
            f"(la fuente de verdad del gasto): {duplicated} presentes en {payload}"
        )

        if payload["phase"] == "reader":
            assert payload["outcome"] == "read"
            assert payload["interest_score"] == 5
            assert "finding_id" not in payload
        else:
            assert payload["phase"] == "popularizer"
            assert payload["outcome"] == "popularized"
            assert "finding_id" in payload
            assert "interest_score" not in payload
