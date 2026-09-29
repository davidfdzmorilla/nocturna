"""reading measurements jsonb column

Revision ID: 52ccb04d0f06
Revises: 950738867fb9
Create Date: 2026-09-29 09:36:10.932068

Columna nueva, nullable, sin backfill: `readings.measurements` (T71.c),
`JSONB` que guarda la tupla `Reading.measurements` -- `NULL` si esta lectura
no se hizo con el prompt de Reader v3 que extrae medidas estructuradas
(`reader-v2`, o un ítem fuera de las categorías con medidas de
`pipeline.toml`), `[]` si sí se hizo y no encontró ninguna medida utilizable
en el abstract. Ver el docstring de `Reading.measurements` en
`domain/entities.py` y el de `ReadingRow.measurements` en
`infrastructure/db/models.py`: la distinción `NULL`/`[]` depende de
`JSONB(none_as_null=True)`, que autogenerate ya detecta y reproduce aquí.

Filas ya persistidas antes de esta migración quedan con `measurements IS
NULL`: no se distingue retroactivamente si esas lecturas "no se buscaron"
o "se buscaron y no había nada", porque ninguna de las dos es cierta -- son
de antes de que existiera esta extracción. `NULL` es la lectura correcta
para ese pasado, la misma que usa `_measurements_to_json` para "no se
extrajo con este prompt".
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "52ccb04d0f06"
down_revision: str | Sequence[str] | None = "950738867fb9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "readings",
        sa.Column("measurements", postgresql.JSONB(none_as_null=True), nullable=True),
    )


def downgrade() -> None:
    """Downgrade schema.

    Borra la columna entera, incluidas las medidas de lecturas ya pagadas
    con una llamada real al Reader v3 -- no hay backfill posible desde
    aquí, así que un downgrade después de correr una noche real pierde ese
    dato sin vuelta atrás.
    """
    op.drop_column("readings", "measurements")
