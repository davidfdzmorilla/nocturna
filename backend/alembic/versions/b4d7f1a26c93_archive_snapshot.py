"""archive snapshot

Revision ID: b4d7f1a26c93
Revises: a79e3c5d8f12
Create Date: 2026-10-01 12:01:16.446110

T81: `archive_snapshot`, `archive_solution` y `archive_default_change`, el
historico del NASA Exoplanet Archive. Tablas nuevas, sin tocar las existentes.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4d7f1a26c93"
down_revision: str | Sequence[str] | None = "a79e3c5d8f12"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "archive_snapshot",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("taken_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "full",
                "incremental",
                name="archive_snapshot_kind",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("max_releasedate", sa.Date(), nullable=True),
        sa.Column("rows_total", sa.Integer(), nullable=False),
        sa.Column("defaults_total", sa.Integer(), nullable=False),
        sa.Column("duplicate_rows", sa.Integer(), nullable=False),
        sa.Column("payload_sha256", sa.CHAR(length=64), nullable=False),
        sa.Column("requests", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "defaults_total >= 0", name=op.f("ck_archive_snapshot_defaults_total_non_negative")
        ),
        sa.CheckConstraint(
            "duplicate_rows >= 0", name=op.f("ck_archive_snapshot_duplicate_rows_non_negative")
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name=op.f("ck_archive_snapshot_duration_ms_non_negative")
        ),
        sa.CheckConstraint("requests >= 0", name=op.f("ck_archive_snapshot_requests_non_negative")),
        sa.CheckConstraint(
            "rows_total >= 0", name=op.f("ck_archive_snapshot_rows_total_non_negative")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_archive_snapshot")),
    )
    op.create_index(
        "ix_archive_snapshot_taken_at",
        "archive_snapshot",
        [sa.literal_column("taken_at DESC")],
        unique=False,
    )
    op.create_table(
        "archive_default_change",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("pl_name", sa.Text(), nullable=False),
        sa.Column("old_solution_key", sa.CHAR(length=64), nullable=True),
        sa.Column("new_solution_key", sa.CHAR(length=64), nullable=False),
        sa.Column("snapshot_id", sa.UUID(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["archive_snapshot.id"],
            name=op.f("fk_archive_default_change_snapshot_id_archive_snapshot"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_archive_default_change")),
    )
    op.create_index(
        "ix_archive_default_change_snapshot_id",
        "archive_default_change",
        ["snapshot_id"],
        unique=False,
    )
    op.create_table(
        "archive_solution",
        sa.Column("solution_key", sa.CHAR(length=64), nullable=False),
        sa.Column("pl_name", sa.Text(), nullable=False),
        sa.Column("hostname", sa.Text(), nullable=True),
        sa.Column("pl_refname", sa.Text(), nullable=False),
        sa.Column("ref_key", sa.Text(), nullable=False),
        sa.Column("ref_text", sa.Text(), nullable=True),
        sa.Column("arxiv_id", sa.Text(), nullable=True),
        sa.Column("soltype", sa.Text(), nullable=False),
        sa.Column("releasedate", sa.Date(), nullable=False),
        sa.Column("pl_pubdate", sa.Text(), nullable=True),
        sa.Column("mass_value", sa.Double(), nullable=True),
        sa.Column("mass_err1", sa.Double(), nullable=True),
        sa.Column("mass_err2", sa.Double(), nullable=True),
        sa.Column("mass_lim", sa.SmallInteger(), nullable=True),
        sa.Column("radius_value", sa.Double(), nullable=True),
        sa.Column("radius_err1", sa.Double(), nullable=True),
        sa.Column("radius_err2", sa.Double(), nullable=True),
        sa.Column("radius_lim", sa.SmallInteger(), nullable=True),
        sa.Column("period_value", sa.Double(), nullable=True),
        sa.Column("period_err1", sa.Double(), nullable=True),
        sa.Column("period_err2", sa.Double(), nullable=True),
        sa.Column("period_lim", sa.SmallInteger(), nullable=True),
        sa.Column("pl_bmassprov", sa.Text(), nullable=True),
        sa.Column("st_rad_value", sa.Double(), nullable=True),
        sa.Column("st_rad_err1", sa.Double(), nullable=True),
        sa.Column("st_rad_err2", sa.Double(), nullable=True),
        sa.Column("st_mass_value", sa.Double(), nullable=True),
        sa.Column("st_mass_err1", sa.Double(), nullable=True),
        sa.Column("st_mass_err2", sa.Double(), nullable=True),
        sa.Column("discoverymethod", sa.Text(), nullable=True),
        sa.Column("ttv_flag", sa.Boolean(), nullable=True),
        sa.Column("pl_controv_flag", sa.Boolean(), nullable=True),
        sa.Column("is_default", sa.Boolean(), nullable=False),
        sa.Column("first_seen_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("last_seen_snapshot_id", sa.UUID(), nullable=False),
        sa.Column("is_default_current", sa.Boolean(), nullable=False),
        sa.Column("removed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["first_seen_snapshot_id"],
            ["archive_snapshot.id"],
            name=op.f("fk_archive_solution_first_seen_snapshot_id_archive_snapshot"),
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_snapshot_id"],
            ["archive_snapshot.id"],
            name=op.f("fk_archive_solution_last_seen_snapshot_id_archive_snapshot"),
        ),
        sa.PrimaryKeyConstraint("solution_key", name=op.f("pk_archive_solution")),
    )
    op.create_index("ix_archive_solution_pl_name", "archive_solution", ["pl_name"], unique=False)
    op.create_index(
        "ix_archive_solution_releasedate", "archive_solution", ["releasedate"], unique=False
    )
    op.create_index(
        "uq_archive_solution_pl_name_default_current",
        "archive_solution",
        ["pl_name"],
        unique=True,
        postgresql_where=sa.text("is_default_current"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Borra las tres tablas (y sus indices) sin comprobar filas: es un historico
    reconstruible con un snapshot completo, y nada mas del esquema depende de
    el. Orden inverso al de creacion por las FK a `archive_snapshot`.
    """
    op.drop_table("archive_solution")
    op.drop_table("archive_default_change")
    op.drop_table("archive_snapshot")
