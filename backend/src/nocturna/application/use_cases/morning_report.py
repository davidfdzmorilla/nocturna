"""Informe matinal de la noche (T105, ADR 0044 C).

Casos de uso de lectura: reúnen el `Run` de la noche, sus llamadas, el estado de
salida de los envoltorios y el último snapshot/resumen del archivo, y lo
clasifican con `domain/night_report.py`. Sin LLM, sin tokens, sin escritura en
la base: este módulo no importa el proveedor LLM, el Agent SDK ni el control de
gasto, porque no llama a ningún agente.

Nunca incluye DSN, variables de entorno, rutas absolutas ni el contenido de los
logs; solo nombres de fichero de log.
"""

from __future__ import annotations

import logging
import textwrap
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

from nocturna.domain.archive import ArchiveSnapshot
from nocturna.domain.clock import Clock
from nocturna.domain.entities import AgentCall, AgentCallStatus, Run, RunStatus
from nocturna.domain.night_report import (
    NightCause,
    NightClassification,
    NightEvidence,
    breaker_role,
    classify_night,
    is_night_run,
    parse_run_notes,
)

_logger = logging.getLogger(__name__)

_MAX_COLUMNS = 100
_RECENT_DAYS = 7
_WEEKDAYS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
_ARCHIVE_EXIT_KEY = "archive-snapshot"


class Trigger(StrEnum):
    """Quién pide el informe."""

    RUN_NIGHT = "run-night"
    ARCHIVE_SNAPSHOT = "archive-snapshot"
    SCHEDULED = "scheduled"


class ReportDataUnavailable(Exception):
    """La base no se pudo leer (conexión caída, esquema ausente)."""


@dataclass(frozen=True, slots=True)
class LaunchEvidence:
    """Lo que dejó el envoltorio en el directorio de logs para una noche."""

    launched: bool
    wrapper_exits: Mapping[str, int] = field(default_factory=dict)
    archive_exits: Mapping[str, int] = field(default_factory=dict)
    logs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class LatestDigest:
    """Última semana del resumen semanal: cambios de solución por defecto y texto."""

    week: str
    changes: int
    text_status: str  # written | failed | none


class LaunchLog(Protocol):
    def evidence(self, night: date) -> LaunchEvidence: ...


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> bool:
        """Muestra el aviso. Nunca lanza; `False` si no se pudo."""
        ...


class ReportSink(Protocol):
    def write(self, night: date, text: str) -> str:
        """Escribe el informe de la noche y devuelve su nombre de fichero."""
        ...

    def append_delivery(self, line: str) -> None: ...

    def delivered(self, night: date) -> bool:
        """¿Hubo ya un aviso entregado (`notified=ok`) para esa noche?"""
        ...


class MorningReportData(Protocol):
    """Lectura de la base que necesita el informe; levanta `ReportDataUnavailable`."""

    def started_between(self, start: datetime, end: datetime) -> list[Run]: ...

    def calls_for_run(self, run_id: UUID) -> list[AgentCall]: ...

    def tokens_used_for_run(self, run_id: UUID) -> int: ...

    def last_snapshot(self) -> ArchiveSnapshot | None: ...

    def latest_digest(self) -> LatestDigest | None: ...


@dataclass(frozen=True, slots=True)
class MorningReport:
    night: date
    trigger: Trigger
    generated_at: datetime
    run_status: RunStatus | None
    classification: NightClassification
    db_reachable: bool
    launched: bool
    started_at: datetime | None = None
    finished_at: datetime | None = None
    tokens_used: int | None = None
    budget_tokens: int | None = None
    calls_by_status: Mapping[str, int] = field(default_factory=dict)
    items_fetched: int | None = None
    items_read: int | None = None
    items_failed: int | None = None
    findings_published: int | None = None
    evaluate_exit: int | None = None
    run_night_exit: int | None = None
    last_snapshot: ArchiveSnapshot | None = None
    last_snapshot_local_date: date | None = None
    archive_exit: int | None = None
    digest_week: str | None = None
    digest_changes: int | None = None
    digest_text_status: str | None = None  # written | failed | none
    ingest_recovered_from: str | None = None
    recent_nights: tuple[tuple[date, RunStatus | None], ...] = ()
    logs: tuple[str, ...] = ()


