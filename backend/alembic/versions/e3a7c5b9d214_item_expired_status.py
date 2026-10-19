"""item expired status

Revision ID: e3a7c5b9d214
Revises: b6d2f8a4c917
Create Date: 2026-10-17 10:00:00.000000

T94: `ItemStatus.EXPIRED`, sexto valor de `items.status`. Como en
`950738867fb9` (que añadió `failed`), el enum no es nativo de PostgreSQL
(`native_enum=False`): es `VARCHAR(20)` más el `CHECK` nombrado
`ck_items_item_status`. Hay que `DROP` el CHECK viejo y crear uno nuevo con la
lista ampliada. Autogenerate no detecta el cambio de contenido de un CHECK,
así que está escrito a mano.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e3a7c5b9d214"
down_revision: str | Sequence[str] | None = "b6d2f8a4c917"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ITEM_STATUSES = ("new", "read", "discarded", "published", "failed")
_NEW_ITEM_STATUSES = (*_OLD_ITEM_STATUSES, "expired")
_ITEM_STATUS_CHECK_NAME = "ck_items_item_status"


def upgrade() -> None:
    """Upgrade schema."""
    op.drop_constraint(op.f(_ITEM_STATUS_CHECK_NAME), "items", type_="check")
    op.create_check_constraint(
        op.f(_ITEM_STATUS_CHECK_NAME),
        "items",
        sa.column("status").in_(_NEW_ITEM_STATUSES),
    )


def downgrade() -> None:
    """Downgrade schema.

    Si hay filas `status = 'expired'` falla ruidosamente: un ítem caducado es
    un hecho de dominio (salió de la cola sin leerse) y no hay un estado
    anterior equivalente al que reescribirlo sin falsificarlo (por ejemplo,
    `new` lo devolvería a la cola y gastaría presupuesto en él). Que un humano
    decida qué hacer con esas filas.
    """
    connection = op.get_bind()
    expired_count = connection.execute(
        sa.text("SELECT count(*) FROM items WHERE status = 'expired'")
    ).scalar_one()
    if expired_count:
        raise RuntimeError(
            f"no se puede bajar la migración {revision}: hay {expired_count} fila(s) en "
            "'items' con status = 'expired', un valor que no existe en el CHECK anterior "
            f"({_ITEM_STATUS_CHECK_NAME}). Decide manualmente qué hacer con esas filas "
            "(por ejemplo, moverlas a 'discarded') y vuelve a intentarlo; esta migración "
            "no lo decide por ti."
        )

    op.drop_constraint(op.f(_ITEM_STATUS_CHECK_NAME), "items", type_="check")
    op.create_check_constraint(
        op.f(_ITEM_STATUS_CHECK_NAME),
        "items",
        sa.column("status").in_(_OLD_ITEM_STATUSES),
    )
