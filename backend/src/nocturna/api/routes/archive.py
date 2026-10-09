"""`GET /archive/weeks` y `GET /archive/weeks/{week}` (T84).

Resumen semanal de cambios de referencia del NASA Exoplanet Archive,
calculado al vuelo desde la base. Solo lectura, sin LLM. Los esquemas de
`api/schemas.py` son una lista blanca: nunca salen `solution_key` ni metadatos
del snapshot.
"""

from collections.abc import Callable
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException

from nocturna.api.deps import (
    get_archive_digest_reader,
    get_digest_timezone,
    get_planet_overview_url,
)
from nocturna.api.schemas import DigestWeekOut, DigestWeeksResponse, WeeklyDigestOut
from nocturna.application.use_cases.archive_digest import GetWeeklyDigest, ListDigestWeeks
from nocturna.domain.archive_digest import TransitionKind
from nocturna.domain.repositories import ArchiveDigestReader

router = APIRouter()

_ReaderDep = Annotated[ArchiveDigestReader, Depends(get_archive_digest_reader)]
_TzDep = Annotated[ZoneInfo, Depends(get_digest_timezone)]
_UrlDep = Annotated[Callable[[str], str], Depends(get_planet_overview_url)]


@router.get("/archive/weeks", response_model=DigestWeeksResponse)
def list_weeks(reader: _ReaderDep, tz: _TzDep) -> DigestWeeksResponse:
    """Semanas con snapshot, más recientes primero, con recuento por tipo."""
    return DigestWeeksResponse(
        weeks=[
            DigestWeekOut(
                week=w.week,
                snapshots=w.snapshots,
                changed=w.counts[TransitionKind.CHANGED],
                new_planet=w.counts[TransitionKind.NEW_PLANET],
                regained=w.counts[TransitionKind.REGAINED],
                lost=w.counts[TransitionKind.LOST],
            )
            for w in ListDigestWeeks(reader, tz)()
        ]
    )


@router.get("/archive/weeks/{week}", response_model=WeeklyDigestOut)
def get_week(week: str, reader: _ReaderDep, tz: _TzDep, planet_url: _UrlDep) -> WeeklyDigestOut:
    """Resumen de una semana ISO (`YYYY-Www`). `422` si el formato es inválido;
    `404` si la semana no tiene ningún snapshot."""
    try:
        digest = GetWeeklyDigest(reader, tz)(week)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid week, expected YYYY-Www") from exc
    if digest is None:
        raise HTTPException(status_code=404, detail="week not found")
    return WeeklyDigestOut.from_domain(digest, planet_url=planet_url)
