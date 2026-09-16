"""Traducción entidad de dominio ↔ fila ORM, campo a campo.

Diez funciones puras, dos por entidad (`*_to_row` / `*_from_row`), todas con
argumentos nombrados explícitos. Deliberadamente **no** se usa
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
    )
