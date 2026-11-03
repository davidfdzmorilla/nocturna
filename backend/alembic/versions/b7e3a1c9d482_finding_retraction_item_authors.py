"""finding retraction and item authors

Revision ID: b7e3a1c9d482
Revises: a4d9e1b7c352
Create Date: 2026-11-03 12:00:00.000000

T100: `items.authors` (autores del paper de arXiv; NULL = desconocido, sin
relleno de los anteriores) y retirada editorial de hallazgos
(`findings.retracted_at` / `retraction_note`) con tres CHECK.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b7e3a1c9d482"
down_revision: str | Sequence[str] | None = "a4d9e1b7c352"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOGETHER = "ck_findings_retraction_together"
_ONLY_PUBLISHED = "ck_findings_retraction_only_published"
_NOTE_NOT_BLANK = "ck_findings_retraction_note_not_blank"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("items", sa.Column("authors", sa.Text(), nullable=True))
    op.add_column("findings", sa.Column("retracted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("findings", sa.Column("retraction_note", sa.Text(), nullable=True))
    op.create_check_constraint(
        op.f(_TOGETHER), "findings", "(retracted_at IS NULL) = (retraction_note IS NULL)"
    )
    op.create_check_constraint(
        op.f(_ONLY_PUBLISHED), "findings", "retracted_at IS NULL OR published_at IS NOT NULL"
    )
    op.create_check_constraint(
        op.f(_NOTE_NOT_BLANK),
        "findings",
        "retraction_note IS NULL OR length(btrim(retraction_note)) > 0",
    )


def downgrade() -> None:
    """Downgrade schema.

    Si hay algun finding retirado, revienta sin tocar nada: borrar las columnas
    republicaria en silencio un hallazgo retirado (mismo criterio que
    `7c1e4a9b2d35`). Un humano decide que hacer con esas filas.

    Borra tambien `items.authors`: esos datos no se pueden reconstruir y solo
    se recuperan volviendo a cosechar los items.
    """
    connection = op.get_bind()
    count = connection.execute(
        sa.text("SELECT count(*) FROM findings WHERE retracted_at IS NOT NULL")
    ).scalar_one()
    if count:
        raise RuntimeError(
            f"No se puede bajar el esquema: hay {count} finding(s) retirado(s) "
            "(retracted_at no nulo). Bajar el esquema los republicaría. Borra "
            "esas filas o decide su destino antes de hacer downgrade."
        )
    op.drop_constraint(op.f(_NOTE_NOT_BLANK), "findings", type_="check")
    op.drop_constraint(op.f(_ONLY_PUBLISHED), "findings", type_="check")
    op.drop_constraint(op.f(_TOGETHER), "findings", type_="check")
    op.drop_column("findings", "retraction_note")
    op.drop_column("findings", "retracted_at")
    op.drop_column("items", "authors")
