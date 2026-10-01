"""Recalcula `items.exoplanet_match` de TODOS los ítems (T79).

## Por qué existe

La migración `a79e3c5d8f12` añade `items.exoplanet_match` con `false` para
las filas existentes y no hace backfill. Este script aplica el
`ExoplanetFilter` vigente (`[exoplanet_filter]` de `config/pipeline.toml`,
construido con `nocturna.cli.exoplanet_filter_from_config`, igual que la
ingesta) a título + abstract de cada ítem y escribe la marca. Se ejecuta una
vez tras migrar y de nuevo cada vez que el autor cambie la lista.

Idempotente: una segunda ejecución con la misma lista no cambia nada. Solo
toca la columna `exoplanet_match`; no cambia `status` ni nada más.

## Cuándo ejecutarlo

FUERA de la ventana nocturna (00:00-04:45 hora local): el pipeline lee
`next_unread` y la marca decide la variante del Reader, y una noche en curso
no debe ver la marca cambiar a medias. El script no comprueba la hora.

## Sin Claude

No importa `claude_agent_sdk` ni llama a agentes: solo lee/escribe
PostgreSQL (`Settings.database_url`, es decir `NOCTURNA_DATABASE_URL`).

## Uso

    cd backend
    uv run python scripts/backfill_exoplanet_match.py             # escribe
    uv run python scripts/backfill_exoplanet_match.py --dry-run   # solo informa

Al terminar imprime recuentos (total, que casan, cambiados) y los primeros
`limits.max_items_per_night` ítems de `next_unread` con su marca, la
variante prevista del Reader y el título recortado.
"""

from __future__ import annotations

import argparse
import sys

import sqlalchemy as sa

from nocturna.cli import exoplanet_filter_from_config
from nocturna.infrastructure.config import Settings, load_pipeline_config
from nocturna.infrastructure.db.models import ItemRow
from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository
from nocturna.infrastructure.db.session import create_db_engine, create_session_factory

_TITLE_WIDTH = 70


def _trim(title: str) -> str:
    title = " ".join(title.split())
    return title if len(title) <= _TITLE_WIDTH else title[: _TITLE_WIDTH - 1] + "…"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Recalcula items.exoplanet_match (T79).")
    parser.add_argument(
        "--dry-run", action="store_true", help="calcula y muestra recuentos sin escribir"
    )
    args = parser.parse_args(argv)

    settings = Settings()
    config = load_pipeline_config(settings.config_path)
    exo_filter = exoplanet_filter_from_config(config)
    measures_categories = frozenset(config.reader.measurement_categories)
    limit = config.limits.max_items_per_night

    engine = create_db_engine(settings)
    session_factory = create_session_factory(engine)

    with session_factory() as session:
        total = matching = changed = 0
        rows = session.execute(sa.select(ItemRow).order_by(ItemRow.external_id)).scalars()
        for row in rows:
            match = exo_filter.matches(row.title, row.abstract)
            total += 1
            matching += match
            if row.exoplanet_match != match:
                row.exoplanet_match = match
                changed += 1
        session.flush()

        print(f"total: {total}")
        print(f"casan: {matching}")
        print(f"cambiados: {changed}{' (dry-run, sin escribir)' if args.dry_run else ''}")

        # Misma consulta que usa la noche; ve las marcas recién flusheadas.
        print(f"\nprimeros {limit} de next_unread:")
        for item in SqlAlchemyItemRepository(session).next_unread(limit):
            variant = (
                "v3"
                if item.exoplanet_match and not measures_categories.isdisjoint(item.categories)
                else "v2"
            )
            print(
                f"  {item.external_id:<16} match={int(item.exoplanet_match)} "
                f"{variant} {_trim(item.title)}"
            )

        if args.dry_run:
            session.rollback()
        else:
            session.commit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
