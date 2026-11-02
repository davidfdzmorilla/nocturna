"""Constructores de resumenes semanales y del redactor (T84.r) para los tests.

Los imports de modulos de T84.r (`write_archive_digest`, `DigestText`) son
perezosos o de nivel de modulo solo aqui: ningun test previo importa este fichero.
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fakes.archive_source import make_solution
from fakes.digest import FakeDigestReader, InMemoryArchiveDigestTextRepository

from nocturna.domain.archive import ArchiveSolution
from nocturna.domain.archive_digest import (
    DefaultTransition,
    WeeklyDigest,
    build_weekly_digest,
    week_bounds,
)

TZ = ZoneInfo("Europe/Madrid")
NOW = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)  # martes de la W46, dentro de la ventana
SINCE_WEEK = "2026-W40"
CREATED_AT = datetime(2026, 11, 10, 2, 5, tzinfo=UTC)

DIGEST_JSON = {
    "title": "Cambios en el archivo de exoplanetas durante la semana",
    "summary": "Esta semana el archivo cambió la referencia de un planeta.\n\nNada más.",
}


def week_ts(week: str, day: int = 2, hour: int = 3) -> datetime:
    """Instante UTC dentro de `week` (lunes + `day` dias + `hour` h, hora local)."""
    start, _ = week_bounds(week, TZ)
    return (start + timedelta(days=day, hours=hour)).astimezone(UTC)


def changed(pl: str, week: str, *, mass_old=1.0, mass_new=2.0, ref_old="R1", ref_new="R2"):
    """(transicion, soluciones) de un cambio de referencia con cambio de masa."""
    old = make_solution(pl, ref_old, default=True, mass=mass_old)
    new = make_solution(pl, ref_new, default=True, mass=mass_new)
    t = DefaultTransition(pl, old.solution_key, new.solution_key, week_ts(week), True)
    return t, [old, new]


def new_planet(pl: str, week: str, *, ref: str = "Collier et al. 2026", day: int = 2):
    sol = make_solution(pl, ref, default=True)
    t = DefaultTransition(pl, None, sol.solution_key, week_ts(week, day), False)
    return t, [sol]


def regained(pl: str, week: str, *, ref: str = "R1"):
    sol = make_solution(pl, ref, default=True)
    return DefaultTransition(pl, None, sol.solution_key, week_ts(week), True), [sol]


def lost(pl: str, week: str, *, ref: str = "R1"):
    sol = make_solution(pl, ref, default=True)
    return DefaultTransition(pl, sol.solution_key, None, week_ts(week), True), [sol]


def with_ref_text(sol: ArchiveSolution, ref_text: str) -> ArchiveSolution:
    """Misma solucion (misma `solution_key`) con otro `ref_text` (dato de terceros)."""
    return dataclasses.replace(sol, ref_text=ref_text)


def make_digest(
    week: str, *parts, snapshots: int = 1, replace_solutions: dict | None = None
) -> WeeklyDigest:
    transitions, solutions = [], {}
    for t, sols in parts:
        transitions.append(t)
        solutions.update({s.solution_key: s for s in sols})
    if replace_solutions:
        solutions.update(replace_solutions)
    return build_weekly_digest(week, snapshots, transitions, solutions)


def load_reader(reader: FakeDigestReader, week: str, *parts, snapshots: int = 1) -> None:
    """Anade a `reader` `snapshots` snapshots de `week` y las transiciones de `parts`."""
    reader.snapshots.extend(week_ts(week, day=1 + i) for i in range(snapshots))
    for t, sols in parts:
        reader.transitions.append(t)
        reader.solutions.update({s.solution_key: s for s in sols})


def digest_text(week: str, run_id: UUID, *, status: str = "written", sha: str = "a" * 64, **kw):
    from nocturna.domain.archive_digest import DigestText, DigestTextStatus

    st = DigestTextStatus(status)
    base: dict = {
        "week": week,
        "status": st,
        "input_sha256": sha,
        "prompt_version": "writer-digest-v1",
        "run_id": run_id,
        "created_at": CREATED_AT,
    }
    if st is DigestTextStatus.WRITTEN:
        base.update(title="Un título", summary="Un resumen.")
    base.update(kw)
    return DigestText(**base)


def make_digest_writer_work_factory(
    reader: FakeDigestReader, texts: InMemoryArchiveDigestTextRepository
):
    from nocturna.application.use_cases.write_archive_digest import DigestWriterWork

    @contextmanager
    def _work():
        yield DigestWriterWork(reader=reader, digest_texts=texts)

    return _work


def make_digest_selector(env, *, since_week: str = SINCE_WEEK):
    from nocturna.application.use_cases.write_archive_digest import SelectDigestWeeks

    return SelectDigestWeeks(
        work=make_digest_writer_work_factory(env.digest_reader, env.digest_texts),
        tz=TZ,
        since_week=since_week,
        clock=env.clock,
    )


def make_digest_writer(
    env,
    provider,
    *,
    max_attempts: int = 2,
    estimated_tokens: int = 500,
    prompt_version="writer-digest-v1",
):
    from nocturna.application.use_cases.write_archive_digest import WriteDigestText

    return WriteDigestText(
        work=env.work,
        provider=provider,
        system_prompt="prompt del redactor del resumen semanal",
        prompt_version=prompt_version,
        model="claude-sonnet-test",
        max_turns=3,
        max_attempts=max_attempts,
        estimated_tokens=estimated_tokens,
        clock=env.clock,
    )
