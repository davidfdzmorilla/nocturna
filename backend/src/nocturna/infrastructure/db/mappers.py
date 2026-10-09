"""Traducción entidad de dominio ↔ fila ORM, campo a campo.

Diez funciones puras, dos por entidad (`*_to_row` / `*_from_row`), todas con
argumentos nombrados explícitos, más `_measurements_to_json` /
`_measurements_from_json` (T71.c), la traducción de
`Reading.measurements` al `JSONB` de `ReadingRow.measurements` y su
inversa. Deliberadamente **no** se usa
`**vars(entity)`, `dataclasses.asdict()` ni un bucle sobre
`dataclasses.fields(entity)`: si mañana se añade un campo al dominio y se
olvida aquí, un mapper genérico lo colaría en silencio (columna `NULL` o
atributo ausente sin que nada avise); con argumentos nombrados, el mapper
revienta con un `TypeError` en el sitio exacto donde falta el campo.

La rehidratación (`*_from_row`) construye la entidad **por su constructor
normal**, id incluido. Nunca se llama a un método de transición
(`mark_read()`, `publish()`, `finish()`, `record_agent_call()`) para
reconstruir desde una fila: esos métodos validan una *transición*, y una
fila ya persistida no está transicionando, ya está en el estado que está.
Las guardas de `__setattr__` en `domain/entities.py` rechazan la
*reasignación* de un campo guardado, no su primera asignación por
`__init__`, así que pasar el `status`/`confidence`/`published_at` ya
resueltos al constructor es válido y no dispara ninguna guarda.
"""

from dataclasses import replace
from datetime import date, datetime
from uuid import UUID, uuid4