class BuildMorningReport:
    """Construye el informe de una noche. Sin `reader` (o con la base caída) lo genera igual."""

    def __init__(
        self,
        reader: MorningReportData | None,
        launch_log: LaunchLog,
        clock: Clock,
        tz: ZoneInfo,
        window_start: time,
        hard_stop: time,
        lookback_days: int = _RECENT_DAYS,
    ) -> None:
        self._reader = reader
        self._launch_log = launch_log
        self._clock = clock
        self._tz = tz
        self._window_start = window_start
        self._hard_stop = hard_stop
        self._lookback_days = lookback_days

    def _bounds(self, night: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(night, self._window_start, tzinfo=self._tz),
            datetime.combine(night, self._hard_stop, tzinfo=self._tz),
        )

    def _night_run(self, runs: list[Run], night: date) -> Run | None:
        start, end = self._bounds(night)
        found = [r for r in runs if is_night_run(r) and start <= r.started_at <= end]
        # Prefiere el Run que llegó a registrar la ingesta (`ingest=`) frente a
        # uno FAILED/RUNNING sin notas.
        with_ingest = [r for r in found if (r.notes or "").startswith("ingest=")]
        found = with_ingest or found
        return found[-1] if found else None

    def __call__(self, night: date, trigger: Trigger) -> MorningReport:
        now = self._clock.now()
        launch = self._launch_log.evidence(night)
        run: Run | None = None
        recent: tuple[tuple[date, RunStatus | None], ...] = ()
        calls: list[AgentCall] = []
        tokens_used: int | None = None
        db_reachable = self._reader is not None

        if self._reader is not None:
            first = night - timedelta(days=self._lookback_days - 1)
            try:
                runs = self._reader.started_between(self._bounds(first)[0], self._bounds(night)[1])
                run = self._night_run(runs, night)
                recent = tuple(
                    (d, (r.status if (r := self._night_run(runs, d)) is not None else None))
                    for d in (first + timedelta(days=i) for i in range(self._lookback_days))
                )
                if run is not None:
                    calls = self._reader.calls_for_run(run.id)
                    tokens_used = self._reader.tokens_used_for_run(run.id)
            except ReportDataUnavailable as exc:
                _logger.warning(
                    "morning_report.db_unreachable",
                    extra={"error": type(exc.__cause__ or exc).__name__},
                )
                db_reachable = False
                run, recent, calls, tokens_used = None, (), [], None

        snapshot: ArchiveSnapshot | None = None
        digest: LatestDigest | None = None
        if self._reader is not None and db_reachable:
            try:
                snapshot = self._reader.last_snapshot()
                digest = self._reader.latest_digest()
            except ReportDataUnavailable as exc:
                _logger.warning(
                    "morning_report.archive_unavailable",
                    extra={"error": type(exc.__cause__ or exc).__name__},
                )

        parsed = parse_run_notes(run.notes if run is not None else None)
        role = breaker_role(parsed.end)
        failed = [
            c
            for c in calls
            if c.status is not AgentCallStatus.OK and (role is None or c.agent.value == role)
        ]
        evidence = NightEvidence(
            run_status=run.status if run is not None else None,
            notes=run.notes if run is not None else None,
            launched=launch.launched,
            wrapper_exits=launch.wrapper_exits,
            db_reachable=db_reachable,
            failed_calls=len(failed),
            failed_calls_zero_tokens=sum(1 for c in failed if c.total_tokens == 0),
            past_hard_stop=now >= self._bounds(night)[1],
        )
        by_status: dict[str, int] = {}
        for call in calls:
            by_status[call.status.value] = by_status.get(call.status.value, 0) + 1

        return MorningReport(
            night=night,
            trigger=trigger,
            generated_at=now,
            run_status=run.status if run is not None else None,
            classification=classify_night(evidence),
            db_reachable=db_reachable,
            launched=launch.launched,
            started_at=run.started_at.astimezone(self._tz) if run is not None else None,
            finished_at=(
                run.finished_at.astimezone(self._tz)
                if run is not None and run.finished_at is not None
                else None
            ),
            tokens_used=tokens_used,
            budget_tokens=run.budget_tokens if run is not None else None,
            calls_by_status=by_status,
            items_fetched=run.items_fetched if run is not None else None,
            items_read=run.items_read if run is not None else None,
            items_failed=parsed.read_failed,
            findings_published=run.findings_published if run is not None else None,
            evaluate_exit=launch.wrapper_exits.get("evaluate-tensions"),
            run_night_exit=launch.wrapper_exits.get("run-night"),
            last_snapshot=snapshot,
            last_snapshot_local_date=(
                snapshot.taken_at.astimezone(self._tz).date() if snapshot is not None else None
            ),
            archive_exit=launch.archive_exits.get(_ARCHIVE_EXIT_KEY),
            digest_week=digest.week if digest is not None else None,
            digest_changes=digest.changes if digest is not None else None,
            digest_text_status=digest.text_status if digest is not None else None,
            ingest_recovered_from=(
                parsed.ingest_retry.removeprefix("ok:")
                if parsed.ingest_retry is not None and parsed.ingest_retry.startswith("ok:")
                else None
            ),
            recent_nights=recent,
            logs=launch.logs,
        )


