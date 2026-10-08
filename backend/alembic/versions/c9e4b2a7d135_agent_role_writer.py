"""agent role writer

Revision ID: c9e4b2a7d135
Revises: f7c2d8e4a951
Create Date: 2026-10-08 10:00:00.000000

T75: cuarto valor del enum `agent_calls.agent` (`AgentRole.WRITER`). Igual que
en la migración 950738867fb9, el enum no es nativo de PostgreSQL
(`native_enum=False`): es `VARCHAR(20)` más un `CHECK` nombrado
`ck_agent_calls_agent_role`, así que se hace `DROP` y `CREATE` del `CHECK`.
Autogenerate no diferencia el contenido de un `CheckConstraint`, por lo que
esta migración está escrita a mano.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c9e4b2a7d135"
down_revision: str | Sequence[str] | None = "f7c2d8e4a951"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_AGENT_ROLES = ("reader", "popularizer", "editor")
_NEW_AGENT_ROLES = (*_OLD_AGENT_ROLES, "writer")
_AGENT_ROLE_CHECK_NAME = "ck_agent_calls_agent_role"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(_AGENT_ROLE_CHECK_NAME), "agent_calls", type_="check")
    op.create_check_constraint(
        op.f(_AGENT_ROLE_CHECK_NAME),
        "agent_calls",
        sa.column("agent").in_(_NEW_AGENT_ROLES),
    )


def downgrade() -> None:
    """Downgrade schema.

    Si hay filas con `agent = 'writer'` falla ruidosamente sin tocar nada: son
    registros de gasto reales y no hay rol equivalente al que reescribirlos sin
    falsificar la contabilidad de tokens (mismo criterio que 950738867fb9).
    """
    connection = op.get_bind()
    writer_count = connection.execute(
        sa.text("SELECT count(*) FROM agent_calls WHERE agent = 'writer'")
    ).scalar_one()
    if writer_count:
        raise RuntimeError(
            f"no se puede bajar la migración {revision}: hay {writer_count} fila(s) en "
            "'agent_calls' con agent = 'writer', un valor que no existe en el CHECK anterior "
            f"({_AGENT_ROLE_CHECK_NAME}). Decide manualmente qué hacer con esas filas y "
            "vuelve a intentarlo; esta migración no lo decide por ti."
        )

    op.drop_constraint(op.f(_AGENT_ROLE_CHECK_NAME), "agent_calls", type_="check")
    op.create_check_constraint(
        op.f(_AGENT_ROLE_CHECK_NAME),
        "agent_calls",
        sa.column("agent").in_(_OLD_AGENT_ROLES),
    )
