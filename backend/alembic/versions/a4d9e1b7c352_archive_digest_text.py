"""archive digest text

Revision ID: a4d9e1b7c352
Revises: e3a7c5b9d214
Create Date: 2026-11-02 12:00:00.000000

T84.r: tabla `archive_digest_text`, una fila por semana ISO con el texto del
redactor del resumen semanal (o `failed` si dos salidas fueron inválidas).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4d9e1b7c352"
down_revision: str | Sequence[str] | None = "e3a7c5b9d214"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "archive_digest_text",
        sa.Column("week", sa.String(length=10), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("input_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("prompt_version", sa.String(length=50), nullable=False),
        sa.Column("run_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "week ~ '^[0-9]{4}-W[0-9]{2}$'",
            name=op.f("ck_archive_digest_text_week_format"),
        ),
        sa.CheckConstraint(
            "status IN ('written', 'failed')",
            name=op.f("ck_archive_digest_text_status"),
        ),
        sa.CheckConstraint(
            "(status = 'written' AND title IS NOT NULL AND summary IS NOT NULL) "
            "OR (status = 'failed' AND title IS NULL AND summary IS NULL)",
            name=op.f("ck_archive_digest_text_text_by_status"),
        ),
        sa.ForeignKeyConstraint(
            ["run_id"], ["runs.id"], name=op.f("fk_archive_digest_text_run_id_runs")
        ),
        sa.PrimaryKeyConstraint("week", name=op.f("pk_archive_digest_text")),
    )


def downgrade() -> None:
    """Downgrade schema.

    Falla ruidosamente si hay filas: son textos ya generados con tokens y no
    se tiran sin que un humano lo decida.
    """
    connection = op.get_bind()
    count = connection.execute(sa.text("SELECT count(*) FROM archive_digest_text")).scalar_one()
    if count:
        raise RuntimeError(
            f"no se puede bajar la migración {revision}: 'archive_digest_text' tiene "
            f"{count} fila(s), textos del redactor ya pagados en tokens. Expórtalas o "
            "bórralas a mano si de verdad quieres perderlas; esta migración no lo decide."
        )
    op.drop_table("archive_digest_text")
