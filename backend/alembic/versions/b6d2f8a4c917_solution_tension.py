"""solution_tension

Revision ID: b6d2f8a4c917
Revises: d8b1e4f7a203
Create Date: 2026-10-14 12:00:00.000000

T85: tabla `solution_tension` (una fila por alta del archivo y parametro, con su
referencia, incluidas las que no pasan los filtros) y tipo de finding
`solution_tension`: columnas `solution_tension` (JSONB) y `solution_tension_id`
(FK), un CHECK "si y solo si" por cada una, indice unico
`(solution_tension_id, type)` y CHECK de tipo ampliado. Sin backfill.
Downgrade: se niega si hay filas en `solution_tension`, findings de ese tipo
o items con source distinto de arxiv.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6d2f8a4c917"
down_revision: str | Sequence[str] | None = "d8b1e4f7a203"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Nombres cortos: la convencion de nombres añade el prefijo `ck_findings_`.
_TYPE_CHECK = "finding_type"
_PAYLOAD_CHECK = "solution_tension_iff_type"
_ID_CHECK = "solution_tension_id_iff_type"
_FK = "fk_findings_solution_tension_id_solution_tension"
_UNIQUE_INDEX = "uq_findings_solution_tension_id_type"

_OLD_TYPES = "'paper_explained', 'catalog_tension', 'primera_medida', 'confirmacion_independiente'"
_NEW_TYPES = _OLD_TYPES + ", 'solution_tension'"


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "solution_tension",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("new_solution_key", sa.CHAR(length=64), nullable=False),
        sa.Column("reference_solution_key", sa.CHAR(length=64), nullable=False),
        sa.Column("planet_name", sa.Text(), nullable=False),
        sa.Column(
            "parameter",
            sa.Enum(
                "mass",
                "radius",
                "period",
                name="solution_tension_parameter",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("sigma", sa.Double(), nullable=False),
        sa.Column(
            "skip_reason",
            sa.Enum(
                "not_published_confirmed",
                "same_reference",
                "old_paper",
                "below_threshold",
                "period_min_difference",
                "period_alias",
                "period_ttv",
                "no_reference",
                name="solution_tension_skip_reason",
                native_enum=False,
                create_constraint=True,
                length=32,
            ),
            nullable=True,
        ),
        sa.Column(
            "reference_role",
            sa.Enum(
                "default",
                "most_recent",
                name="solution_tension_reference_role",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("labels", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("detail", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("item_id", sa.UUID(), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["item_id"], ["items.id"], name=op.f("fk_solution_tension_item_id_items")
        ),
        sa.ForeignKeyConstraint(
            ["new_solution_key"],
            ["archive_solution.solution_key"],
            name=op.f("fk_solution_tension_new_solution_key_archive_solution"),
        ),
        sa.ForeignKeyConstraint(
            ["reference_solution_key"],
            ["archive_solution.solution_key"],
            name=op.f("fk_solution_tension_reference_solution_key_archive_solution"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_solution_tension")),
        sa.CheckConstraint(
            "item_id IS NULL OR skip_reason IS NULL",
            name=op.f("ck_solution_tension_item_only_if_eligible"),
        ),
    )
    op.create_index(
        "uq_solution_tension_new_solution_key_parameter",
        "solution_tension",
        ["new_solution_key", "parameter"],
        unique=True,
    )

    op.add_column(
        "findings",
        sa.Column("solution_tension", postgresql.JSONB(none_as_null=True), nullable=True),
    )
    op.add_column("findings", sa.Column("solution_tension_id", sa.UUID(), nullable=True))
    op.create_foreign_key(_FK, "findings", "solution_tension", ["solution_tension_id"], ["id"])
    op.drop_constraint(_TYPE_CHECK, "findings", type_="check")
    op.create_check_constraint(_TYPE_CHECK, "findings", f"type IN ({_NEW_TYPES})")
    op.create_check_constraint(
        _PAYLOAD_CHECK, "findings", "(type = 'solution_tension') = (solution_tension IS NOT NULL)"
    )
    op.create_check_constraint(
        _ID_CHECK, "findings", "(type = 'solution_tension') = (solution_tension_id IS NOT NULL)"
    )
    op.create_index(_UNIQUE_INDEX, "findings", ["solution_tension_id", "type"], unique=True)


def downgrade() -> None:
    """Downgrade schema.

    Si ya hay filas en `solution_tension` o findings `solution_tension`,
    revienta sin tocar nada: bajar el esquema destruiria su dato. Un humano
    decide que hacer con esas filas (mismo criterio que `7c1e4a9b2d35`).
    """
    connection = op.get_bind()
    findings = connection.execute(
        sa.text("SELECT count(*) FROM findings WHERE type = 'solution_tension'")
    ).scalar_one()
    rows = connection.execute(sa.text("SELECT count(*) FROM solution_tension")).scalar_one()
    # Sin la guarda de `next_unread` (source = 'arxiv'), el Reader leeria los
    # items del archivo: bajar el esquema con ellos presentes gastaria tokens.
    non_arxiv = connection.execute(
        sa.text("SELECT count(*) FROM items WHERE source <> 'arxiv'")
    ).scalar_one()
    if non_arxiv:
        raise RuntimeError(
            f"No se puede bajar el esquema: hay {non_arxiv} item(s) con source <> 'arxiv' "
            "y el next_unread anterior los entregaria al Reader. Bórralos antes de hacer "
            "downgrade."
        )
    if findings or rows:
        raise RuntimeError(
            f"No se puede bajar el esquema: hay {findings} finding(s) con "
            f"type='solution_tension' y {rows} fila(s) en solution_tension. "
            "Bórralos o migra su contenido antes de hacer downgrade."
        )
    op.drop_index(_UNIQUE_INDEX, table_name="findings")
    op.drop_constraint(_ID_CHECK, "findings", type_="check")
    op.drop_constraint(_PAYLOAD_CHECK, "findings", type_="check")
    op.drop_constraint(_TYPE_CHECK, "findings", type_="check")
    op.create_check_constraint(_TYPE_CHECK, "findings", f"type IN ({_OLD_TYPES})")
    op.drop_constraint(_FK, "findings", type_="foreignkey")
    op.drop_column("findings", "solution_tension_id")
    op.drop_column("findings", "solution_tension")
    op.drop_index("uq_solution_tension_new_solution_key_parameter", table_name="solution_tension")
    op.drop_table("solution_tension")
