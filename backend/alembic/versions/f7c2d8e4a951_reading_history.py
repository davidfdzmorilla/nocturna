"""reading history

Revision ID: f7c2d8e4a951
Revises: e5b3a9d1c746
Create Date: 2026-10-05 18:00:00.000000

T82: historia de `readings`. `prompt_version` (VARCHAR(50), NULL: fila historica
sin version conocida) y `superseded_at` (TIMESTAMPTZ, NULL = lectura vigente).
`uq_readings_item_id` (unica por item) se sustituye por el indice unico parcial
`uq_readings_item_id_current` sobre (item_id) WHERE superseded_at IS NULL: como
mucho una lectura vigente por item, impuesto por la base. Relleno de
`prompt_version` desde `agent_calls` solo cuando no es ambiguo.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f7c2d8e4a951"
down_revision: str | Sequence[str] | None = "e5b3a9d1c746"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_UNIQUE = "uq_readings_item_id"
_CURRENT_INDEX = "uq_readings_item_id_current"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("readings", sa.Column("prompt_version", sa.String(length=50), nullable=True))
    op.add_column("readings", sa.Column("superseded_at", sa.DateTime(timezone=True), nullable=True))
    op.drop_constraint(_OLD_UNIQUE, "readings", type_="unique")
    op.create_index(
        _CURRENT_INDEX,
        "readings",
        ["item_id"],
        unique=True,
        postgresql_where=sa.text("superseded_at IS NULL"),
    )
    # Relleno: solo si todas las llamadas 'ok' del Reader del item llevan la
    # misma prompt_version (una o varias). Con versiones distintas, alguna
    # NULL o ninguna llamada (ambiguo) la lectura queda en NULL.
    op.execute(
        """
        UPDATE readings AS r
        SET prompt_version = c.prompt_version
        FROM (
            SELECT item_id, min(prompt_version) AS prompt_version
            FROM agent_calls
            WHERE agent = 'reader' AND status = 'ok' AND item_id IS NOT NULL
            GROUP BY item_id
            HAVING count(DISTINCT prompt_version) = 1
               AND count(prompt_version) = count(*)
        ) AS c
        WHERE c.item_id = r.item_id AND c.prompt_version IS NOT NULL
        """
    )


def downgrade() -> None:
    """Downgrade schema.

    Si hay lecturas sustituidas revienta sin tocar nada: volver a la unicidad
    por item obligaria a borrar historia. Un humano decide que hacer con ellas
    (mismo criterio que `e5b3a9d1c746`). `prompt_version` se pierde al bajar
    (dato derivado y solo informativo).
    """
    connection = op.get_bind()
    count = connection.execute(
        sa.text("SELECT count(*) FROM readings WHERE superseded_at IS NOT NULL")
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"No se puede bajar el esquema: hay {count} lectura(s) sustituida(s) en "
            "readings (superseded_at no es NULL). Bórralas o consolídalas antes de "
            "hacer downgrade."
        )
    op.drop_index(_CURRENT_INDEX, table_name="readings")
    op.create_unique_constraint(_OLD_UNIQUE, "readings", ["item_id"])
    op.drop_column("readings", "superseded_at")
    op.drop_column("readings", "prompt_version")
