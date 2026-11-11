"""Lectura de la base para el informe matinal (T105). Solo SELECT.

Compone los repositorios existentes y traduce cualquier `SQLAlchemyError` a
`ReportDataUnavailable`, para que el caso de uso (que no importa SQLAlchemy)
pueda seguir generando el informe sin base.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from nocturna.application.use_cases.morning_report import LatestDigest, ReportDataUnavailable
from nocturna.domain.archive import ArchiveSnapshot
from nocturna.domain.archive_digest import DigestTextStatus, week_bounds
from nocturna.domain.entities import AgentCall, Run
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyArchiveDigestReader,
    SqlAlchemyArchiveDigestTextRepository,
    SqlAlchemyArchiveRepository,
    SqlAlchemyRunRepository,
)


def _guard[T](fn: Callable[[], T]) -> T:
    try:
        return fn()
    except (SQLAlchemyError, OSError, ImportError) as exc:
        raise ReportDataUnavailable(type(exc).__name__) from exc


class SqlMorningReportData:
    """Cumple `application.use_cases.morning_report.MorningReportData`."""

    def __init__(self, session: Session, tz: ZoneInfo) -> None:
        self._session = session
        self._tz = tz

    def started_between(self, start: datetime, end: datetime) -> list[Run]:
        return _guard(lambda: SqlAlchemyRunRepository(self._session).started_between(start, end))

    def last_night_run_finished_before(self, before: datetime) -> Run | None:
        return _guard(
            lambda: SqlAlchemyRunRepository(self._session).last_night_run_finished_before(before)
        )

    def calls_for_run(self, run_id: UUID) -> list[AgentCall]:
        return _guard(lambda: SqlAlchemyAgentCallRepository(self._session).calls_for_run(run_id))

    def tokens_used_for_run(self, run_id: UUID) -> int:
        return _guard(
            lambda: SqlAlchemyAgentCallRepository(self._session).tokens_used_for_run(run_id)
        )

    def last_snapshot(self) -> ArchiveSnapshot | None:
        return _guard(lambda: SqlAlchemyArchiveRepository(self._session).last_snapshot())

    def latest_digest(self) -> LatestDigest | None:
        return _guard(self._latest_digest)

    def _latest_digest(self) -> LatestDigest | None:
        reader = SqlAlchemyArchiveDigestReader(self._session)
        weeks = reader.snapshot_weeks(self._tz)
        if not weeks:
            return None
        week = weeks[-1][0]
        start, end = week_bounds(week, self._tz)
        changes = len(reader.transitions_between(start, end))
        text = SqlAlchemyArchiveDigestTextRepository(self._session).get(week)
        if text is None:
            status = "none"
        else:
            status = "written" if text.status is DigestTextStatus.WRITTEN else "failed"
        return LatestDigest(week=week, changes=changes, text_status=status)
