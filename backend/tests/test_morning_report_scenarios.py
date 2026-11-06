"""T105: escenarios del informe matinal, contrato con `RunNight._build_notes` y pureza de imports.

Sin base, sin Claude, sin `osascript` ni red. El contrato ejecuta `RunNight` de verdad (con
`FakeLLMProvider` y repositorios en memoria) y pasa `result.notes` por `classify_night`: si
`_build_notes` cambia de formato, estos tests rompen antes de que el informe mienta.
"""

from __future__ import annotations

import ast
import itertools
import subprocess
import sys
import textwrap
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest
from fakes.llm import FakeLLMProvider
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    make_item,
    make_policy,
    make_run_night,
    valid_reading_json,
)
from test_morning_report import (
    NIGHT,
    NOTES_ARXIV,
    _build,
    _call,
    _Data,
    _run,
    _snapshot,
)

from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.morning_report import (
    LatestDigest,
    LaunchEvidence,
    Trigger,
    render_notification,
    render_report,
    should_notify,
)
from nocturna.domain.entities import AgentCallStatus, RunStatus
from nocturna.domain.errors import LLMError
from nocturna.domain.llm import AgentRole
from nocturna.domain.night_report import NightCause, NightEvidence, classify_night
from nocturna.infrastructure.arxiv.transport import ArxivUnavailable

SRC = Path(__file__).resolve().parents[1] / "src" / "nocturna"

# --- Caso de uso ------------------------------------------------------------------------


def test_noche_sin_run_y_hueco_de_tres_noches_en_las_ultimas_siete():
    runs = [
        _run(date(2026, 10, 30), RunStatus.COMPLETED, "ingest=ok fetched=1 new=1 | end=edited"),
        _run(date(2026, 11, 3), RunStatus.PARTIAL, NOTES_ARXIV),
    ]

    report = _build(_Data(runs))
    by_day = dict(report.recent_nights)

    assert report.run_status is None
    assert report.classification.causes == (NightCause.NOT_LAUNCHED,)
    assert [by_day[date(2026, 10, d)] for d in (31,)] == [None]
    assert by_day[date(2026, 11, 1)] is None
    assert by_day[date(2026, 11, 2)] is None
    assert by_day[NIGHT] is None
    assert by_day[date(2026, 10, 30)] is RunStatus.COMPLETED
    assert by_day[date(2026, 11, 3)] is RunStatus.PARTIAL
    text = render_report(report)
    assert text.count("sin Run") >= 5


def test_un_run_item_dentro_de_la_ventana_no_sustituye_al_run_de_la_noche():
    night = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV)
    item_run = _run(NIGHT, RunStatus.COMPLETED, "")
    object.__setattr__(item_run, "started_at", night.started_at.replace(hour=3, minute=30))
    no_notes = _run(NIGHT, RunStatus.COMPLETED, "")
    no_notes.notes = None
    object.__setattr__(no_notes, "started_at", night.started_at.replace(hour=4, minute=0))

    report = _build(_Data([night, item_run, no_notes]))

    assert report.run_status is RunStatus.PARTIAL
    assert report.classification.causes == (NightCause.ARXIV_UNAVAILABLE,)


def test_los_tokens_salen_de_las_llamadas_y_no_del_contador_del_run():
    run = _run(NIGHT, RunStatus.COMPLETED, "ingest=ok fetched=1 new=1 | end=edited")
    assert run.tokens_used == 0

    report = _build(_Data([run], tokens=4_321))

    assert report.tokens_used == 4_321
    assert "Tokens: 4.321 / 300.000 (1 %)" in render_report(report)


def _scenarios():
    run_partial = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV, published=9, read=7)
    calls = [_call(AgentCallStatus.OK, run_partial) for _ in range(5)]
    evidence = LaunchEvidence(
        launched=True,
        wrapper_exits={"run-night": 7, "evaluate-tensions": 4},
        archive_exits={"archive-snapshot": 3},
        logs=("night-20261105.out.log", "night-20261105.err.log"),
    )
    breaker = _run(
        NIGHT,
        RunStatus.PARTIAL,
        "ingest=error:ArxivUnavailable fetched=0 new=0 | expired=0:Boom | read=1(failed=1) | "
        "candidates=0 published=0 | tokens=1 | end=circuit_breaker_popularizer",
    )
    return {
        "con_run": (
            _Data(
                [run_partial],
                calls,
                digest=LatestDigest("2026-W44", 123_456, "failed"),
                snapshot=_snapshot(),
            ),
            evidence,
        ),
        "con_run_con_causa_larga": (
            _Data([breaker], [_call(AgentCallStatus.ERROR, breaker)]),
            evidence,
        ),
        "sin_run": (_Data([]), LaunchEvidence(launched=False)),
        "sin_run_con_centinela_y_basura": (
            _Data([]),
            LaunchEvidence(launched=True, wrapper_exits={"zzz" * 20: 99, "run-night": 1}),
        ),
        "db_inaccesible": (_Data([], fail=True), evidence),
        "sin_lector": (None, evidence),
    }


@pytest.mark.parametrize("name", list(_scenarios()))
def test_el_informe_cabe_en_una_pantalla_en_todos_los_escenarios(name):
    data, evidence = _scenarios()[name]

    report = _build(data, evidence)
    text = render_report(report)
    title, body = render_notification(report)

    lines = text.splitlines()
    assert len(lines) <= 15
    assert all(len(line) <= 100 for line in lines)
    assert "\n" not in title and "\n" not in body
    assert title and body


# --- should_notify: todas las combinaciones ---------------------------------------------


