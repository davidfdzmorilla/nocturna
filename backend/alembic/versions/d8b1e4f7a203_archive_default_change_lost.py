"""archive_default_change admite solucion nueva ausente

Revision ID: d8b1e4f7a203
Revises: a3f6d9c1b852
Create Date: 2026-10-09 12:00:00.000000

T84: un cambio de solucion por defecto puede ser la perdida de la solucion
(`new_solution_key` NULL). El CHECK exige al menos una de las dos claves.
`new_solution_key` no tiene FK. Downgrade: falla sin tocar nada si hay filas
con `new_solution_key` NULL.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d8b1e4f7a203"
down_revision: str | Sequence[str] | None = "a3f6d9c1b852"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Nombre corto: la convencion de nombres anade el prefijo `ck_archive_default_change_`.
_CHECK = "old_or_new"
_TABLE = "archive_default_change"


def upgrade() -> None:
    """Upgrade schema."""
    op.alter_column(_TABLE, "new_solution_key", existing_type=sa.CHAR(length=64), nullable=True)
    op.create_check_constraint(
        _CHECK, _TABLE, "num_nonnulls(old_solution_key, new_solution_key) >= 1"
    )


def downgrade() -> None:
    """Downgrade schema."""
    lost = (
        op.get_bind()
        .execute(sa.text(f"SELECT count(*) FROM {_TABLE} WHERE new_solution_key IS NULL"))
        .scalar_one()
    )
    if lost:
        raise RuntimeError(
            f"No se puede hacer downgrade: hay {lost} fila(s) en {_TABLE} con "
            "new_solution_key NULL (solucion perdida). Eliminalas o rellenalas a mano antes."
        )
    op.drop_constraint(_CHECK, _TABLE, type_="check")
    op.alter_column(_TABLE, "new_solution_key", existing_type=sa.CHAR(length=64), nullable=False)