from nocturna.domain.archive import (
    ArchiveParameterValue,
    ArchiveSnapshot,
    ArchiveSolution,
    DefaultChange,
)
from nocturna.domain.entities import (
    AgentCall,
    CatalogSolution,
    CatalogTension,
    CatalogTensionComparison,
    Finding,
    FirstMeasurement,
    IndependentConfirmation,
    Item,
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
    Reading,
    Run,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.tension import (
    EvaluationStatus,
    LimitComparison,
    LimitOutcome,
    PeriodCheck,
    TensionEvaluation,
    TensionResult,
    compare,
)
from nocturna.infrastructure.db.models import (
    AgentCallRow,
    ArchiveDefaultChangeRow,
    ArchiveSnapshotRow,
    ArchiveSolutionRow,
    FindingRow,
    ItemRow,
    ReadingRow,
    RunRow,
    TensionEvaluationRow,
)


def _measurement_to_dict(measurement: Measurement) -> dict:
    """Un `Measurement` como dict JSON; enums por `.value`."""
    return {
        "planet_name": measurement.planet_name,
        "parameter": measurement.parameter.value,
        "value": measurement.value,
        "err_plus": measurement.err_plus,
        "err_minus": measurement.err_minus,
        "unit": measurement.unit.value,
        "limit": measurement.limit.value,
        "origin": measurement.origin.value,
        "evidence": measurement.evidence,
    }


def _measurement_from_dict(entry: dict) -> Measurement:
    """Inversa de `_measurement_to_dict`; pasa por `__post_init__`."""
    return Measurement(
        planet_name=entry["planet_name"],
        parameter=MeasuredParameter(entry["parameter"]),
        value=entry["value"],
        err_plus=entry["err_plus"],
        err_minus=entry["err_minus"],
        unit=MeasurementUnit(entry["unit"]),
        limit=MeasurementLimit(entry["limit"]),
        origin=MeasurementOrigin(entry["origin"]),
        evidence=entry["evidence"],
    )


def _measurements_to_json(
    measurements: tuple[Measurement, ...] | None,
) -> list[dict] | None:
    """`None` -> SQL `NULL` ("no se extrajo con este prompt"); `()` -> `[]`
    ("se buscó y no había ninguna medida"). Ver `Reading.measurements` y el
    `mapped_column(JSONB(none_as_null=True))` de `ReadingRow.measurements`
    en `models.py`: sin ese `none_as_null=True`, esta distinción se perdería
    en la columna. Los enums se guardan por su *valor* string (`.value`),
    no por el nombre del miembro, igual que `_str_enum` en `models.py`.
    """
    if measurements is None:
        return None
    return [_measurement_to_dict(measurement) for measurement in measurements]


def _measurements_from_json(
    raw: list[dict] | None,
) -> tuple[Measurement, ...] | None:
    """Inversa de `_measurements_to_json`. SQL `NULL` -> `None`; `[]` -> `()`.

    Reconstruye cada `Measurement` por su constructor normal: pasa por
    `__post_init__` y sus invariantes, no las repite aquí.
    """
    if raw is None:
        return None
    return tuple(_measurement_from_dict(entry) for entry in raw)


# Versión del JSON de `findings.catalog_tension`. Solo existe en
# infraestructura; el dominio no la conoce.
CATALOG_TENSION_SCHEMA_VERSION = 1


def _solution_to_dict(solution: CatalogSolution) -> dict:
    return {
        "planet_name": solution.planet_name,
        "parameter": solution.parameter.value,
        "value": solution.value,
        "err_plus": solution.err_plus,
        "err_minus": solution.err_minus,
        "unit": solution.unit.value,
        "limit": solution.limit.value,
        "reference": solution.reference,
        "is_default": solution.is_default,
        "arxiv_id": solution.arxiv_id,
        # T88: claves aditivas y opcionales (schema_version sigue en 1).
        "solution_key": solution.solution_key,
        "soltype": solution.soltype,
        "pl_pubdate": solution.pl_pubdate,
        "releasedate": None if solution.releasedate is None else solution.releasedate.isoformat(),
        "ttv_flag": solution.ttv_flag,
    }


def _solution_from_dict(entry: dict) -> CatalogSolution:
    return CatalogSolution(
        planet_name=entry["planet_name"],
        parameter=MeasuredParameter(entry["parameter"]),
        value=entry["value"],
        err_plus=entry["err_plus"],
        err_minus=entry["err_minus"],
        unit=MeasurementUnit(entry["unit"]),
        limit=MeasurementLimit(entry["limit"]),
        reference=entry["reference"],
        is_default=entry["is_default"],
        arxiv_id=entry["arxiv_id"],
        solution_key=entry.get("solution_key"),
        soltype=entry.get("soltype"),
        pl_pubdate=entry.get("pl_pubdate"),
        releasedate=(
            None if entry.get("releasedate") is None else date.fromisoformat(entry["releasedate"])
        ),
        ttv_flag=entry.get("ttv_flag"),
    )


def _catalog_tension_to_json(tension: CatalogTension | None) -> dict | None:
    """`None` -> SQL `NULL` (finding que no es `catalog_tension`)."""
    if tension is None:
        return None
    return {
        "schema_version": CATALOG_TENSION_SCHEMA_VERSION,
        "planet_name": tension.planet_name,
        "parameter": tension.parameter.value,
        "archive_url": tension.archive_url,
        "threshold_sigma": tension.threshold_sigma,
        "reference_sigma": tension.reference_sigma,
        "comparisons": [
            {
                "paper": _measurement_to_dict(comparison.paper),
                "prior": _solution_to_dict(comparison.prior),
                "sigma": comparison.sigma,
            }
            for comparison in tension.comparisons
        ],
    }


def _catalog_tension_from_json(raw: dict | None) -> CatalogTension | None:
    """Inversa. `schema_version` ausente o distinta de la conocida -> `ValueError`."""
    if raw is None:
        return None
    version = raw.get("schema_version")
    if type(version) is not int or version != CATALOG_TENSION_SCHEMA_VERSION:
        raise ValueError(
            f"catalog_tension: schema_version {version!r} desconocida "
            f"(soportada: {CATALOG_TENSION_SCHEMA_VERSION})"
        )
    return CatalogTension(
        planet_name=raw["planet_name"],
        parameter=MeasuredParameter(raw["parameter"]),
        archive_url=raw["archive_url"],
        threshold_sigma=raw["threshold_sigma"],
        reference_sigma=raw["reference_sigma"],
        comparisons=tuple(
            CatalogTensionComparison(
                paper=_measurement_from_dict(entry["paper"]),
                prior=_solution_from_dict(entry["prior"]),
                sigma=entry["sigma"],
            )
            for entry in raw["comparisons"]
        ),
    )


def item_to_row(item: Item) -> ItemRow:
    return ItemRow(
        id=item.id,
        source=item.source,
        external_id=item.external_id,
        title=item.title,
        abstract=item.abstract,
        categories=list(item.categories),
        published_at=item.published_at,
        fetched_at=item.fetched_at,
        status=item.status,
        exoplanet_match=item.exoplanet_match,
    )


def item_from_row(row: ItemRow) -> Item:
    # `list(...)`: la entidad no debe compartir la lista mutable de la fila
    # ORM. Mutar `item.categories` desde el dominio no debe mutar la fila.
    return Item(
        id=row.id,
        source=row.source,
        external_id=row.external_id,
        title=row.title,
        abstract=row.abstract,
        categories=list(row.categories),
        published_at=row.published_at,
        fetched_at=row.fetched_at,
        status=row.status,
        exoplanet_match=row.exoplanet_match,
    )


def reading_to_row(reading: Reading) -> ReadingRow:
    # Conversión explícita a `list`: la columna ARRAY no acepta una tupla.
    return ReadingRow(
        id=reading.id,
        item_id=reading.item_id,
        summary=reading.summary,
        objects=list(reading.objects),
        claims=list(reading.claims),
        interest_score=reading.interest_score,
        tokens_in=reading.tokens_in,
        tokens_out=reading.tokens_out,
        model=reading.model,
        measurements=_measurements_to_json(reading.measurements),
        prompt_version=reading.prompt_version,
    )


def reading_from_row(row: ReadingRow) -> Reading:
    # `objects`/`claims` llegan como `list` de la columna ARRAY; se pasan
    # tal cual y es `Reading.__post_init__` quien los normaliza a tupla.
    return Reading(
        id=row.id,
        item_id=row.item_id,
        summary=row.summary,
        objects=row.objects,
        claims=row.claims,
        interest_score=row.interest_score,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        model=row.model,
        measurements=_measurements_from_json(row.measurements),
        prompt_version=row.prompt_version,
    )


def finding_to_row(finding: Finding) -> FindingRow:
    return FindingRow(
        id=finding.id,
        item_id=finding.item_id,
        run_id=finding.run_id,
        type=finding.type,
        title=finding.title,
        level_curious=finding.level_curious,
        level_amateur=finding.level_amateur,
        level_technical=finding.level_technical,
        confidence=finding.confidence,
        published_at=finding.published_at,
        catalog_tension=_catalog_tension_to_json(finding.catalog_tension),
        first_measurement=(
            None if finding.first_measurement is None else finding.first_measurement.to_json()
        ),
        independent_confirmation=(
            None
            if finding.independent_confirmation is None
            else finding.independent_confirmation.to_json()
        ),
        tension_evaluation_id=finding.tension_evaluation_id,
    )


def finding_from_row(row: FindingRow) -> Finding:
    return Finding(
        id=row.id,
        item_id=row.item_id,
        run_id=row.run_id,
        type=row.type,
        title=row.title,
        level_curious=row.level_curious,
        level_amateur=row.level_amateur,
        level_technical=row.level_technical,
        confidence=row.confidence,
        published_at=row.published_at,
        catalog_tension=_catalog_tension_from_json(row.catalog_tension),
        first_measurement=(
            None
            if row.first_measurement is None
            else FirstMeasurement.from_json(row.first_measurement)
        ),
        independent_confirmation=(
            None
            if row.independent_confirmation is None
            else IndependentConfirmation.from_json(row.independent_confirmation)
        ),
        tension_evaluation_id=row.tension_evaluation_id,
    )


def run_to_row(run: Run) -> RunRow:
    return RunRow(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        status=run.status,
        budget_tokens=run.budget_tokens,
        tokens_used=run.tokens_used,
        items_fetched=run.items_fetched,
        items_read=run.items_read,
        findings_published=run.findings_published,
        notes=run.notes,
    )


def run_from_row(row: RunRow) -> Run:
    return Run(
        id=row.id,
        started_at=row.started_at,
        finished_at=row.finished_at,
        status=row.status,
        budget_tokens=row.budget_tokens,
        tokens_used=row.tokens_used,
        items_fetched=row.items_fetched,
        items_read=row.items_read,
        findings_published=row.findings_published,
        notes=row.notes,
    )


def agent_call_to_row(call: AgentCall) -> AgentCallRow:
    return AgentCallRow(
        id=call.id,
        run_id=call.run_id,
        item_id=call.item_id,
        agent=call.agent,
        model=call.model,
        tokens_in=call.tokens_in,
        tokens_out=call.tokens_out,
        duration_ms=call.duration_ms,
        status=call.status,
        prompt_version=call.prompt_version,
    )


def agent_call_from_row(row: AgentCallRow) -> AgentCall:
    return AgentCall(
        id=row.id,
        run_id=row.run_id,
        item_id=row.item_id,
        agent=row.agent,
        model=row.model,
        tokens_in=row.tokens_in,
        tokens_out=row.tokens_out,
        duration_ms=row.duration_ms,
        status=row.status,
        prompt_version=row.prompt_version,
    )


# --- Exoplanet Archive (T81) -------------------------------------------------

# Parametros con columnas `<prefijo>_value/_err1/_err2[/_lim]`. Masa, radio y
# periodo llevan `lim`; las magnitudes estelares no.
_LIM_PARAMS = ("mass", "radius", "period")
_STAR_PARAMS = ("st_rad", "st_mass")


def archive_snapshot_to_row(snapshot: ArchiveSnapshot) -> ArchiveSnapshotRow:
    return ArchiveSnapshotRow(
        id=snapshot.id,
        taken_at=snapshot.taken_at,
        kind=snapshot.kind,
        max_releasedate=snapshot.max_releasedate,
        rows_total=snapshot.rows_total,
        defaults_total=snapshot.defaults_total,
        duplicate_rows=snapshot.duplicate_rows,
        payload_sha256=snapshot.payload_sha256,
        requests=snapshot.requests,
        duration_ms=snapshot.duration_ms,
    )


def archive_snapshot_from_row(row: ArchiveSnapshotRow) -> ArchiveSnapshot:
    return ArchiveSnapshot(
        id=row.id,
        taken_at=row.taken_at,
        kind=row.kind,
        max_releasedate=row.max_releasedate,
        rows_total=row.rows_total,
        defaults_total=row.defaults_total,
        duplicate_rows=row.duplicate_rows,
        payload_sha256=row.payload_sha256,
        requests=row.requests,
        duration_ms=row.duration_ms,
    )


def archive_solution_to_values(solution: ArchiveSolution, *, snapshot_id: UUID) -> dict:
    """Valores de INSERT de una solucion tal como se ve por primera vez en `snapshot_id`.

    Devuelve un dict (no una fila) porque el repositorio la escribe con
    `INSERT ... ON CONFLICT` por bloques. `is_default_current` y `removed_at`
    nacen en falso/nulo; el repositorio los fija despues segun el diff.
    """
    values: dict = {
        "solution_key": solution.solution_key,
        "pl_name": solution.pl_name,
        "hostname": solution.hostname,
        "pl_refname": solution.pl_refname,
        "ref_key": solution.ref_key,
        "ref_text": solution.ref_text,
        "arxiv_id": solution.arxiv_id,
        "soltype": solution.soltype or "",
        "releasedate": solution.releasedate,
        "pl_pubdate": solution.pl_pubdate,
        "pl_bmassprov": solution.pl_bmassprov,
        "discoverymethod": solution.discoverymethod,
        "ttv_flag": solution.ttv_flag,
        "pl_controv_flag": solution.pl_controv_flag,
        "is_default": solution.is_default,
        "first_seen_snapshot_id": snapshot_id,
        "last_seen_snapshot_id": snapshot_id,
        "is_default_current": False,
        "removed_at": None,
    }
    for prefix in (*_LIM_PARAMS, *_STAR_PARAMS):
        parameter: ArchiveParameterValue = getattr(solution, prefix)
        values[f"{prefix}_value"] = parameter.value
        values[f"{prefix}_err1"] = parameter.err1
        values[f"{prefix}_err2"] = parameter.err2
        if prefix in _LIM_PARAMS:
            values[f"{prefix}_lim"] = parameter.lim
    return values


def archive_solution_from_row(row: ArchiveSolutionRow) -> ArchiveSolution:
    """Reconstruye la solucion; `solution_key` se recalcula, no se lee de la fila."""

    def lim_param(prefix: str) -> ArchiveParameterValue:
        return ArchiveParameterValue(
            value=getattr(row, f"{prefix}_value"),
            err1=getattr(row, f"{prefix}_err1"),
            err2=getattr(row, f"{prefix}_err2"),
            lim=getattr(row, f"{prefix}_lim"),
        )

    def star_param(prefix: str) -> ArchiveParameterValue:
        return ArchiveParameterValue(
            value=getattr(row, f"{prefix}_value"),
            err1=getattr(row, f"{prefix}_err1"),
            err2=getattr(row, f"{prefix}_err2"),
        )

    return ArchiveSolution(
        pl_name=row.pl_name,
        hostname=row.hostname or "",
        pl_refname=row.pl_refname,
        ref_key=row.ref_key,
        ref_text=row.ref_text or "",
        arxiv_id=row.arxiv_id,
        soltype=row.soltype or None,
        releasedate=row.releasedate,
        pl_pubdate=row.pl_pubdate,
        is_default=row.is_default,
        mass=lim_param("mass"),
        radius=lim_param("radius"),
        period=lim_param("period"),
        pl_bmassprov=row.pl_bmassprov,
        st_rad=star_param("st_rad"),
        st_mass=star_param("st_mass"),
        discoverymethod=row.discoverymethod,
        ttv_flag=row.ttv_flag,
        pl_controv_flag=row.pl_controv_flag,
    )


def archive_default_change_to_row(
    change: DefaultChange, *, snapshot_id: UUID, detected_at: datetime
) -> ArchiveDefaultChangeRow:
    """El `id` de la fila lo genera el mapper: `DefaultChange` es un value object sin identidad."""
    return ArchiveDefaultChangeRow(
        id=uuid4(),
        pl_name=change.pl_name,
        old_solution_key=change.old_key,
        new_solution_key=change.new_key,
        snapshot_id=snapshot_id,
        detected_at=detected_at,
    )


def archive_lost_default_to_row(
    pl_name: str, old_key: str, *, snapshot_id: UUID, detected_at: datetime
) -> ArchiveDefaultChangeRow:
    """Fila de pérdida de default (T84): `new_solution_key` nulo."""
    return ArchiveDefaultChangeRow(
        id=uuid4(),
        pl_name=pl_name,
        old_solution_key=old_key,
        new_solution_key=None,
        snapshot_id=snapshot_id,
        detected_at=detected_at,
    )


def archive_default_change_from_row(row: ArchiveDefaultChangeRow) -> DefaultChange:
    """Solo para filas con `new_solution_key`; una pérdida no es un `DefaultChange`."""
    if row.new_solution_key is None:
        raise InvariantViolation("archive_default_change sin new_solution_key (pérdida de default)")
    return DefaultChange(
        pl_name=row.pl_name,
        old_key=row.old_solution_key,
        new_key=row.new_solution_key,
    )


# Versión del JSON de `tension_evaluation.detail`. Solo existe en infraestructura.
TENSION_EVALUATION_SCHEMA_VERSION = 1


def _tension_result_to_dict(result: TensionResult | None) -> dict | None:
    if result is None:
        return None
    return {
        "planet_name": result.planet_name,
        "item_id": str(result.item_id),
        "reading_id": None if result.reading_id is None else str(result.reading_id),
        "comparisons": [
            {
                "paper": _measurement_to_dict(c.paper),
                "prior": _solution_to_dict(c.prior),
                "sigma": c.sigma,
            }
            for c in result.comparisons
        ],
    }


def _tension_result_from_dict(
    raw: dict | None, parameter: MeasuredParameter
) -> TensionResult | None:
    """Rehidrata las comparaciones con `compare` (valores y errores canónicos) y
    conserva el `sigma` guardado; la referencia se vuelve a derivar."""
    if raw is None:
        return None
    comparisons = tuple(
        replace(
            compare(_measurement_from_dict(entry["paper"]), _solution_from_dict(entry["prior"])),
            sigma=entry["sigma"],
        )
        for entry in raw["comparisons"]
    )
    return TensionResult(
        item_id=UUID(raw["item_id"]),
        planet_name=raw["planet_name"],
        parameter=parameter,
        comparisons=comparisons,
        reading_id=None if raw["reading_id"] is None else UUID(raw["reading_id"]),
    )


def _limit_to_dict(limit: LimitComparison | None) -> dict | None:
    if limit is None:
        return None
    return {
        "paper": [_measurement_to_dict(m) for m in limit.paper],
        "limit": _solution_to_dict(limit.limit),
        "margins": list(limit.margins),
        "outcome": limit.outcome.value,
    }


def _limit_from_dict(raw: dict | None) -> LimitComparison | None:
    if raw is None:
        return None
    return LimitComparison(
        paper=tuple(_measurement_from_dict(m) for m in raw["paper"]),
        limit=_solution_from_dict(raw["limit"]),
        margins=tuple(raw["margins"]),
        outcome=LimitOutcome(raw["outcome"]),
    )


def _period_check_to_dict(check: PeriodCheck | None) -> dict | None:
    if check is None:
        return None
    return {
        "min_difference_met": check.min_difference_met,
        "alias_suspected": check.alias_suspected,
    }


def _period_check_from_dict(raw: dict | None) -> PeriodCheck | None:
    if raw is None:
        return None
    return PeriodCheck(
        min_difference_met=raw["min_difference_met"], alias_suspected=raw["alias_suspected"]
    )


def _tension_evaluation_detail(evaluation: TensionEvaluation) -> dict:
    return {
        "schema_version": TENSION_EVALUATION_SCHEMA_VERSION,
        "measurements": _measurements_to_json(evaluation.measurements),
        "result": _tension_result_to_dict(evaluation.result),
        "limit": _limit_to_dict(evaluation.limit),
        "period_check": _period_check_to_dict(evaluation.period_check),
    }


def _tension_evaluation_derived(evaluation: TensionEvaluation) -> tuple[str | None, float | None]:
    """`(reference_solution_key, reference_sigma)`; solo si EVALUATED."""
    if evaluation.status != EvaluationStatus.EVALUATED or evaluation.result is None:
        return None, None
    reference = evaluation.result.reference()
    key = None if reference is None else reference.solution_key
    return key, evaluation.result.reference_sigma()


def tension_evaluation_to_row(evaluation: TensionEvaluation) -> TensionEvaluationRow:
    """Fila nueva: `first_evaluated_at` = `evaluated_at`."""
    key, sigma = _tension_evaluation_derived(evaluation)
    return TensionEvaluationRow(
        id=evaluation.id,
        reading_id=evaluation.reading_id,
        item_id=evaluation.item_id,
        planet_name=evaluation.planet_name,
        parameter=evaluation.parameter,
        status=evaluation.status,
        archive_planet_name=evaluation.archive_planet_name,
        reference_solution_key=key,
        reference_sigma=sigma,
        own_solution_key=evaluation.own_solution_key,
        detail=_tension_evaluation_detail(evaluation),
        first_evaluated_at=evaluation.evaluated_at,
        evaluated_at=evaluation.evaluated_at,
    )


def apply_tension_evaluation(row: TensionEvaluationRow, evaluation: TensionEvaluation) -> None:
    """Sobrescribe la fila con la evaluación; `first_evaluated_at` no cambia."""
    key, sigma = _tension_evaluation_derived(evaluation)
    row.reading_id = evaluation.reading_id
    row.item_id = evaluation.item_id
    row.planet_name = evaluation.planet_name
    row.parameter = evaluation.parameter
    row.status = evaluation.status
    row.archive_planet_name = evaluation.archive_planet_name
    row.reference_solution_key = key
    row.reference_sigma = sigma
    row.own_solution_key = evaluation.own_solution_key
    row.detail = _tension_evaluation_detail(evaluation)
    row.evaluated_at = evaluation.evaluated_at


def tension_evaluation_from_row(row: TensionEvaluationRow) -> TensionEvaluation:
    """`schema_version` ausente o desconocida -> `ValueError`."""
    detail = row.detail
    version = detail.get("schema_version")
    if type(version) is not int or version != TENSION_EVALUATION_SCHEMA_VERSION:
        raise ValueError(
            f"tension_evaluation: schema_version {version!r} desconocida "
            f"(soportada: {TENSION_EVALUATION_SCHEMA_VERSION})"
        )
    measurements = _measurements_from_json(detail["measurements"])
    assert measurements is not None  # noqa: S101
    return TensionEvaluation(
        id=row.id,
        reading_id=row.reading_id,
        item_id=row.item_id,
        planet_name=row.planet_name,
        parameter=row.parameter,
        measurements=measurements,
        status=row.status,
        evaluated_at=row.evaluated_at,
        archive_planet_name=row.archive_planet_name,
        result=_tension_result_from_dict(detail["result"], row.parameter),
        limit=_limit_from_dict(detail["limit"]),
        period_check=_period_check_from_dict(detail["period_check"]),
        own_solution_key=row.own_solution_key,
    )