@pytest.mark.parametrize(
    ("trigger", "status", "delivered"),
    list(itertools.product(Trigger, [*RunStatus, None], [True, False])),
)
def test_should_notify_en_todas_las_combinaciones(trigger, status, delivered):
    notes = "ingest=ok fetched=1 new=1 | end=edited"
    runs = [_run(NIGHT, status, notes)] if status is not None else []
    report = replace(_build(_Data(runs)), trigger=trigger)
    assert report.run_status is status

    expected = True
    if trigger is Trigger.SCHEDULED and status is RunStatus.COMPLETED and delivered:
        expected = False

    assert should_notify(report, delivered) is expected


# --- Contrato con RunNight._build_notes -------------------------------------------------


async def _run_night(scenario: str):
    item = make_item()
    env = Environment(items=[item], policy=make_policy(), now=WITHIN_WINDOW)
    fake = FakeLLMProvider()
    items = [item]
    kwargs: dict[str, object] = {}

    if scenario == "arxiv":
        fake.respond(AgentRole.READER, json=valid_reading_json(), tokens_in=100, tokens_out=10)
        fake.respond(AgentRole.POPULARIZER, json={}, tokens_in=1, tokens_out=1)
        fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=10)
    elif scenario == "breaker":
        items = [make_item() for _ in range(4)]
        env = Environment(items=items, policy=make_policy(), now=WITHIN_WINDOW)
        for _ in range(2):
            fake.fail(AgentRole.READER, error=LLMError("sin tokens", tokens_in=0, tokens_out=0))
        kwargs["max_consecutive_failures"] = 2
        fake.respond(AgentRole.EDITOR, json={"publish": []}, tokens_in=100, tokens_out=10)
    else:  # hard_stop
        import asyncio  # noqa: PLC0415

        class _Slow:
            async def run_agent(self, request):
                await asyncio.sleep(10)

        fake = _Slow()  # type: ignore[assignment]
        kwargs["deadline_s"] = 0.05

    ingest = IngestResult(
        fetched=len(items), new=len(items), duplicates=0, skipped=0, truncated=False, items=items
    )
    run_night = make_run_night(env=env, provider=fake, ingest_result=ingest, **kwargs)
    if scenario == "arxiv":

        async def _failing() -> IngestResult:
            raise ArxivUnavailable("406 tras 4 intentos")

        run_night._ingest = _failing  # noqa: SLF001
    return await run_night(), env


@pytest.mark.anyio
async def test_contrato_ingesta_con_arxiv_unavailable_se_clasifica_como_arxiv():
    result, _ = await _run_night("arxiv")

    assert "ingest=error:ArxivUnavailable" in result.notes
    classification = classify_night(
        NightEvidence(run_status=result.status, notes=result.notes, launched=True)
    )

    assert classification.causes == (NightCause.ARXIV_UNAVAILABLE,)
    assert classification.detail == "ArxivUnavailable"


@pytest.mark.anyio
async def test_contrato_cortacircuitos_del_reader_se_clasifica_como_cortacircuitos():
    result, env = await _run_night("breaker")

    assert "end=circuit_breaker_reader" in result.notes
    failed = [c for c in env.agent_calls.calls if c.status is not AgentCallStatus.OK]
    classification = classify_night(
        NightEvidence(
            run_status=result.status,
            notes=result.notes,
            launched=True,
            failed_calls=len(failed),
            failed_calls_zero_tokens=sum(1 for c in failed if c.total_tokens == 0),
        )
    )

    assert classification.primary is NightCause.CIRCUIT_BREAKER
    assert NightCause.RATE_LIMITED not in classification.causes
    assert classification.detail.startswith("reader: ")


@pytest.mark.anyio
async def test_contrato_hard_stop_se_clasifica_como_hard_stop():
    result, _ = await _run_night("hard_stop")

    assert result.status is RunStatus.KILLED
    assert "end=hard_stop" in result.notes
    classification = classify_night(
        NightEvidence(run_status=result.status, notes=result.notes, launched=True)
    )

    assert classification.causes == (NightCause.HARD_STOP,)


# --- Pureza de imports ------------------------------------------------------------------

_NEW_MODULES = [
    "domain/night_report.py",
    "application/use_cases/morning_report.py",
    "infrastructure/ops/launch_log.py",
    "infrastructure/ops/notifier.py",
    "infrastructure/ops/report_file.py",
    "infrastructure/db/morning_report_data.py",
]
_FORBIDDEN = (
    "nocturna.infrastructure.llm",
    "nocturna.application.budget",
    "nocturna.application.agents",
    "claude_agent_sdk",
)


def _imported(path: Path) -> list[str]:
    names: list[str] = []
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
            names += [f"{node.module}.{a.name}" for a in node.names]
    return names


@pytest.mark.parametrize("module", _NEW_MODULES)
def test_los_modulos_del_informe_no_importan_llm_presupuesto_ni_agentes(module):
    path = SRC / module
    assert path.is_file()

    leaked = [
        n for n in _imported(path) if any(n == f or n.startswith(f + ".") for f in _FORBIDDEN)
    ]

    assert leaked == []


def test_importar_los_modulos_del_informe_no_carga_claude_en_sys_modules():
    script = textwrap.dedent(
        """
        import sys
        import nocturna.domain.night_report
        import nocturna.application.use_cases.morning_report
        import nocturna.infrastructure.ops.launch_log
        import nocturna.infrastructure.ops.notifier
        import nocturna.infrastructure.ops.report_file
        import nocturna.infrastructure.db.morning_report_data
        import nocturna.cli
        bad = sorted(n for n in sys.modules
                     if n == "claude_agent_sdk" or n.startswith("claude_agent_sdk.")
                     or n.startswith("nocturna.infrastructure.llm"))
        print("LEAK", bad) if bad else print("OK")
        sys.exit(1 if bad else 0)
        """
    )

    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=60, check=False
    )

    assert result.returncode == 0, result.stdout + result.stderr
