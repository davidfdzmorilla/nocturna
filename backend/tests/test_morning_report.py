"""T105: caso de uso `BuildMorningReport`, presentación del informe y regla de aviso.

Sin base, sin Claude y sin `osascript`: lector, registro de lanzamiento y reloj son falsos.
"""

from __future__ import annotations

import contextlib
import logging
from dataclasses import replace
from datetime import UTC, date, datetime, time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

import pytest

from nocturna.application.use_cases.morning_report import (
    BuildMorningReport,
    LatestDigest,
    LaunchEvidence,
    ReportDataUnavailable,
    Trigger,
    delivery_line,
    render_notification,
    render_report,
    should_notify,
)
from nocturna.domain.archive import ArchiveSnapshot, SnapshotKind
from nocturna.domain.entities import AgentCall, AgentCallStatus, Run, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.domain.night_report import NightCause

TZ = ZoneInfo("Europe/Madrid")
NIGHT = date(2026, 11, 5)
NOW = datetime(2026, 11, 5, 7, 30, tzinfo=TZ)
NOTES_ARXIV = (
    "ingest=error:ArxivUnavailable fetched=0 new=0 | expired=0 | read=7(failed=0) | "
    "candidates=9 published=9 | tokens=183182 | end=ingest_error"
)


@contextlib.contextmanager
def _capture_logs(name: str):
    """Captura los registros de un logger con su formato JSON real, sea cual sea el
    estado global del logging que hayan dejado otros tests."""
    from nocturna.infrastructure.logging import _JsonFormatter

    lines: list[str] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(_JsonFormatter().format(record))

    logger = logging.getLogger(name)
    handler = _H()
    old = (logger.level, logger.disabled, logger.propagate)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    logging.disable(logging.NOTSET)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)
        logger.level, logger.disabled, logger.propagate = old


class _Clock:
    def now(self) -> datetime:
        return NOW


class _Log:
    def __init__(self, evidence: LaunchEvidence) -> None:
        self._evidence = evidence

    def evidence(self, night: date) -> LaunchEvidence:
        return self._evidence


def _run(
    day: date,
    status: RunStatus,
    notes: str,
    *,
    published: int = 0,
    read: int = 0,
    fetched: int = 0,
) -> Run:
    started = datetime.combine(day, time(0, 5), tzinfo=TZ)
    run = Run(started_at=started, budget_tokens=300_000)
    run.items_fetched = fetched
    run.items_read = read
    run.findings_published = published
    if status is not RunStatus.RUNNING:
        run.finish(status, datetime.combine(day, time(3, 41), tzinfo=TZ), notes)
    else:
        run.notes = notes
    return run


def _snapshot() -> ArchiveSnapshot:
    return ArchiveSnapshot(
        id=uuid4(),
        taken_at=datetime(2026, 10, 31, 10, 0, tzinfo=UTC),
        kind=SnapshotKind.INCREMENTAL,
        max_releasedate=date(2026, 10, 15),
        rows_total=40_112,
        defaults_total=1,
        duplicate_rows=0,
        payload_sha256="x" * 64,
        requests=1,
        duration_ms=1,
    )


class _Data:
    def __init__(
        self,
        runs: list[Run],
        calls: list[AgentCall] | None = None,
        *,
        tokens: int = 183_182,
        digest: LatestDigest | None = None,
        snapshot: ArchiveSnapshot | None = None,
        fail: bool = False,
    ) -> None:
        self.runs, self.calls, self.tokens = runs, calls or [], tokens
        self.digest, self.snapshot, self.fail = digest, snapshot, fail

    def started_between(self, start: datetime, end: datetime) -> list[Run]:
        if self.fail:
            raise ReportDataUnavailable("OperationalError")
        return sorted(
            (r for r in self.runs if start <= r.started_at <= end), key=lambda r: r.started_at
        )

    def calls_for_run(self, run_id: UUID) -> list[AgentCall]:
        return self.calls

    def tokens_used_for_run(self, run_id: UUID) -> int:
        return self.tokens

    def last_snapshot(self) -> ArchiveSnapshot | None:
        return self.snapshot

    def latest_digest(self) -> LatestDigest | None:
        return self.digest


def _call(status: AgentCallStatus, run: Run, agent: AgentRole = AgentRole.READER, t: int = 10):
    return AgentCall(
        run_id=run.id,
        item_id=None,
        agent=agent,
        model="m",
        tokens_in=t,
        tokens_out=0,
        duration_ms=1,
        status=status,
    )


