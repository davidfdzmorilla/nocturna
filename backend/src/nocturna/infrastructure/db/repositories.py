"""Implementaciones SQLAlchemy de los repositorios de dominio.

Cada clase cumple estructuralmente (sin heredar) el `Protocol`
correspondiente de `domain/repositories.py`. No hay `BaseRepository[T]`: la
skill `ddd-conventions` lo rechaza explícitamente como sobrearquitectura, y
cada repositorio tiene formas de consulta lo bastante distintas (un
`add_many` con deduplicación, dos agregados en `AgentCallRepository`, un
`current()` que debe ser ruidoso ante ambigüedad) como para que una base
genérica no ahorrara nada.

**Regla dura**: ningún método de este módulo confirma ni deshace la
transacción de la sesión (ni `commit`, ni `rollback`), ni abre una
transacción explícita. Eso es cosa exclusiva de `unit_of_work` en
`session.py`. Si un repositorio confirmara su propia transacción, un
`AgentCall` y el incremento de `Run.tokens_used` que `application/` escribe
en la misma unidad de trabajo (ver `CLAUDE.md`, control de gasto) podrían
quedar repartidos en dos transacciones distintas; un fallo entre medias
dejaría el acumulado de gasto corto de una llamada que ya se cobró. Los
métodos que lo necesitan usan `flush()` (visibilidad dentro de la misma
transacción, sin cerrarla), nunca confirman la transacción.
"""

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nocturna.domain.entities import AgentCall, Finding, Item, ItemStatus, Reading, Run, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.mappers import (
    agent_call_to_row,
    finding_from_row,
    finding_to_row,
    item_from_row,
    reading_from_row,
    reading_to_row,
    run_from_row,
    run_to_row,
)
from nocturna.infrastructure.db.models import AgentCallRow, FindingRow, ItemRow, ReadingRow, RunRow


