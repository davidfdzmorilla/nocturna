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

from nocturna.domain.entities import (
    AgentCall,
    Finding,
    Item,
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
    Reading,
    Run,
)
from nocturna.infrastructure.db.models import (
    AgentCallRow,
    FindingRow,
    ItemRow,
    ReadingRow,
    RunRow,
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
    return [
        {
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
        for measurement in measurements
    ]


def _measurements_from_json(
    raw: list[dict] | None,
) -> tuple[Measurement, ...] | None:
    """Inversa de `_measurements_to_json`. SQL `NULL` -> `None`; `[]` -> `()`.

    Reconstruye cada `Measurement` por su constructor normal: pasa por
    `__post_init__` y sus invariantes, no las repite aquí.
    """
    if raw is None:
        return None
    return tuple(
        Measurement(
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
        for entry in raw
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
