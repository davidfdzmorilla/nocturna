"""Modelos ORM de SQLAlchemy 2 para Nocturna.

Estas clases son el único lugar del proyecto que sabe de tablas, columnas e
índices de PostgreSQL. No son entidades de dominio: no tienen métodos de
transición ni invariantes, solo estructura de fila. La traducción
entidad ↔ fila vive en `mappers.py`; los repositorios (`repositories.py`)
son los únicos consumidores de este módulo fuera del propio paquete
`infrastructure/db/`.

Decisiones deliberadas, no cosméticas:

- **Identidad**: el `id` de cada tabla es un `UUID` generado por el dominio
  (`uuid4()` en el `__init__` de la entidad, ver `domain/entities.py`) y
  copiado por el mapper. No hay `default=uuid4` ni
  `server_default=gen_random_uuid()` aquí: si la fila generara su propio id,
  el objeto en memoria y la fila persistida tendrían identidades distintas
  hasta el siguiente `refresh()`.
- **Timestamps**: `DateTime(timezone=True)` en todos, sin `server_default`.
  El instante lo decide el dominio (por ejemplo, `Run.started_at` es el que
  vio `BudgetGuard` al comprobar la ventana de ejecución); si lo pusiera el
  servidor al `INSERT`, ambos podrían no coincidir.
- **Enums**: `sa.Enum(..., native_enum=False, create_constraint=True,
  values_callable=...)`. Sin `values_callable` SQLAlchemy persiste el
  *nombre* del miembro (`"NEW"`) en vez de su *valor* (`"new"`), que es el
  que espera la API de lectura (T50) y el resto del dominio. Sin
  `create_constraint=True` (no es el valor por defecto en SQLAlchemy 2.0
  cuando `native_enum=False`), la columna se materializa como `VARCHAR` sin
  ningún `CHECK`: cualquier cadena entraría, y la única garantía de que el
  valor es uno de los del enum quedaría en el dominio, no en la base de
  datos.
- **`ARRAY(Text)` en vez de JSONB** para `categories`, `objects` y `claims`:
  son listas de cadenas simples, no estructuras anidadas, y no se consultan
  por contenido en fase 1, así que no hace falta índice GIN.
- **Sin `relationship()`**: las relaciones perezosas del ORM son el camino
  más fácil por el que un modelo de SQLAlchemy se cuela en `application/`.
  Los repositorios navegan por `id` explícito, con `session.get()` o
  `select()`.
- **FK sin `ondelete`**: en fase 1 nada se borra. Un `ON DELETE CASCADE`
  sobre `agent_calls` borraría el registro de gasto de un ítem si algún día
  se borrara ese ítem, y el gasto ya ocurrió: borrar el registro sería
  falsificar el histórico de consumo.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from nocturna.domain.entities import (
    AgentCallStatus,
    FindingType,
    ItemStatus,
    RunStatus,
)
from nocturna.domain.llm import AgentRole

# Convención de nombres completa. Sin ella, los CHECK de los enums (y el
# resto de constraints) salen con nombres autogenerados por PostgreSQL
# (p. ej. "items_status_check1") que Alembic no puede referenciar de forma
# estable en un `downgrade`.
NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = sa.MetaData(naming_convention=NAMING_CONVENTION)


def _str_enum(enum_cls: type, name: str) -> sa.Enum:
    """`sa.Enum` que persiste el *valor* del `StrEnum`, no el nombre del miembro.

    `values_callable` es obligatorio: sin él, SQLAlchemy usa por defecto
    `enum.name`, así que `ItemStatus.NEW` se guardaría como `"NEW"` en vez de
    `"new"`. `native_enum=False` lo materializa como `VARCHAR` en vez de un
    tipo `ENUM` nativo de PostgreSQL, que exige un `ALTER TYPE` aparte para
    añadir valores y complica el `downgrade` de Alembic. `create_constraint`
    no es `True` por defecto en SQLAlchemy 2.0 para este caso: sin pasarlo
    explícitamente, la columna quedaría como `VARCHAR` sin `CHECK` alguno, y
    cualquier valor inventado entraría por SQL directo. Con
    `create_constraint=True` y la `NAMING_CONVENTION` del módulo, el `CHECK`
    generado sale nombrado `ck_<tabla>_<name>` (p. ej. `ck_items_item_status`),
    referenciable de forma estable desde el `downgrade` de una migración.
    """

    return sa.Enum(
        enum_cls,
        native_enum=False,
        length=20,
        values_callable=lambda e: [member.value for member in e],
        name=name,
        create_constraint=True,
    )


class ItemRow(Base):
    __tablename__ = "items"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    source: Mapped[str] = mapped_column(sa.Text, nullable=False)
    external_id: Mapped[str] = mapped_column(sa.Text, nullable=False)
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    abstract: Mapped[str] = mapped_column(sa.Text, nullable=False)
    categories: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), nullable=False)
    published_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    status: Mapped[ItemStatus] = mapped_column(_str_enum(ItemStatus, "item_status"), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("source", "external_id", name="uq_items_source_external_id"),
    )


class ReadingRow(Base):
    __tablename__ = "readings"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False
    )
    summary: Mapped[str] = mapped_column(sa.Text, nullable=False)
    objects: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), nullable=False)
    claims: Mapped[list[str]] = mapped_column(ARRAY(sa.Text), nullable=False)
    interest_score: Mapped[int] = mapped_column(sa.SmallInteger, nullable=False)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    model: Mapped[str] = mapped_column(sa.String(100), nullable=False)

    __table_args__ = (
        sa.UniqueConstraint("item_id", name="uq_readings_item_id"),
        sa.CheckConstraint("interest_score BETWEEN 1 AND 5", name="interest_score_range"),
        sa.CheckConstraint("tokens_in >= 0", name="tokens_in_non_negative"),
        sa.CheckConstraint("tokens_out >= 0", name="tokens_out_non_negative"),
    )


class FindingRow(Base):
    __tablename__ = "findings"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    item_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=False
    )
    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False
    )
    type: Mapped[FindingType] = mapped_column(
        _str_enum(FindingType, "finding_type"), nullable=False
    )
    title: Mapped[str] = mapped_column(sa.Text, nullable=False)
    level_curious: Mapped[str] = mapped_column(sa.Text, nullable=False)
    level_amateur: Mapped[str] = mapped_column(sa.Text, nullable=False)
    level_technical: Mapped[str] = mapped_column(sa.Text, nullable=False)
    confidence: Mapped[float | None] = mapped_column(sa.Double, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)

    __table_args__ = (
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1", name="confidence_range"
        ),
        sa.CheckConstraint(
            "(confidence IS NULL) = (published_at IS NULL)",
            name="confidence_published_at_together",
        ),
        sa.Index(
            "ix_findings_published_at",
            sa.text("published_at DESC"),
            postgresql_where=sa.text("published_at IS NOT NULL"),
        ),
    )


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    started_at: Mapped[datetime] = mapped_column(sa.DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True), nullable=True)
    status: Mapped[RunStatus] = mapped_column(_str_enum(RunStatus, "run_status"), nullable=False)
    budget_tokens: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    tokens_used: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    items_fetched: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    items_read: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    findings_published: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    notes: Mapped[str] = mapped_column(sa.Text, nullable=False)

    __table_args__ = (
        sa.CheckConstraint("budget_tokens > 0", name="budget_tokens_positive"),
        sa.CheckConstraint("tokens_used >= 0", name="tokens_used_non_negative"),
        sa.CheckConstraint("items_fetched >= 0", name="items_fetched_non_negative"),
        sa.CheckConstraint("items_read >= 0", name="items_read_non_negative"),
        sa.CheckConstraint("findings_published >= 0", name="findings_published_non_negative"),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name="running_iff_finished_at_null",
        ),
        # Control de gasto: impide dos Run abiertos a la vez. Dos noches
        # solapadas gastarían el doble del presupuesto semanal.
        sa.Index(
            "uq_runs_status_running",
            "status",
            unique=True,
            postgresql_where=sa.text("status = 'running'"),
        ),
    )


class AgentCallRow(Base):
    __tablename__ = "agent_calls"

    id: Mapped[uuid.UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column(
        PgUUID(as_uuid=True), sa.ForeignKey("runs.id"), nullable=False
    )
    item_id: Mapped[uuid.UUID | None] = mapped_column(
        PgUUID(as_uuid=True), sa.ForeignKey("items.id"), nullable=True
    )
    agent: Mapped[AgentRole] = mapped_column(_str_enum(AgentRole, "agent_role"), nullable=False)
    model: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    tokens_in: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    tokens_out: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    duration_ms: Mapped[int] = mapped_column(sa.Integer, nullable=False)
    status: Mapped[AgentCallStatus] = mapped_column(
        _str_enum(AgentCallStatus, "agent_call_status"), nullable=False
    )

    __table_args__ = (
        sa.CheckConstraint("tokens_in >= 0", name="tokens_in_non_negative"),
        sa.CheckConstraint("tokens_out >= 0", name="tokens_out_non_negative"),
        sa.CheckConstraint("duration_ms >= 0", name="duration_ms_non_negative"),
        # Sirve `count_for_run` (run_id, agent) y `tokens_used_for_run` por
        # prefijo izquierdo (run_id solo).
        sa.Index("ix_agent_calls_run_id_agent", "run_id", "agent"),
    )