class SqlAlchemyItemRepository:
    """Persistencia de `Item`. Cumple `domain.repositories.ItemRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, items: list[Item]) -> int:
        """Inserta ítems nuevos, deduplicando por `(source, external_id)`.

        Deduplica la lista de entrada antes de tocar la base de datos,
        quedándose con la primera aparición de cada `(source, external_id)`:
        un `INSERT ... ON CONFLICT DO NOTHING` con dos filas duplicadas en el
        mismo lote no está definido de forma útil (PostgreSQL no garantiza
        cuál "gana" dentro de la misma sentencia), así que no se delega esa
        decisión en la base de datos.
        """
        if not items:
            return 0

        deduplicated: dict[tuple[str, str], Item] = {}
        for item in items:
            key = (item.source, item.external_id)
            if key not in deduplicated:
                deduplicated[key] = item

        rows = [
            {
                "id": item.id,
                "source": item.source,
                "external_id": item.external_id,
                "title": item.title,
                "abstract": item.abstract,
                "categories": list(item.categories),
                "published_at": item.published_at,
                "fetched_at": item.fetched_at,
                "status": item.status,
            }
            for item in deduplicated.values()
        ]

        stmt = pg_insert(ItemRow).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["source", "external_id"])
        # `result.rowcount` no es fiable aquí: con SQLAlchemy 2.0.54 +
        # psycopg + PostgreSQL 16, un `INSERT ... ON CONFLICT DO NOTHING`
        # construido desde el modelo declarativo devuelve `rowcount == -1`
        # aunque las filas se inserten correctamente. `.returning(ItemRow.id)`
        # sí es fiable: `ON CONFLICT DO NOTHING` solo deja pasar por el
        # `RETURNING` las filas que de verdad se insertaron, así que contar
        # las filas devueltas da el número real de ítems nuevos.
        stmt = stmt.returning(ItemRow.id)
        result = self._session.execute(stmt)
        return len(result.all())

    def get(self, item_id: UUID) -> Item | None:
        row = self._session.get(ItemRow, item_id)
        return item_from_row(row) if row is not None else None

    def next_unread(self, limit: int) -> list[Item]:
        # Orden de llegada: `fetched_at ASC` es la columna que refleja cuándo
        # entró el ítem a la base. No basta como criterio único: dos ítems de
        # la misma ingesta pueden compartir `fetched_at` al milisegundo, y sin
        # desempate PostgreSQL puede devolverlos en cualquier orden entre
        # ellos. `external_id ASC` desempata de forma determinista para que
        # `run-item` y una re-ejecución de la noche vean siempre el mismo
        # orden.
        stmt = (
            select(ItemRow)
            .where(ItemRow.status == ItemStatus.NEW)
            .order_by(ItemRow.fetched_at.asc(), ItemRow.external_id.asc())
            .limit(limit)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [item_from_row(row) for row in rows]

    def save(self, item: Item) -> None:
        row = self._session.get(ItemRow, item.id)
        if row is None:
            raise LookupError(f"no existe Item con id={item.id}")
        row.status = item.status


class SqlAlchemyReadingRepository:
    """Persistencia de `Reading`. Cumple `domain.repositories.ReadingRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, reading: Reading) -> None:
        self._session.add(reading_to_row(reading))

    def get_for_item(self, item_id: UUID) -> Reading | None:
        stmt = select(ReadingRow).where(ReadingRow.item_id == item_id)
        row = self._session.execute(stmt).scalar_one_or_none()
        return reading_from_row(row) if row is not None else None


class SqlAlchemyFindingRepository:
    """Persistencia de `Finding`. Cumple `domain.repositories.FindingRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, finding: Finding) -> None:
        self._session.add(finding_to_row(finding))

    def unpublished_for_run(self, run_id: UUID) -> list[Finding]:
        stmt = select(FindingRow).where(
            FindingRow.run_id == run_id, FindingRow.published_at.is_(None)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [finding_from_row(row) for row in rows]

    def save(self, finding: Finding) -> None:
        row = self._session.get(FindingRow, finding.id)
        if row is None:
            raise LookupError(f"no existe Finding con id={finding.id}")
        row.confidence = finding.confidence
        row.published_at = finding.published_at


class SqlAlchemyRunRepository:
    """Persistencia de `Run`. Cumple `domain.repositories.RunRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: Run) -> None:
        self._session.add(run_to_row(run))

    def get(self, run_id: UUID) -> Run | None:
        row = self._session.get(RunRow, run_id)
        return run_from_row(row) if row is not None else None

    def current(self) -> Run | None:
        # `scalar_one_or_none()`, no `first()`: el índice único parcial
        # `uq_runs_status_running` (models.py) ya impide dos filas `running`
        # a nivel de base de datos, pero si alguna vez hubiera dos, quiero
        # un error ruidoso aquí, no que este método elija una en silencio.
        stmt = select(RunRow).where(RunRow.status == RunStatus.RUNNING)
        row = self._session.execute(stmt).scalar_one_or_none()
        return run_from_row(row) if row is not None else None

    def save(self, run: Run) -> None:
        """Persiste el estado actual del `Run`, incluido `tokens_used`.

        `runs.tokens_used` es una **caché desnormalizada**: la fuente de
        verdad del gasto de la noche es la suma de `agent_calls`
        (`AgentCallRepository.tokens_used_for_run`). Este método escribe lo
        que traiga la entidad `Run` en memoria, sea lo que sea, incluido un
        valor manipulado o desincronizado; no lo recalcula ni lo valida
        contra la base. Por eso `BudgetGuard` (T30) nunca debe leer
        `Run.tokens_used` para decidir si autoriza una llamada: debe leer
        siempre `tokens_used_for_run`.
        """
        row = self._session.get(RunRow, run.id)
        if row is None:
            raise LookupError(f"no existe Run con id={run.id}")
        row.status = run.status
        row.finished_at = run.finished_at
        row.tokens_used = run.tokens_used
        row.items_fetched = run.items_fetched
        row.items_read = run.items_read
        row.findings_published = run.findings_published
        row.notes = run.notes


class SqlAlchemyAgentCallRepository:
    """Persistencia de `AgentCall`. Cumple `domain.repositories.AgentCallRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, call: AgentCall) -> None:
        self._session.add(agent_call_to_row(call))

    def tokens_used_for_run(self, run_id: UUID) -> int:
        """Suma de `tokens_in + tokens_out` de todas las llamadas del Run.

        Cuenta llamadas de **cualquier** `status`, no solo `ok`: una llamada
        que acabó en `timeout` o `error` también consumió tokens de la
        suscripción, y filtrar por `ok` regalaría presupuesto que ya se
        gastó. No "arreglar" esto añadiendo un `WHERE status = 'ok'`.

        `COALESCE(..., 0)` es imprescindible: sin llamadas registradas,
        `SUM` devuelve `NULL` en SQL, y `BudgetGuard` espera un `int`, no un
        `None`.
        """
        stmt = select(
            func.coalesce(func.sum(AgentCallRow.tokens_in + AgentCallRow.tokens_out), 0)
        ).where(AgentCallRow.run_id == run_id)
        return self._session.execute(stmt).scalar_one()

    def count_for_run(self, run_id: UUID, agent: AgentRole) -> int:
        """Número de llamadas registradas para `run_id` y `agent`.

        Cuenta llamadas de **cualquier** `status`, incluidos `invalid_output`,
        `error` y `timeout`, no solo las que llegaron a producir una salida
        válida. Decisión de T30 (ADR 0005): lo que cuesta presupuesto de la
        suscripción es el intento, no el acierto, así que el tope se
        comprueba sobre todas las llamadas, de cualquier estado. La clave
        que hace esto compatible con la regla de reintento por JSON
        inválido ("si el JSON no valida, un reintento; si falla de nuevo, el
        ítem se marca failed") es `max_editor_calls_per_night = 2`
        (`pipeline.toml`): con tope 1, un primer intento fallido ya
        agotaría "la llamada de la noche" y consumiría el reintento en
        silencio; con tope 2, el reintento sigue disponible aunque se
        cuenten los intentos y no solo los aciertos. `BudgetGuard` usa este
        mismo conteo también para Reader y Popularizer, vía
        `max_calls_per_item * max_items_per_night`.
        """
        stmt = select(func.count()).where(
            AgentCallRow.run_id == run_id, AgentCallRow.agent == agent
        )
        return self._session.execute(stmt).scalar_one()