def _build(data: _Data | None, evidence: LaunchEvidence | None = None, **kw):
    return BuildMorningReport(
        data,
        _Log(evidence or LaunchEvidence(launched=False)),
        _Clock(),
        TZ,
        time(0, 0),
        time(4, 45),
        **kw,
    )(NIGHT, Trigger.SCHEDULED)


def test_noche_partial_por_arxiv_con_todos_los_datos():
    run = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV, published=9, read=7)
    ok = [_call(AgentCallStatus.OK, run) for _ in range(40)]
    data = _Data(
        [run],
        [*ok, _call(AgentCallStatus.INVALID_OUTPUT, run)],
        digest=LatestDigest("2026-W44", 12, "written"),
        snapshot=_snapshot(),
    )
    evidence = LaunchEvidence(
        launched=True,
        wrapper_exits={"run-night": 7, "evaluate-tensions": 0},
        archive_exits={"archive-snapshot": 0},
        logs=("night-20261105.out.log", "night-20261105.err.log"),
    )

    report = _build(data, evidence)
    text = render_report(report)

    assert report.classification.causes == (NightCause.ARXIV_UNAVAILABLE,)
    assert report.tokens_used == 183_182
    assert text.splitlines()[:8] == [
        "Nocturna · noche del 2026-11-05 (jue) · generado 07:30 por scheduled",
        "Estado: PARTIAL · Causa: arXiv no disponible en la ingesta (ArxivUnavailable)",
        "Horario: 00:05–03:41 · run-night 7 · evaluate-tensions 0",
        "Tokens: 183.182 / 300.000 (61 %) · llamadas 41: ok 40, inválidas 1, error 0, timeout 0",
        "Ítems: nuevos 0 · leídos 7 (fallidos 0) · publicados 9",
        "Archivo: snapshot 2026-10-31 · 40.112 filas · última publ. 2026-10-15"
        " · archive-snapshot 0",
        "Resumen semanal: 2026-W44 · 12 cambios · texto del redactor: escrito",
        text.splitlines()[7],
    ]
    assert text.splitlines()[7].startswith("Últimas 7 noches: ")
    assert text.splitlines()[-1] == "Logs: night-20261105.out.log · night-20261105.err.log"


def test_formato_acotado_de_lineas_y_columnas():
    run = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV)
    text = render_report(_build(_Data([run], digest=LatestDigest("2026-W44", 1, "none"))))

    assert len(text.splitlines()) <= 15
    assert all(len(line) <= 100 for line in text.splitlines())


def test_noche_sin_run():
    text = render_report(_build(_Data([])))

    assert "Estado: SIN RUN · Causa: no lanzado (Mac apagado, dormido o sin sesión)" in text
    assert "Tokens: —" in text
    assert "Ítems: nuevos — · leídos — (fallidos —) · publicados —" in text
    assert f"{NIGHT:%m-%d} sin Run" in text


def test_sin_run_por_precondiciones():
    evidence = LaunchEvidence(launched=False, wrapper_exits={"precondition": 75})

    report = _build(_Data([]), evidence)

    assert report.classification.causes == (NightCause.PRECONDITIONS,)


def test_ultimas_siete_noches_con_hueco():
    runs = [
        _run(date(2026, 11, 3), RunStatus.COMPLETED, "ingest=ok fetched=1 new=1 | end=edited"),
        _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV),
    ]

    report = _build(_Data(runs))

    assert [d for d, _ in report.recent_nights][0] == date(2026, 10, 30)
    assert report.recent_nights[-1] == (NIGHT, RunStatus.PARTIAL)
    assert dict(report.recent_nights)[date(2026, 11, 4)] is None
    assert dict(report.recent_nights)[date(2026, 11, 3)] is RunStatus.COMPLETED


def test_relecturas_y_run_item_no_son_la_noche():
    reread = _run(NIGHT, RunStatus.COMPLETED, "reread")
    item = _run(NIGHT, RunStatus.COMPLETED, "")

    report = _build(_Data([reread, item]))

    assert report.run_status is None


def test_run_fuera_de_la_ventana_de_la_noche_no_cuenta():
    late = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV)
    object.__setattr__(late, "started_at", datetime(2026, 11, 5, 9, 0, tzinfo=TZ))

    assert _build(_Data([late])).run_status is None