# --- Presentación -------------------------------------------------------------

_DASH = "—"


def _n(value: int | None) -> str:
    return _DASH if value is None else f"{value:,}".replace(",", ".")


def _hm(value: datetime | None) -> str:
    return _DASH if value is None else value.strftime("%H:%M")


def _fit(line: str) -> str:
    return line if len(line) <= _MAX_COLUMNS else line[: _MAX_COLUMNS - 1] + "…"


def _cause_text(cause: NightCause, detail: str) -> str:
    extra = f" ({detail})" if detail else ""
    match cause:
        case NightCause.ARXIV_UNAVAILABLE:
            return f"arXiv no disponible en la ingesta{extra}"
        case NightCause.INGEST_ERROR:
            return f"error en la ingesta{extra}"
        case NightCause.EXPIRE_ERROR:
            return "fallo al caducar ítems antiguos"
        case NightCause.CIRCUIT_BREAKER:
            return f"cortacircuitos{extra}"
        case NightCause.RATE_LIMITED:
            return f"límite de uso de Claude{extra}"
        case NightCause.BUDGET_EXHAUSTED:
            return "presupuesto de tokens agotado"
        case NightCause.BUDGET_DENIED:
            return f"llamada denegada por el control de gasto{extra}"
        case NightCause.WRITER_ERROR:
            return "fallo del redactor"
        case NightCause.MEASUREMENT_FINDINGS_ERROR:
            return "fallo en los hallazgos de medida"
        case NightCause.EDITOR_FAILED:
            return f"el Editor falló{extra}"
        case NightCause.HARD_STOP:
            return "cortada por el fin de la ventana"
        case NightCause.RUN_TIMEOUT:
            return "tiempo máximo de ejecución agotado"
        case NightCause.ORPHAN_CLOSED:
            return "Run sin cerrar, cerrado como killed por la noche siguiente"
        case NightCause.EXCEPTION:
            return "excepción no controlada (ver el log de errores)"
        case NightCause.STILL_RUNNING:
            return "sigue en curso pasada la hora límite"
        case NightCause.NOT_LAUNCHED:
            return "no lanzado (Mac apagado, dormido o sin sesión)"
        case NightCause.PRECONDITIONS:
            return f"precondiciones no listas (Docker o PostgreSQL){extra}"
        case NightCause.ENVIRONMENT:
            return f"entorno inválido (uv, claude o configuración){extra}"
        case NightCause.LAUNCHED_NO_RUN:
            return f"lanzado pero sin Run en la base{extra}"
        case NightCause.DB_UNREACHABLE:
            return "base de datos inaccesible"
        case NightCause.UNCLASSIFIED:
            return f"sin clasificar{extra}"
    return cause.value  # pragma: no cover


def _cause_short(cause: NightCause, detail: str) -> str:
    if cause is NightCause.ARXIV_UNAVAILABLE:
        return "arXiv no disponible"
    if cause is NightCause.UNCLASSIFIED and detail:
        return f"sin clasificar ({detail})"
    return _cause_text(cause, "").split(" (")[0]


def _status_label(report: MorningReport) -> str:
    if report.run_status is not None:
        return report.run_status.value
    return "sin datos" if not report.db_reachable else "sin Run"


def _causes_line(report: MorningReport) -> str:
    c = report.classification
    if not c.causes:
        return "ninguna"
    text = _cause_text(c.causes[0], c.detail)
    others = [_cause_text(x, "") for x in c.causes[1:]]
    return text if not others else f"{text} · además: {', '.join(others)}"


def _recent_line(report: MorningReport) -> str:
    if not report.db_reachable or not report.recent_nights:
        return f"Últimas {_RECENT_DAYS} noches: {_DASH}"
    cells = [
        f"{d:%m-%d} {s.value if s is not None else 'sin Run'}" for d, s in report.recent_nights
    ]
    body = textwrap.fill(
        " · ".join(cells),
        width=_MAX_COLUMNS,
        initial_indent=f"Últimas {len(cells)} noches: ",
        subsequent_indent="  ",
        break_long_words=False,
        break_on_hyphens=False,
    )
    return body


