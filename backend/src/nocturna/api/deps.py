"""Composition root de la API de lectura (T50 paso 3).

Igual que `cli.py` es el composition root del pipeline, este módulo es el
único de `api/` que ata `Settings`, el motor de SQLAlchemy y las
implementaciones concretas de repositorio a los endpoints de FastAPI. Los
routers dependen de los `Protocol` de `domain/repositories.py` a través de
`Depends(...)`, nunca construyen una sesión ni un repositorio por su
cuenta.

## Por qué no hay `unit_of_work` aquí

`application/unit_of_work.py` (`unit_of_work`, en `infrastructure/db/
session.py`) es la frontera transaccional de **escritura** del pipeline:
abre una sesión, hace `commit()` si todo va bien y `rollback()` si no. La
API es de solo lectura por diseño (`CLAUDE.md`, § Web fase 1) y no debe
poder confirmar nada -- ni por error, ni porque alguien añada un `session.
add(...)` a un endpoint sin darse cuenta de que eso persistiría. Por eso
`get_session` no usa `unit_of_work` y cierra siempre con `rollback()`,
nunca con `commit()`: aunque un repositorio de lectura no debería modificar
nada, esta función no depende de esa promesa para ser segura.

## Qué toma de `pipeline.toml`

Solo `window.timezone` (T84), vía `get_digest_timezone`, cacheada: define la
semana ISO del resumen semanal. Se lee la primera vez que se llama a una ruta
`/archive/*`, no al arrancar. Si el fichero falta o no valida, esas rutas
devuelven 500 por el manejador genérico; `/health` y `/findings` siguen
funcionando. El resto de `PipelineConfig` (presupuesto, ventana, modelos por
rol) es del pipeline nocturno y no incumbe a la API.
"""

from collections.abc import Callable, Generator
from functools import lru_cache
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import Depends
from sqlalchemy.orm import Session, sessionmaker

from nocturna.infrastructure.config import Settings, load_pipeline_config
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveDigestReader,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
)
from nocturna.infrastructure.db.session import create_db_engine, create_session_factory
from nocturna.infrastructure.exoplanet_archive.mappers import planet_overview_url


@lru_cache
def get_settings() -> Settings:
    """Ajustes de proceso, cacheados: una sola lectura de entorno por proceso."""
    return Settings()


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """Fábrica de sesiones, cacheada: un único motor de conexión por proceso."""
    settings = get_settings()
    engine = create_db_engine(settings)
    return create_session_factory(engine)


def get_session() -> Generator[Session, None, None]:
    """Sesión de solo lectura para la duración de una petición HTTP.

    Nunca llama a `commit()`. Cierra siempre con `rollback()`, ocurra o no
    una excepción: no hay ninguna escritura legítima que confirmar en esta
    capa, así que no existe un camino "feliz" que deba persistir cambios.
    """
    factory = get_session_factory()
    session = factory()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


#: Alias de tipo para la sesión de lectura, con el estilo `Annotated` que
#: recomienda hoy la documentación de FastAPI: `Depends(...)` vive en los
#: metadatos del tipo, no como valor por defecto del parámetro, así que ruff
#: (B008) no lo trata como una llamada mutable arriesgada en cada petición
#: -- a diferencia de `session: Session = Depends(get_session)`, que sí
#: necesitaba la excepción de `[tool.ruff.lint.flake8-bugbear]` en
#: `pyproject.toml`. Ver ese fichero: la excepción se retira en este mismo
#: cambio porque, tras este refactor, ya no hace falta en todo `api/`.
_SessionDep = Annotated[Session, Depends(get_session)]


def get_findings_repository(session: _SessionDep) -> SqlAlchemyFindingRepository:
    return SqlAlchemyFindingRepository(session)


def get_items_repository(session: _SessionDep) -> SqlAlchemyItemRepository:
    return SqlAlchemyItemRepository(session)


def get_archive_digest_reader(session: _SessionDep) -> SqlAlchemyArchiveDigestReader:
    return SqlAlchemyArchiveDigestReader(session)


@lru_cache
def get_digest_timezone() -> ZoneInfo:
    """`window.timezone` de `pipeline.toml` (T84): define la semana ISO del resumen.
    Es lo único que la API toma de esa configuración."""
    return ZoneInfo(load_pipeline_config().window.timezone)


def get_planet_overview_url() -> Callable[[str], str]:
    """Enlace a la ficha del planeta, inyectado como en T89 (D14)."""
    return planet_overview_url
