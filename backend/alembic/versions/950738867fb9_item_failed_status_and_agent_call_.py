"""item failed status and agent call prompt version

Revision ID: 950738867fb9
Revises: a318e7fd86a9
Create Date: 2026-09-17 09:41:12.318568

Dos cambios de dominio (T41, paso 4), sin relación funcional entre sí pero
agrupados en una sola migración porque ambos vinieron con el mismo cambio de
entidades:

1. `ItemStatus.FAILED`: quinto valor del enum de `items.status`. El enum
   **no** es un tipo nativo de PostgreSQL (`native_enum=False` en
   `_str_enum`, ver `models.py`): está materializado como `VARCHAR(20)` más
   un `CHECK` nombrado `ck_items_item_status` que enumera los valores
   permitidos. Añadir un valor no es un `ALTER TYPE ... ADD VALUE` (eso solo
   existe para tipos `ENUM` nativos); aquí hay que `DROP` el `CHECK` viejo y
   `CREATE` uno nuevo con la lista ampliada.
2. `agent_calls.prompt_version`: columna nueva, `VARCHAR(50)` nullable, sin
   índice. Autogenerate sí la detecta (columna simple); el cambio del
   `CHECK` de `items` no lo detecta autogenerate porque no diferencia el
   contenido de un `CheckConstraint` de texto libre entre dos metadatas, así
   que esa parte de esta migración está escrita a mano.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "950738867fb9"
down_revision: str | Sequence[str] | None = "a318e7fd86a9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD_ITEM_STATUSES = ("new", "read", "discarded", "published")
_NEW_ITEM_STATUSES = (*_OLD_ITEM_STATUSES, "failed")
_ITEM_STATUS_CHECK_NAME = "ck_items_item_status"


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("agent_calls", sa.Column("prompt_version", sa.String(length=50), nullable=True))

    op.drop_constraint(op.f(_ITEM_STATUS_CHECK_NAME), "items", type_="check")
    op.create_check_constraint(
        op.f(_ITEM_STATUS_CHECK_NAME),
        "items",
        sa.column("status").in_(_NEW_ITEM_STATUSES),
    )


def downgrade() -> None:
    """Downgrade schema.

    Decisión sobre las filas `status = 'failed'`: **fallar ruidosamente**,
    no migrarlas en silencio a otro estado. Un ítem `failed` es un hecho de
    dominio (el Reader agotó los reintentos de JSON inválido contra él); no
    hay un estado "equivalente" al que reescribirlo sin falsificar ese
    hecho, y un `downgrade` que decidiera por su cuenta convertir `failed`
    en, por ejemplo, `new` reabriría en silencio un ítem que el pipeline
    dio por perdido, gastando de nuevo presupuesto contra él la noche
    siguiente sin que nadie lo decidiera. Es exactamente el tipo de
    conversión silenciosa que este proyecto rechaza en el dominio
    (`entities.py`: "no hay idempotencia silenciosa") y que aquí, en un
    `downgrade` que corre sin supervisión a las 3 de la mañana, sería peor
    todavía: revienta con un mensaje claro y deja que un humano decida qué
    hacer con esas filas antes de bajar el esquema.
    """
    connection = op.get_bind()
    failed_count = connection.execute(
        sa.text("SELECT count(*) FROM items WHERE status = 'failed'")
    ).scalar_one()
    if failed_count:
        raise RuntimeError(
            f"no se puede bajar la migración {revision}: hay {failed_count} fila(s) en "
            "'items' con status = 'failed', un valor que no existe en el CHECK anterior "
            f"({_ITEM_STATUS_CHECK_NAME}). Decide manualmente qué hacer con esas filas "
            "(por ejemplo, moverlas a 'new' o 'discarded' si de verdad hace falta "
            "downgradear) y vuelve a intentarlo; esta migración no lo decide por ti."
        )

    op.drop_constraint(op.f(_ITEM_STATUS_CHECK_NAME), "items", type_="check")
    op.create_check_constraint(
        op.f(_ITEM_STATUS_CHECK_NAME),
        "items",
        sa.column("status").in_(_OLD_ITEM_STATUSES),
    )

    op.drop_column("agent_calls", "prompt_version")
