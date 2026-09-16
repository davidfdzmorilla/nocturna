"""initial schema

Revision ID: a318e7fd86a9
Revises:
Create Date: 2026-09-16 13:20:45.779961

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a318e7fd86a9"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "items",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("abstract", sa.Text(), nullable=False),
        sa.Column("categories", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "new",
                "read",
                "discarded",
                "published",
                name="item_status",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_items")),
        sa.UniqueConstraint("source", "external_id", name="uq_items_source_external_id"),
    )
    op.create_table(
        "runs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum(
                "running",
                "completed",
                "partial",
                "failed",
                "killed",
                name="run_status",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("budget_tokens", sa.Integer(), nullable=False),
        sa.Column("tokens_used", sa.Integer(), nullable=False),
        sa.Column("items_fetched", sa.Integer(), nullable=False),
        sa.Column("items_read", sa.Integer(), nullable=False),
        sa.Column("findings_published", sa.Integer(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "(status = 'running') = (finished_at IS NULL)",
            name=op.f("ck_runs_running_iff_finished_at_null"),
        ),
        sa.CheckConstraint("budget_tokens > 0", name=op.f("ck_runs_budget_tokens_positive")),
        sa.CheckConstraint(
            "findings_published >= 0", name=op.f("ck_runs_findings_published_non_negative")
        ),
        sa.CheckConstraint("items_fetched >= 0", name=op.f("ck_runs_items_fetched_non_negative")),
        sa.CheckConstraint("items_read >= 0", name=op.f("ck_runs_items_read_non_negative")),
        sa.CheckConstraint("tokens_used >= 0", name=op.f("ck_runs_tokens_used_non_negative")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_runs")),
    )
    op.create_index(
        "uq_runs_status_running",
        "runs",
        ["status"],
        unique=True,
        postgresql_where=sa.text("status = 'running'"),
    )
    op.create_table(
        "agent_calls",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column(
            "agent",
            sa.Enum(
                "reader",
                "popularizer",
                "editor",
                name="agent_role",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "ok",
                "invalid_output",
                "error",
                "timeout",
                name="agent_call_status",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_ms >= 0", name=op.f("ck_agent_calls_duration_ms_non_negative")
        ),
        sa.CheckConstraint("tokens_in >= 0", name=op.f("ck_agent_calls_tokens_in_non_negative")),
        sa.CheckConstraint("tokens_out >= 0", name=op.f("ck_agent_calls_tokens_out_non_negative")),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_agent_calls_item_id_items")
        ),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_agent_calls_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_calls")),
    )
    op.create_index("ix_agent_calls_run_id_agent", "agent_calls", ["run_id", "agent"], unique=False)
    op.create_table(
        "findings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("run_id", sa.UUID(), nullable=False),
        sa.Column(
            "type",
            sa.Enum(
                "paper_explained",
                name="finding_type",
                native_enum=False,
                length=20,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("level_curious", sa.Text(), nullable=False),
        sa.Column("level_amateur", sa.Text(), nullable=False),
        sa.Column("level_technical", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Double(), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(confidence IS NULL) = (published_at IS NULL)",
            name=op.f("ck_findings_confidence_published_at_together"),
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR confidence BETWEEN 0 AND 1",
            name=op.f("ck_findings_confidence_range"),
        ),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], name=op.f("fk_findings_item_id_items")),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], name=op.f("fk_findings_run_id_runs")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_findings")),
    )
    op.create_index(
        "ix_findings_published_at",
        "findings",
        [sa.text("published_at DESC")],
        unique=False,
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )
    op.create_table(
        "readings",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("objects", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("claims", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("interest_score", sa.SmallInteger(), nullable=False),
        sa.Column("tokens_in", sa.Integer(), nullable=False),
        sa.Column("tokens_out", sa.Integer(), nullable=False),
        sa.Column("model", sa.String(length=100), nullable=False),
        sa.CheckConstraint(
            "interest_score BETWEEN 1 AND 5", name=op.f("ck_readings_interest_score_range")
        ),
        sa.CheckConstraint("tokens_in >= 0", name=op.f("ck_readings_tokens_in_non_negative")),
        sa.CheckConstraint("tokens_out >= 0", name=op.f("ck_readings_tokens_out_non_negative")),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], name=op.f("fk_readings_item_id_items")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_readings")),
        sa.UniqueConstraint("item_id", name="uq_readings_item_id"),
    )


def downgrade() -> None:
    """Downgrade schema.

    Orden inverso al de `upgrade()`, tablas hijas antes que padres: primero
    los índices y tablas que referencian por FK a `items`/`runs`
    (`readings`, `findings`, `agent_calls`), después `runs` (ya no
    referenciada) y por último `items`.
    """
    op.drop_table("readings")
    op.drop_index(
        "ix_findings_published_at",
        table_name="findings",
        postgresql_where=sa.text("published_at IS NOT NULL"),
    )
    op.drop_table("findings")
    op.drop_index("ix_agent_calls_run_id_agent", table_name="agent_calls")
    op.drop_table("agent_calls")
    op.drop_index(
        "uq_runs_status_running",
        table_name="runs",
        postgresql_where=sa.text("status = 'running'"),
    )
    op.drop_table("runs")
    op.drop_table("items")
