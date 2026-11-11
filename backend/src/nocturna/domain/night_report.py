"""Clasificación de la causa de una noche y detección de la noche sin `Run` (T105).

Reglas puras, sin IO: leen la evidencia ya recogida (el `Run` y su `notes`, el
estado de salida del envoltorio, las llamadas fallidas) y devuelven una o
varias causas. No deciden nada del pipeline: solo explican lo ocurrido.

`Run.notes` lo escribe `RunNight._build_notes` con el formato
`ingest=<ok|error[:Clase]> fetched=N new=M | [ingest_retry=<ok|error|skipped>:X |]
expired=N[:err] | read=N(failed=M) | candidates=X published=Y | tokens=T |
end=<motivo>`. `parse_run_notes` lo lee con tolerancia:
ante cualquier cosa que no reconozca devuelve campos `None`; nunca lanza.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from enum import StrEnum

from nocturna.domain.entities import Run, RunStatus

# Nota fija con la que `run-night` cierra un `Run` huérfano (`cli.py`). Vive aquí
# para que cierre y detección no puedan divergir.
ORPHAN_NOTE = "cerrado por run-night: quedó RUNNING de un proceso anterior"

REREAD_NOTE = "reread"
NIGHT_NOTES_PREFIX = "ingest="


class NightCause(StrEnum):
    """Por qué una noche no fue un `completed` limpio (o por qué no hay `Run`)."""

    ARXIV_UNAVAILABLE = "arxiv_unavailable"
    INGEST_ERROR = "ingest_error"
    EXPIRE_ERROR = "expire_error"
    CIRCUIT_BREAKER = "circuit_breaker"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    BUDGET_EXHAUSTED = "budget_exhausted"
    BUDGET_DENIED = "budget_denied"
    WRITER_ERROR = "writer_error"
    MEASUREMENT_FINDINGS_ERROR = "measurement_findings_error"
    EDITOR_FAILED = "editor_failed"
    HARD_STOP = "hard_stop"
    RUN_TIMEOUT = "run_timeout"
    ORPHAN_CLOSED = "orphan_closed"
    EXCEPTION = "exception"
    STILL_RUNNING = "still_running"
    # Sin `Run`.
    NOT_LAUNCHED = "not_launched"
    PRECONDITIONS = "preconditions"
    ENVIRONMENT = "environment"
    LAUNCHED_NO_RUN = "launched_no_run"
    DB_UNREACHABLE = "db_unreachable"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True, slots=True)
class ParsedNotes:
    """Campos de `Run.notes`; `None` si no aparecen o no se pueden leer."""

    ingest_ok: bool | None = None
    ingest_error: str | None = None
    fetched: int | None = None
    new: int | None = None
    ingest_retry: str | None = None
    expired: int | None = None
    expire_error: str | None = None
    read: int | None = None
    read_failed: int | None = None
    candidates: int | None = None
    published: int | None = None
    tokens: int | None = None
    end: str | None = None


_INGEST_RE = re.compile(r"^ingest=(ok|error)(?::(\S+))?(?:\s+fetched=(\d+))?(?:\s+new=(\d+))?")
_INGEST_RETRY_RE = re.compile(r"^ingest_retry=((?:ok|error|skipped):\S+)")
_EXPIRED_RE = re.compile(r"^expired=(\d+)(?::(\S+))?")
_READ_RE = re.compile(r"^read=(\d+)\(failed=(\d+)\)")
_CANDIDATES_RE = re.compile(r"^candidates=(\d+)\s+published=(\d+)")
_TOKENS_RE = re.compile(r"^tokens=(\d+)")
_END_RE = re.compile(r"^end=(\S+)")


def parse_run_notes(notes: str | None) -> ParsedNotes:
    """Lee `Run.notes`. Tolerante: un segmento ilegible deja sus campos en `None`."""
    if not notes:
        return ParsedNotes()
    values: dict[str, object] = {}
    for raw in notes.split("|"):
        part = raw.strip()
        if m := _INGEST_RE.match(part):
            values["ingest_ok"] = m.group(1) == "ok"
            values["ingest_error"] = m.group(2)
            values["fetched"] = int(m.group(3)) if m.group(3) else None
            values["new"] = int(m.group(4)) if m.group(4) else None
        elif m := _INGEST_RETRY_RE.match(part):
            values["ingest_retry"] = m.group(1)
        elif m := _EXPIRED_RE.match(part):
            values["expired"] = int(m.group(1))
            values["expire_error"] = m.group(2)
        elif m := _READ_RE.match(part):
            values["read"] = int(m.group(1))
            values["read_failed"] = int(m.group(2))
        elif m := _CANDIDATES_RE.match(part):
            values["candidates"] = int(m.group(1))
            values["published"] = int(m.group(2))
        elif m := _TOKENS_RE.match(part):
            values["tokens"] = int(m.group(1))
        elif m := _END_RE.match(part):
            values["end"] = m.group(1)
    return ParsedNotes(**values)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class NightEvidence:
    """Todo lo que hace falta para clasificar una noche.

    `run_status` es `None` si no hay `Run`. `wrapper_exits` son las líneas
    `clave=código` que el envoltorio dejó para esa noche (`precondition`,
    `duplicate`, `environment`, `run-night`, `evaluate-tensions`).
    `failed_calls`/`failed_calls_zero_tokens`: llamadas del `Run` con `status` distinto de
    `ok` y, de ellas, las de 0 tokens; para detallar el cortacircuitos.
    `past_hard_stop`: solo importa con `RUNNING`.
    """

    run_status: RunStatus | None
    notes: str | None
    launched: bool
    wrapper_exits: Mapping[str, int] = field(default_factory=dict)
    db_reachable: bool = True
    failed_calls: int = 0
    failed_calls_zero_tokens: int = 0
    past_hard_stop: bool = True


@dataclass(frozen=True, slots=True)
class NightClassification:
    """Causas de una noche, la principal primero. Vacía solo si `COMPLETED` sin más."""

    causes: tuple[NightCause, ...]
    detail: str = ""

    @property
    def primary(self) -> NightCause | None:
        return self.causes[0] if self.causes else None


_ARXIV_ERROR_CLASS = "ArxivUnavailable"
_BUDGET_EXHAUSTED_ENDS = frozenset({"budget_exhausted", "writer_budget_exhausted"})
_BUDGET_DENIED_ENDS = frozenset({"call_limit_reached", "run_not_running", "editor_already_called"})
_HARD_STOP_ENDS = frozenset({"hard_stop", "outside_window"})
_EDITOR_FAILED_ENDS = frozenset(
    {"editor_invalid_output", "editor_timeout", "editor_agent_error", "editor_rate_limited"}
)
_BREAKER_PREFIX = "circuit_breaker_"
_RATE_LIMITED_PREFIX = "rate_limited_"
_AUTH_FAILED_PREFIX = "auth_failed_"


def breaker_role(end: str | None) -> str | None:
    """Rol del cortacircuitos (`reader`, `popularizer`) si `end` es de ese tipo."""
    if end is not None and end.startswith(_BREAKER_PREFIX):
        return end[len(_BREAKER_PREFIX) :]
    return None


def _from_end(end: str, evidence: NightEvidence, parsed: ParsedNotes) -> tuple[NightCause, str]:
    if end.startswith(_AUTH_FAILED_PREFIX):
        return NightCause.AUTH_FAILED, end[len(_AUTH_FAILED_PREFIX) :]
    if end == "ingest_error":
        if parsed.ingest_error == _ARXIV_ERROR_CLASS:
            return NightCause.ARXIV_UNAVAILABLE, _ARXIV_ERROR_CLASS
        return NightCause.INGEST_ERROR, parsed.ingest_error or ""
    if end == "expire_error":
        return NightCause.EXPIRE_ERROR, parsed.expire_error or ""
    if (role := breaker_role(end)) is not None:
        detail = (
            f"{role}: {evidence.failed_calls} llamadas con error, "
            f"{evidence.failed_calls_zero_tokens} con 0 tokens"
        )
        return NightCause.CIRCUIT_BREAKER, detail
    if end.startswith(_RATE_LIMITED_PREFIX):
        return NightCause.RATE_LIMITED, end[len(_RATE_LIMITED_PREFIX) :]
    if end in _BUDGET_EXHAUSTED_ENDS:
        return NightCause.BUDGET_EXHAUSTED, end
    if end in _BUDGET_DENIED_ENDS:
        return NightCause.BUDGET_DENIED, end
    if end == "writer_error":
        return NightCause.WRITER_ERROR, ""
    if end == "measurement_findings_error":
        return NightCause.MEASUREMENT_FINDINGS_ERROR, ""
    if end in _EDITOR_FAILED_ENDS:
        return NightCause.EDITOR_FAILED, end.removeprefix("editor_")
    if end in _HARD_STOP_ENDS:
        return NightCause.HARD_STOP, end
    if end == "run_timeout":
        return NightCause.RUN_TIMEOUT, ""
    return NightCause.UNCLASSIFIED, f"end={end}"


def _classify_without_run(evidence: NightEvidence) -> NightClassification:
    exits = evidence.wrapper_exits
    if "run-night" in exits:
        if not evidence.db_reachable:
            return NightClassification((NightCause.DB_UNREACHABLE,), "")
        return NightClassification((NightCause.LAUNCHED_NO_RUN,), f"run-night={exits['run-night']}")
    if "precondition" in exits:
        return NightClassification((NightCause.PRECONDITIONS,), f"código {exits['precondition']}")
    if "environment" in exits:
        return NightClassification((NightCause.ENVIRONMENT,), f"código {exits['environment']}")
    if not evidence.db_reachable:
        return NightClassification((NightCause.DB_UNREACHABLE,), "")
    if exits and set(exits) == {"duplicate"}:
        return NightClassification((NightCause.UNCLASSIFIED,), f"duplicate={exits['duplicate']}")
    if exits:
        raw = ",".join(f"{k}={v}" for k, v in sorted(exits.items()))
        return NightClassification((NightCause.UNCLASSIFIED,), raw)
    if evidence.launched:
        return NightClassification((NightCause.UNCLASSIFIED,), "centinela sin código de salida")
    return NightClassification((NightCause.NOT_LAUNCHED,), "")


def classify_night(evidence: NightEvidence) -> NightClassification:
    """Causa(s) de la noche. Nunca lanza."""
    status = evidence.run_status
    if status is None:
        return _classify_without_run(evidence)

    parsed = parse_run_notes(evidence.notes)
    ingest_failed = parsed.ingest_ok is False
    ingest_cause = (
        NightCause.ARXIV_UNAVAILABLE
        if parsed.ingest_error == _ARXIV_ERROR_CLASS
        else NightCause.INGEST_ERROR
    )

    if status is RunStatus.COMPLETED:
        if ingest_failed:
            return NightClassification((ingest_cause,), parsed.ingest_error or "")
        return NightClassification(())
    if status is RunStatus.FAILED:
        return NightClassification((NightCause.EXCEPTION,), "")
    if status is RunStatus.RUNNING:
        if evidence.past_hard_stop:
            return NightClassification((NightCause.STILL_RUNNING,), "")
        return NightClassification((NightCause.UNCLASSIFIED,), f"status={status.value}")
    if evidence.notes == ORPHAN_NOTE:
        return NightClassification((NightCause.ORPHAN_CLOSED,), "")

    # PARTIAL o KILLED.
    if parsed.end is None:
        if ingest_failed:
            return NightClassification((ingest_cause,), parsed.ingest_error or "")
        return NightClassification((NightCause.UNCLASSIFIED,), f"status={status.value}")
    primary, detail = _from_end(parsed.end, evidence, parsed)
    causes = [primary]
    if ingest_failed and primary not in (NightCause.ARXIV_UNAVAILABLE, NightCause.INGEST_ERROR):
        causes.append(ingest_cause)
    return NightClassification(tuple(causes), detail)


def is_night_run(run: Run) -> bool:
    """¿Es este `Run` el de una noche de `run-night`?

    Excluye las relecturas (`notes == 'reread'`) y los de `run-item` (sin notas).
    """
    notes = run.notes or ""
    if notes == REREAD_NOTE:
        return False
    if notes.startswith(NIGHT_NOTES_PREFIX) or notes == ORPHAN_NOTE:
        return True
    return run.status in (RunStatus.FAILED, RunStatus.RUNNING)


def target_night(now_local: datetime, hard_stop: time) -> date:
    """Noche a informar: la de hoy si ya pasó `hard_stop`, si no la de ayer."""
    today = now_local.date()
    return today if now_local.time() >= hard_stop else today - timedelta(days=1)
