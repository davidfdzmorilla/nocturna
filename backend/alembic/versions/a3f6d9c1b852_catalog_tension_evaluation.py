"""catalog_tension con tension_evaluation_id

Revision ID: a3f6d9c1b852
Revises: c9e4b2a7d135
Create Date: 2026-10-08 12:00:00.000000

T76 (ADR 0026): un `catalog_tension` redactado por el writer nace de una
`TensionEvaluation`. El CHECK `tension_evaluation_id_iff_type` se recrea para
que `tension_evaluation_id` sea obligatorio si y solo si `type` es
`primera_medida`, `confirmacion_independiente` o `catalog_tension`. Sin
backfill: si hay `catalog_tension` sin evaluacion, el upgrade falla sin tocar
nada.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3f6d9c1b852"
down_revision: str | Sequence[str] | None = "c9e4b2a7d135"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Nombre corto: la convencion de nombres anade el prefijo `ck_findings_`.
_CHECK = "tension_evaluation_id_iff_type"
_OLD_TYPES = "('primera_medida', 'confirmacion_independiente')"
_NEW_TYPES = "('primera_medida', 'confirmacion_independiente', 'catalog_tension')"


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()
    count = connection.execute(
        sa.text(
            "SELECT count(*) FROM findings "
            "WHERE type = 'catalog_tension' AND tension_evaluation_id IS NULL"
        )
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"No se puede subir el esquema: hay {count} finding(s) con "
            "type='catalog_tension' sin tension_evaluation_id. Asigna su "
            "evaluacion o bórralos antes de hacer upgrade."
        )
    op.drop_constraint(_CHECK, "findings", type_="check")
    op.create_check_constraint(
        _CHECK, "findings", f"(type IN {_NEW_TYPES}) = (tension_evaluation_id IS NOT NULL)"
    )


def downgrade() -> None:
    """Downgrade schema.

    Si ya hay findings `catalog_tension`, revienta sin tocar nada: el CHECK
    anterior los rechazaria (llevan `tension_evaluation_id`). Un humano decide
    que hacer con esas filas (mismo criterio que `7c1e4a9b2d35`).
    """
    connection = op.get_bind()
    count = connection.execute(
        sa.text("SELECT count(*) FROM findings WHERE type = 'catalog_tension'")
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"No se puede bajar el esquema: hay {count} finding(s) con "
            "type='catalog_tension'. Bórralos o migra su contenido antes de "
            "hacer downgrade."
        )
    op.drop_constraint(_CHECK, "findings", type_="check")
    op.create_check_constraint(
        _CHECK, "findings", f"(type IN {_OLD_TYPES}) = (tension_evaluation_id IS NOT NULL)"
    )
