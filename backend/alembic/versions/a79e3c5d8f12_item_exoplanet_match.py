"""item exoplanet_match

Revision ID: a79e3c5d8f12
Revises: 7c1e4a9b2d35
Create Date: 2026-10-01 12:00:00.000000

T79: `items.exoplanet_match` (BOOLEAN NOT NULL, server_default false), marca
de la ingesta que decide la variante del Reader. Sin backfill aquí: los
ítems existentes quedan en false y `scripts/backfill_exoplanet_match.py`
los recalcula con la lista vigente de `pipeline.toml`.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a79e3c5d8f12"
down_revision: str | Sequence[str] | None = "7c1e4a9b2d35"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "items",
        sa.Column("exoplanet_match", sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("items", "exoplanet_match")
