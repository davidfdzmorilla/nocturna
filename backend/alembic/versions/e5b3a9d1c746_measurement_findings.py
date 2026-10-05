"""measurement findings

Revision ID: e5b3a9d1c746
Revises: d2a8c5e7f104
Create Date: 2026-10-05 12:00:00.000000

T89: tipos `primera_medida` y `confirmacion_independiente` en `findings`.
`type` pasa a VARCHAR(32) (`confirmacion_independiente` mide 26); el CHECK de
tipo se recrea con los cuatro valores; columnas JSONB `first_measurement` e
`independent_confirmation` (nullable, sin backfill); `tension_evaluation_id`
(FK a `tension_evaluation.id`, nullable); un CHECK "si y solo si" por payload y
otro para la evaluacion; indice unico `(tension_evaluation_id, type)`.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5b3a9d1c746"
down_revision: str | Sequence[str] | None = "d2a8c5e7f104"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Nombres cortos: la convencion de nombres añade el prefijo `ck_findings_`.
_TYPE_CHECK = "finding_type"
_FIRST_CHECK = "first_measurement_iff_type"
_CONFIRMATION_CHECK = "independent_confirmation_iff_type"
_EVALUATION_CHECK = "tension_evaluation_id_iff_type"
_FK = "fk_findings_tension_evaluation_id_tension_evaluation"
_UNIQUE_INDEX = "uq_findings_tension_evaluation_id_type"

_NEW_TYPES = "('primera_medida', 'confirmacion_independiente')"


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(
        "findings", "type", existing_type=sa.String(length=20), type_=sa.String(length=32)
    )
    op.add_column(
        "findings",
        sa.Column("first_measurement", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.add_column(
        "findings",
        sa.Column("independent_confirmation", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.add_column("findings", sa.Column("tension_evaluation_id", sa.UUID(), nullable=True))
    op.create_foreign_key(_FK, "findings", "tension_evaluation", ["tension_evaluation_id"], ["id"])
    op.drop_constraint(_TYPE_CHECK, "findings", type_="check")
    op.create_check_constraint(
        _TYPE_CHECK,
        "findings",
        "type IN ('paper_explained', 'catalog_tension', 'primera_medida', "
        "'confirmacion_independiente')",
    )
    op.create_check_constraint(
        _FIRST_CHECK,
        "findings",
        "(type = 'primera_medida') = (first_measurement IS NOT NULL)",
    )
    op.create_check_constraint(
        _CONFIRMATION_CHECK,
        "findings",
        "(type = 'confirmacion_independiente') = (independent_confirmation IS NOT NULL)",
    )
    op.create_check_constraint(
        _EVALUATION_CHECK,
        "findings",
        f"(type IN {_NEW_TYPES}) = (tension_evaluation_id IS NOT NULL)",
    )
    op.create_index(_UNIQUE_INDEX, "findings", ["tension_evaluation_id", "type"], unique=True)


def downgrade() -> None:
    """Downgrade schema.

    Si ya hay findings de los tipos nuevos, revienta sin tocar nada: bajar el
    esquema borraria sus columnas con su dato y el tipo dejaria de ser valido.
    Un humano decide que hacer con esas filas (mismo criterio que
    `7c1e4a9b2d35`).
    """
    connection = op.get_bind()
    count = connection.execute(
        sa.text(f"SELECT count(*) FROM findings WHERE type IN {_NEW_TYPES}")
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"No se puede bajar el esquema: hay {count} finding(s) con "
            "type='primera_medida' o 'confirmacion_independiente'. Bórralos o "
            "migra su contenido antes de hacer downgrade."
        )
    op.drop_index(_UNIQUE_INDEX, table_name="findings")
    op.drop_constraint(_EVALUATION_CHECK, "findings", type_="check")
    op.drop_constraint(_CONFIRMATION_CHECK, "findings", type_="check")
    op.drop_constraint(_FIRST_CHECK, "findings", type_="check")
    op.drop_constraint(_TYPE_CHECK, "findings", type_="check")
    # La columna vuelve a VARCHAR(20) antes de recrear el CHECK: asi los literales
    # se castean a VARCHAR(20) como en `7c1e4a9b2d35` y el esquema queda identico.
    op.alter_column(
        "findings", "type", existing_type=sa.String(length=32), type_=sa.String(length=20)
    )
    op.create_check_constraint(
        _TYPE_CHECK, "findings", "type IN ('paper_explained', 'catalog_tension')"
    )
    op.drop_constraint(_FK, "findings", type_="foreignkey")
    op.drop_column("findings", "tension_evaluation_id")
    op.drop_column("findings", "independent_confirmation")
    op.drop_column("findings", "first_measurement")