def test_base_inaccesible_genera_el_informe_igual():
    evidence = LaunchEvidence(launched=True, wrapper_exits={"run-night": 0})

    report = _build(_Data([], fail=True), evidence)
    text = render_report(report)

    assert report.db_reachable is False
    assert report.classification.causes == (NightCause.DB_UNREACHABLE,)
    assert "Estado: — · Causa: base de datos inaccesible" in text
    assert "Tokens: —" in text
    assert "Resumen semanal: —" in text
    assert "Últimas 7 noches: —" in text
    assert "run-night 0" in text


def test_sin_lector_es_base_inaccesible():
    report = _build(None)

    assert report.db_reachable is False
    assert render_report(report)


def test_cortacircuitos_cuenta_solo_las_llamadas_del_rol():
    notes = NOTES_ARXIV.replace("ingest=error:ArxivUnavailable", "ingest=ok").replace(
        "end=ingest_error", "end=circuit_breaker_reader"
    )
    run = _run(NIGHT, RunStatus.PARTIAL, notes)
    calls = [
        _call(AgentCallStatus.ERROR, run, AgentRole.READER, 0),
        _call(AgentCallStatus.TIMEOUT, run, AgentRole.READER, 5),
        _call(AgentCallStatus.ERROR, run, AgentRole.POPULARIZER, 0),
    ]

    report = _build(_Data([run], calls))

    assert report.classification.detail == "reader: 2 llamadas con error, 1 con 0 tokens"


def test_el_informe_no_filtra_rutas_ni_secretos():
    run = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV)
    evidence = LaunchEvidence(launched=True, logs=("night-20261105.err.log",))

    text = render_report(_build(_Data([run], snapshot=_snapshot()), evidence))

    for forbidden in ("/Users", "/home", "postgresql", "DATABASE", "NOCTURNA_"):
        assert forbidden not in text


def test_notificacion():
    run = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV, published=9)

    title, body = render_notification(_build(_Data([run])))

    assert title == "Nocturna · 11-05 partial"
    assert body == "arXiv no disponible · 183.182 tokens · 9 publicados"


def test_notificacion_sin_run():
    title, body = render_notification(_build(_Data([])))

    assert title == "Nocturna · 11-05 sin Run"
    assert body == "no lanzado"


@pytest.mark.parametrize(
    ("trigger", "status", "delivered", "expected"),
    [
        (Trigger.RUN_NIGHT, RunStatus.COMPLETED, True, True),
        (Trigger.ARCHIVE_SNAPSHOT, RunStatus.COMPLETED, True, True),
        (Trigger.SCHEDULED, RunStatus.COMPLETED, True, False),
        (Trigger.SCHEDULED, RunStatus.COMPLETED, False, True),
        (Trigger.SCHEDULED, RunStatus.PARTIAL, True, True),
        (Trigger.SCHEDULED, None, True, True),
    ],
)
def test_should_notify(trigger, status, delivered, expected):
    notes = "ingest=ok fetched=1 new=1 | end=edited"
    runs = [_run(NIGHT, status, notes)] if status is not None else []
    report = _build(_Data(runs))
    report = replace(report, trigger=trigger)

    assert should_notify(report, delivered) is expected


def test_linea_de_entrega():
    run = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV)

    line = delivery_line(_build(_Data([run])), "ok")

    assert (
        line
        == "2026-11-05T07:30:00+01:00 2026-11-05 scheduled partial arxiv_unavailable notified=ok"
    )


def test_t105_varios_runs_en_la_ventana_prefiere_el_que_tiene_ingesta():
    good = _run(NIGHT, RunStatus.COMPLETED, NOTES_ARXIV, published=9, read=7, fetched=3)
    good.started_at = datetime.combine(NIGHT, time(0, 5), tzinfo=TZ)
    stray = _run(NIGHT, RunStatus.FAILED, "", read=0)
    stray.started_at = datetime.combine(NIGHT, time(2, 0), tzinfo=TZ)

    report = _build(_Data([good, stray]))

    assert report.run_status is RunStatus.COMPLETED
    assert report.tokens_used == 183_182


def test_t105_fallo_de_base_no_vuelca_causa_encadenada_en_los_logs():
    class _Boom(_Data):
        def started_between(self, start, end):
            try:
                raise RuntimeError("password=s3cr3t host=/Users/david/db")
            except RuntimeError as cause:
                raise ReportDataUnavailable("RuntimeError") from cause

    with _capture_logs("nocturna.application.use_cases.morning_report") as lines:
        _build(_Boom([]))

    out = "\n".join(lines)
    assert "db_unreachable" in out
    assert "RuntimeError" in out
    assert "s3cr3t" not in out
    assert "/Users/" not in out
