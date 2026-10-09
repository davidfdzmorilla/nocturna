"""Informe de solo lectura del enlace entre las dos vías (T83, ADR 0023).

Mide, sobre la base indicada en `NOCTURNA_DATABASE_URL`, cuánto se cruzan las
soluciones del NASA Exoplanet Archive con los ítems de arXiv:

- (a) filas activas con `releasedate >= since`: total, con `arxiv_id` y cuántas
  cruzan con `items.external_id`.
- (b) desfase en meses entre `releasedate` y el mes del `arxiv_id` (YYMM) de
  todas las filas activas con `arxiv_id`.
- (c) filas con bibcode de revista (sin `arxiv_id`) desde `since` que la regla
  de solución propia marca `OWN_VALUE_MATCH` o `AMBIGUOUS` frente a alguna
  lectura vigente con medidas.
- (d) auditoría de las `tension_evaluation` guardadas con la regla nueva:
  cambios de estado o de `own_solution_key`, referencias que dejan de ser
  `INDEPENDENT` y confirmaciones elegibles antes de T83.

Solo lee: abre la unidad de trabajo con `commit=False` (la transacción se
deshace siempre). Sin Claude, sin red y sin tokens.

    cd backend
    uv run python scripts/t83_link_report.py [--since 2026-08-01]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime

import anyio
import sqlalchemy as sa
from sqlalchemy.orm import Session

from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.cli import own_solution_rule_from_config, period_rule_from_config
from nocturna.domain.archive import catalog_solution_from_archive
from nocturna.domain.entities import MeasuredParameter, Measurement
from nocturna.domain.measurement_findings import (
    confirmation_eligible,
    reference_within_confirmation_limits,
)
from nocturna.domain.own_solution import OwnSolutionRule, SolutionProvenance, classify_solution
from nocturna.domain.tension import PeriodRule, TensionEvaluation
from nocturna.infrastructure.config import Settings, load_pipeline_config
from nocturna.infrastructure.db.mappers import (
    archive_solution_from_row,
    item_from_row,
    reading_from_row,
)
from nocturna.infrastructure.db.models import ArchiveSolutionRow, ItemRow, ReadingRow
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveRepository,
    SqlAlchemyTensionEvaluationRepository,
)
from nocturna.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
    unit_of_work,
)
from nocturna.infrastructure.exoplanet_archive.catalog import ExoplanetArchiveCatalog
from nocturna.infrastructure.exoplanet_archive.names import normalize_name

DEFAULT_SINCE = date(2026, 8, 1)
_PARAMETERS = (MeasuredParameter.MASS, MeasuredParameter.RADIUS, MeasuredParameter.PERIOD)


@dataclass(frozen=True)
class EvaluationChange:
    evaluation_id: object
    external_id: str
    planet_name: str
    parameter: MeasuredParameter
    old_status: object
    new_status: object
    old_own_solution_key: str | None
    new_own_solution_key: str | None


@dataclass(frozen=True)
class ReferenceAudit:
    evaluation_id: object
    external_id: str
    planet_name: str
    parameter: MeasuredParameter
    provenance: SolutionProvenance


@dataclass(frozen=True)
class ConfirmationAudit:
    evaluation_id: object
    external_id: str
    planet_name: str
    parameter: MeasuredParameter
    provenance: SolutionProvenance
    still_eligible: bool


@dataclass(frozen=True)
class LinkReport:
    a_total: int
    a_with_arxiv_id: int
    a_crossing: tuple[str, ...]
    b_count: int
    b_min: int | None
    b_median: float | None
    b_max: int | None
    c_journal_rows: int
    c_own_value_match_rows: int
    c_ambiguous_rows: int
    d_evaluations: int
    d_changed: tuple[EvaluationChange, ...]
    d_reference_not_independent: tuple[ReferenceAudit, ...]
    d_confirmations: tuple[ConfirmationAudit, ...]


def _arxiv_month_index(arxiv_id: str) -> int | None:
    """Mes (año * 12 + mes - 1) del identificador `YYMM.NNNNN`; `None` si no lo es."""
    head = arxiv_id.split(".", 1)[0]
    if len(head) != 4 or not head.isdigit():
        return None
    year, month = 2000 + int(head[:2]), int(head[2:])
    if not 1 <= month <= 12:
        return None
    return year * 12 + month - 1


def _release_month_index(day: date) -> int:
    return day.year * 12 + day.month - 1


def _active_rows(session: Session) -> list[ArchiveSolutionRow]:
    stmt = sa.select(ArchiveSolutionRow).where(ArchiveSolutionRow.removed_at.is_(None))
    return list(session.execute(stmt).scalars())


def _current_measurements(
    session: Session,
) -> list[tuple[str, datetime, tuple[Measurement, ...]]]:
    """(external_id, published_at, medidas) de las lecturas vigentes con medidas."""
    stmt = (
        sa.select(ReadingRow, ItemRow.external_id, ItemRow.published_at)
        .join(ItemRow, ItemRow.id == ReadingRow.item_id)
        .where(ReadingRow.measurements.is_not(None), ReadingRow.superseded_at.is_(None))
        .where(ItemRow.source == "arxiv")
    )
    out = []
    for row, external_id, published_at in session.execute(stmt):
        measurements = reading_from_row(row).measurements or ()
        if measurements:
            out.append((external_id, published_at, tuple(measurements)))
    return out


class _StoredNameCatalog:
    """Catálogo que resuelve el planeta al nombre de archivo ya guardado (sin red ni alias)."""

    def __init__(self, inner: ExoplanetArchiveCatalog, archive_name: str) -> None:
        self._inner = inner
        self._archive_name = archive_name

    async def resolve_planet(self, name: str) -> str | None:
        return self._archive_name

    async def solutions(self, planet_name: str, parameter: MeasuredParameter):
        return await self._inner.solutions(planet_name, parameter)


def _recompute(
    session: Session,
    evaluation: TensionEvaluation,
    *,
    rule: OwnSolutionRule,
    now: datetime,
    threshold_sigma: float,
    period_rule: PeriodRule,
) -> TensionEvaluation | None:
    assert evaluation.archive_planet_name is not None  # noqa: S101
    item_row = session.get(ItemRow, evaluation.item_id)
    reading_row = session.get(ReadingRow, evaluation.reading_id)
    if item_row is None or reading_row is None:
        return None
    # T92: todas las medidas de la lectura para el planeta, de todos los parámetros.
    # Filtra por el nombre exacto del Reader: _StoredNameCatalog resuelve cualquier
    # nombre al planeta guardado. Dos alias del mismo planeta no se unen aquí, como
    # sí hace ComputeTensions en producción.
    base = reading_from_row(reading_row)
    same_planet = (
        tuple(m for m in (base.measurements or ()) if m.planet_name == evaluation.planet_name)
        or evaluation.measurements
    )
    reading = replace(base, measurements=same_planet)
    inner = ExoplanetArchiveCatalog(SqlAlchemyArchiveRepository(session), None)  # type: ignore[arg-type]
    catalog = _StoredNameCatalog(inner, evaluation.archive_planet_name)

    class _Clock:
        def now(self) -> datetime:
            return now

    compute = ComputeTensions(
        catalog,  # type: ignore[arg-type]
        threshold_sigma=threshold_sigma,
        period_rule=period_rule,
        own_solution_rule=rule,
        clock=_Clock(),
    )

    async def run():
        return await compute([(item_from_row(item_row), reading)])

    report = anyio.run(run)
    return next(
        (
            e
            for e in report.evaluations
            if e.planet_name == evaluation.planet_name and e.parameter == evaluation.parameter
        ),
        None,
    )


def build_report(
    session: Session,
    *,
    since: date,
    rule: OwnSolutionRule,
    now: datetime,
    max_sigma: float,
    window_days: int,
    threshold_sigma: float | None = None,
    period_rule: PeriodRule | None = None,
) -> LinkReport:
    """Calcula (a)-(d). Solo lee. `threshold_sigma` y `period_rule` salen de
    `pipeline.toml` si no se dan."""
    if threshold_sigma is None or period_rule is None:
        config = load_pipeline_config(Settings().config_path)
        threshold_sigma = (
            config.tension.threshold_sigma if threshold_sigma is None else threshold_sigma
        )
        period_rule = period_rule_from_config(config) if period_rule is None else period_rule

    rows = _active_rows(session)
    recent = [r for r in rows if r.releasedate >= since]

    # (a)
    recent_ids = {r.arxiv_id for r in recent if r.arxiv_id}
    item_ids = set(
        session.execute(sa.select(ItemRow.external_id).where(ItemRow.source == "arxiv")).scalars()
    )
    crossing = tuple(sorted(recent_ids & item_ids))

    # (b)
    lags = []
    for r in rows:
        if not r.arxiv_id:
            continue
        month = _arxiv_month_index(r.arxiv_id)
        if month is not None:
            lags.append(_release_month_index(r.releasedate) - month)

    # (c)
    papers = _current_measurements(session)
    journal = [r for r in recent if not r.arxiv_id]
    own_rows = ambiguous_rows = 0
    for r in journal:
        archive_solution = archive_solution_from_row(r)
        kinds: list[SolutionProvenance] = []
        for parameter in _PARAMETERS:
            solution = catalog_solution_from_archive(archive_solution, parameter, is_default=False)
            if solution is None:
                continue
            planet = normalize_name(r.pl_name)
            for external_id, published_at, measurements in papers:
                matching = [
                    m
                    for m in measurements
                    if m.parameter == parameter and normalize_name(m.planet_name) == planet
                ]
                if matching:
                    kinds.append(
                        classify_solution(
                            solution,
                            external_id=external_id,
                            published_at=published_at,
                            measurements=matching,
                            rule=rule,
                        )
                    )
        if SolutionProvenance.OWN_VALUE_MATCH in kinds:
            own_rows += 1
        elif SolutionProvenance.AMBIGUOUS in kinds:
            ambiguous_rows += 1

    # (d)
    evaluations = SqlAlchemyTensionEvaluationRepository(session).all()
    changed: list[EvaluationChange] = []
    not_independent: list[ReferenceAudit] = []
    confirmations: list[ConfirmationAudit] = []
    for ev in evaluations:
        item_row = session.get(ItemRow, ev.item_id)
        if item_row is None:
            continue
        reference = ev.result.reference() if ev.result is not None else None
        if reference is not None:
            provenance = classify_solution(
                reference,
                external_id=item_row.external_id,
                published_at=item_row.published_at,
                measurements=ev.measurements,
                rule=rule,
            )
            if provenance != SolutionProvenance.INDEPENDENT:
                not_independent.append(
                    ReferenceAudit(
                        ev.id, item_row.external_id, ev.planet_name, ev.parameter, provenance
                    )
                )
            if reference_within_confirmation_limits(
                ev,
                item_published_at=item_row.published_at,
                now=now,
                max_sigma=max_sigma,
                window_days=window_days,
            ):
                confirmations.append(
                    ConfirmationAudit(
                        ev.id,
                        item_row.external_id,
                        ev.planet_name,
                        ev.parameter,
                        provenance,
                        confirmation_eligible(
                            ev,
                            item_published_at=item_row.published_at,
                            now=now,
                            max_sigma=max_sigma,
                            window_days=window_days,
                            item_external_id=item_row.external_id,
                            own_rule=rule,
                        ),
                    )
                )
        if ev.archive_planet_name is None:
            continue
        new = _recompute(
            session,
            ev,
            rule=rule,
            now=now,
            threshold_sigma=threshold_sigma,
            period_rule=period_rule,
        )
        if new is not None and (
            new.status != ev.status or new.own_solution_key != ev.own_solution_key
        ):
            changed.append(
                EvaluationChange(
                    ev.id,
                    item_row.external_id,
                    ev.planet_name,
                    ev.parameter,
                    ev.status,
                    new.status,
                    ev.own_solution_key,
                    new.own_solution_key,
                )
            )

    return LinkReport(
        a_total=len(recent),
        a_with_arxiv_id=sum(1 for r in recent if r.arxiv_id),
        a_crossing=crossing,
        b_count=len(lags),
        b_min=min(lags) if lags else None,
        b_median=float(statistics.median(lags)) if lags else None,
        b_max=max(lags) if lags else None,
        c_journal_rows=len(journal),
        c_own_value_match_rows=own_rows,
        c_ambiguous_rows=ambiguous_rows,
        d_evaluations=len(evaluations),
        d_changed=tuple(changed),
        d_reference_not_independent=tuple(not_independent),
        d_confirmations=tuple(confirmations),
    )


def render(report: LinkReport, since: date) -> str:
    lines = [
        f"since={since.isoformat()}",
        f"a.total={report.a_total}",
        f"a.with_arxiv_id={report.a_with_arxiv_id}",
        f"a.crossing={len(report.a_crossing)}",
        f"a.crossing_ids={','.join(report.a_crossing)}",
        f"b.count={report.b_count}",
        f"b.min={report.b_min}",
        f"b.median={report.b_median}",
        f"b.max={report.b_max}",
        f"c.journal_rows={report.c_journal_rows}",
        f"c.own_value_match_rows={report.c_own_value_match_rows}",
        f"c.ambiguous_rows={report.c_ambiguous_rows}",
        f"d.evaluations={report.d_evaluations}",
        f"d.changed={len(report.d_changed)}",
        f"d.reference_not_independent={len(report.d_reference_not_independent)}",
        f"d.confirmations_blocked_today={len(report.d_confirmations)}",
        "d.confirmations_still_eligible="
        f"{sum(1 for c in report.d_confirmations if c.still_eligible)}",
    ]
    for change in report.d_changed:
        lines.append(
            f"  cambio: {change.external_id} {change.planet_name} {change.parameter.value} "
            f"{change.old_status} -> {change.new_status} "
            f"(own {change.old_own_solution_key} -> {change.new_own_solution_key})"
        )
    for audit in report.d_reference_not_independent:
        lines.append(
            f"  referencia no independiente: {audit.external_id} {audit.planet_name} "
            f"{audit.parameter.value} {audit.provenance.value}"
        )
    for conf in report.d_confirmations:
        lines.append(
            f"  confirmacion: {conf.external_id} {conf.planet_name} {conf.parameter.value} "
            f"{conf.provenance.value} sigue_elegible={conf.still_eligible}"
        )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, *, now: datetime | None = None) -> int:
    parser = argparse.ArgumentParser(description="Informe de enlace entre vías (T83).")
    parser.add_argument(
        "--since",
        type=date.fromisoformat,
        default=DEFAULT_SINCE,
        help="fecha mínima de releasedate para (a) y (c); por defecto 2026-08-01",
    )
    args = parser.parse_args(argv)

    settings = Settings()
    config = load_pipeline_config(settings.config_path)
    engine = create_db_engine(settings)
    factory = create_session_factory(engine)
    with unit_of_work(factory, commit=False) as session:
        report = build_report(
            session,
            since=args.since,
            rule=own_solution_rule_from_config(config),
            now=now if now is not None else datetime.now(UTC),
            max_sigma=config.measurement_findings.confirmation_max_sigma,
            window_days=config.measurement_findings.confirmation_window_days,
            threshold_sigma=config.tension.threshold_sigma,
            period_rule=period_rule_from_config(config),
        )
    sys.stdout.write(render(report, args.since) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
