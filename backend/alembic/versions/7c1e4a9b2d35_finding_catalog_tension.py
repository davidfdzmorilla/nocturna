"""finding catalog_tension

Revision ID: 7c1e4a9b2d35
Revises: 52ccb04d0f06
Create Date: 2026-09-30 12:00:00.000000

T72 (ADR 0012): `findings.catalog_tension` (JSONB, nullable, sin backfill;
`NULL` para los `paper_explained` existentes), el nuevo valor de
`FindingType` `catalog_tension` y el CHECK `catalog_tension_iff_type`
(`type = 'catalog_tension'` si y solo si la columna no es `NULL`).
El CHECK de tipo se recrea con los dos valores.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7c1e4a9b2d35"
down_revision: str | Sequence[str] | None = "52ccb04d0f06"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Nombres cortos: la convención de nombres añade el prefijo `ck_findings_`.
_TYPE_CHECK = "finding_type"
_IFF_CHECK = "catalog_tension_iff_type"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "findings",
        sa.Column("catalog_tension", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.drop_constraint(_TYPE_CHECK, "findings", type_="check")
    op.create_check_constraint(
        _TYPE_CHECK, "findings", "type IN ('paper_explained', 'catalog_tension')"
    )
    op.create_check_constraint(
        _IFF_CHECK,
        "findings",
        "(type = 'catalog_tension') = (catalog_tension IS NOT NULL)",
    )


def downgrade() -> None:
    """Downgrade schema.

    Si ya hay findings `catalog_tension`, revienta sin tocar nada: bajar el
    esquema borraría la columna con su dato y el tipo dejaría de ser válido.
    Un humano decide qué hacer con esas filas (mismo criterio que
    `950738867fb9`).
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
    op.drop_constraint(_IFF_CHECK, "findings", type_="check")
    op.drop_constraint(_TYPE_CHECK, "findings", type_="check")
    op.create_check_constraint(_TYPE_CHECK, "findings", "type IN ('paper_explained')")
    op.drop_column("findings", "catalog_tension")