_TEXT_STATUS = {"written": "escrito", "failed": "fallido", "none": "sin texto"}


def render_report(report: MorningReport) -> str:
    """Informe de una pantalla: ≤ 15 líneas, ≤ 100 columnas, en español."""
    night = report.night
    gen = f"{report.generated_at:%H:%M}"
    lines = [
        f"Nocturna · noche del {night.isoformat()} ({_WEEKDAYS[night.weekday()]}) "
        f"· generado {gen} por {report.trigger.value}"
    ]
    if report.run_status is not None:
        state = report.run_status.value.upper()
    elif report.db_reachable:
        state = "SIN RUN"
    else:
        state = _DASH
    lines.append(f"Estado: {state} · Causa: {_causes_line(report)}")
    lines.append(
        f"Horario: {_hm(report.started_at)}–{_hm(report.finished_at)}"
        f" · run-night {_n(report.run_night_exit)} · evaluate-tensions {_n(report.evaluate_exit)}"
    )

    if report.tokens_used is None or report.budget_tokens is None:
        lines.append(f"Tokens: {_DASH} · llamadas {_DASH}")
    else:
        pct = round(report.tokens_used * 100 / report.budget_tokens)
        cbs = report.calls_by_status
        lines.append(
            f"Tokens: {_n(report.tokens_used)} / {_n(report.budget_tokens)} ({pct} %)"
            f" · llamadas {sum(cbs.values())}: ok {cbs.get('ok', 0)},"
            f" inválidas {cbs.get('invalid_output', 0)}, error {cbs.get('error', 0)},"
            f" timeout {cbs.get('timeout', 0)}"
        )
    recovered = report.ingest_recovered_from
    items_line = (
        f"Ítems: nuevos {_n(report.items_fetched)} · leídos {_n(report.items_read)}"
        f" (fallidos {_n(report.items_failed)}) · publicados {_n(report.findings_published)}"
    )
    lines.append(items_line)
    if recovered:
        lines.append(f"Ingesta recuperada en el segundo intento ({recovered})")

    snap = report.last_snapshot
    snap_date = report.last_snapshot_local_date.isoformat() if snap is not None else _DASH
    release = snap.max_releasedate.isoformat() if snap and snap.max_releasedate else _DASH
    lines.append(
        f"Archivo: snapshot {snap_date} · {_n(snap.rows_total if snap else None)} filas"
        f" · última publ. {release} · archive-snapshot {_n(report.archive_exit)}"
    )
    if report.digest_week is None:
        lines.append(f"Resumen semanal: {_DASH}")
    else:
        text = _TEXT_STATUS.get(report.digest_text_status or "none", _DASH)
        lines.append(
            f"Resumen semanal: {report.digest_week} · {_n(report.digest_changes)} cambios"
            f" · texto del redactor: {text}"
        )
    lines.append(_recent_line(report))
    lines.append("Logs: " + (" · ".join(report.logs) if report.logs else _DASH))

    out: list[str] = []
    for line in lines:
        out.extend(_fit(part) for part in line.split("\n"))
    return "\n".join(out)


def render_notification(report: MorningReport) -> tuple[str, str]:
    """Título y cuerpo del aviso de macOS."""
    title = f"Nocturna · {report.night:%m-%d} {_status_label(report)}"
    c = report.classification
    parts = [
        _cause_short(c.causes[0], c.detail) if c.causes else "sin incidencias",
    ]
    if report.tokens_used is not None:
        parts.append(f"{_n(report.tokens_used)} tokens")
    if report.findings_published is not None:
        parts.append(f"{_n(report.findings_published)} publicados")
    return title, " · ".join(parts)


def should_notify(report: MorningReport, already_delivered: bool) -> bool:
    """`run-night` y `archive-snapshot` avisan siempre; el programado, solo si la
    noche no fue `completed` o aún no hubo entrega para esa noche."""
    if report.trigger is not Trigger.SCHEDULED:
        return True
    return report.run_status is not RunStatus.COMPLETED or not already_delivered


def delivery_line(report: MorningReport, notified: str) -> str:
    """Línea de `morning-deliveries.log`: `generated_at night trigger status causes notified=`."""
    causes = ",".join(c.value for c in report.classification.causes) or "-"
    status = report.run_status.value if report.run_status is not None else "sin-run"
    return (
        f"{report.generated_at.isoformat(timespec='seconds')} {report.night.isoformat()} "
        f"{report.trigger.value} {status} {causes} notified={notified}"
    )
