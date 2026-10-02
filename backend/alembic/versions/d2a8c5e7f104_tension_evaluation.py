"""tension evaluation

Revision ID: d2a8c5e7f104
Revises: b4d7f1a26c93
Create Date: 2026-10-02 12:00:00.000000

T88: `tension_evaluation`, el resultado de evaluar (reading, planeta,
parametro) frente al catalogo. Tabla nueva, sin tocar las existentes. `detail`
(JSONB, schema_version 1) guarda medidas, comparaciones, cota y chequeo de
periodo; las columnas sueltas son la proyeccion consultable.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2a8c5e7f104"
down_revision: str | Sequence[str] | None = "b4d7f1a26c93"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "tension_evaluation",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("reading_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("planet_name", sa.Text(), nullable=False),
        sa.Column(
            "parameter",
            sa.Enum(
                "mass",
                "radius",
                "period",
                name="tension_evaluation_parameter",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "awaiting_reference",
                "evaluated",
                "consistent_with_limit",
                "incompatible_with_limit",
                "closed_loop",
                name="tension_evaluation_status",
                native_enum=False,
                create_constraint=True,
                length=30,
            ),
            nullable=False,
        ),
        sa.Column("archive_planet_name", sa.Text(), nullable=True),
        sa.Column("reference_solution_key", sa.CHAR(length=64), nullable=True),
        sa.Column("reference_sigma", sa.Double(), nullable=True),
        sa.Column("own_solution_key", sa.CHAR(length=64), nullable=True),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("first_evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_tension_evaluation_item_id_items")
        ),
        sa.ForeignKeyConstraint(
            ["own_solution_key"],
            ["archive_solution.solution_key"],
            name=op.f("fk_tension_evaluation_own_solution_key_archive_solution"),
        ),
        sa.ForeignKeyConstraint(
            ["reading_id"], ["readings.id"], name=op.f("fk_tension_evaluation_reading_id_readings")
        ),
        sa.ForeignKeyConstraint(
            ["reference_solution_key"],
            ["archive_solution.solution_key"],
            name=op.f("fk_tension_evaluation_reference_solution_key_archive_solution"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tension_evaluation")),
    )
    op.create_index(
        "uq_tension_evaluation_reading_id_planet_name_parameter",
        "tension_evaluation",
        ["reading_id", "planet_name", "parameter"],
        unique=True,
    )
    op.create_index("ix_tension_evaluation_status", "tension_evaluation", ["status"])


def downgrade() -> None:
    """Downgrade schema.

    Si ya hay evaluaciones, revienta sin tocar nada: borrar la tabla
    destruiria el estado de las evaluaciones en espera. Un humano decide que
    hacer con esas filas (mismo criterio que `7c1e4a9b2d35`).
    """
    connection = op.get_bind()
    count = connection.execute(sa.text("SELECT count(*) FROM tension_evaluation")).scalar_one()
    if count:
        raise RuntimeError(
            f"No se puede bajar el esquema: hay {count} fila(s) en tension_evaluation. "
            "Bórralas o migra su contenido antes de hacer downgrade."
        )
    op.drop_index("ix_tension_evaluation_status", table_name="tension_evaluation")
    op.drop_index(
        "uq_tension_evaluation_reading_id_planet_name_parameter", table_name="tension_evaluation"
    )
    op.drop_table("tension_evaluation")
