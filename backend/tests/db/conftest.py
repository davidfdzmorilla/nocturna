"""Fixtures de base de datos para `tests/db/`.

Todo lo que cuelga de este directorio se marca automáticamente con `db`
(ver `pytest_collection_modifyitems`) y corre contra `nocturna_test`, una
base aparte de `nocturna` en el mismo PostgreSQL de `docker compose`. El
esquema de `nocturna_test` se crea con `alembic upgrade head`, nunca con
`Base.metadata.create_all`: si los tests crearan el esquema a partir de los
modelos, una migración que divergiera de `models.py` podría pasar
desapercibida, y la migración es la que corre de verdad en producción.

Dos formas de sesión, según lo que necesite el test:

- `db_session`: conexión + transacción externa + `Session` en modo
  `join_transaction_mode="create_savepoint"`, con rollback de la
  transacción externa al final del test. Un `commit()` dentro del test (el
  que hace, por ejemplo, `unit_of_work`) solo libera el savepoint interno;
  la transacción externa —y su rollback final— sigue intacta. Ningún test
  con esta fixture deja estado para el siguiente.
- `db_session_factory`: fábrica de sesiones ligada directamente al motor,
  sin transacción envolvente, para los tests que necesitan un `commit()`
  real (atomicidad de `unit_of_work`, persistencia tras "reinicio"). El
  teardown vacía las tablas con `TRUNCATE ... RESTART IDENTITY CASCADE`.

Solo se salta la sesión de tests si PostgreSQL no responde
(`OperationalError`, con `connect_timeout` corto): un fallo de migración o
de consulta no es un entorno ausente, es un test roto, y debe verse en
rojo.
"""

from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4

import pytest
import sqlalchemy as sa
from alembic.config import Config as AlembicConfig
from sqlalchemy import Engine
from sqlalchemy.engine import make_url
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from alembic import command
from nocturna.infrastructure.config import Settings
from nocturna.infrastructure.db.session import create_db_engine, create_session_factory

BACKEND_DIR = Path(__file__).resolve().parents[2]
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"
TEST_DB_NAME = "nocturna_test"

# Orden hijas -> padres: mismo orden que el `downgrade()` de la migración
# inicial, para que TRUNCATE no tropiece con las FK.
_TABLES_IN_DEPENDENCY_ORDER = ("readings", "findings", "agent_calls", "runs", "items")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Marca con `db` todo test cuyo fichero cuelgue de `tests/db/`."""
    db_dir = str(Path(__file__).resolve().parent)
    for item in items:
        if str(item.fspath).startswith(db_dir):
            item.add_marker(pytest.mark.db)


def _with_database(name: str) -> str:
    # `str(URL)` enmascara la contraseña con "***" desde SQLAlchemy 2.x;
    # `render_as_string(hide_password=False)` es la única forma de recuperar
    # una URL de conexión válida tras `.set(database=...)`.
    return (
        make_url(Settings().database_url).set(database=name).render_as_string(hide_password=False)
    )


def _test_database_url() -> str:
    return _with_database(TEST_DB_NAME)


def _admin_database_url() -> str:
    # "postgres" es la base de mantenimiento: comprobar/crear "nocturna_test"
    # no debe depender de que la base de aplicación "nocturna" exista.
    return _with_database("postgres")


@contextmanager
def _database_url_env(url: str) -> Generator[None, None, None]:
    """Fuerza `NOCTURNA_DATABASE_URL` mientras corre Alembic contra `nocturna_test`.

    `alembic/env.py` resuelve la URL con `Settings().database_url`, que lee
    `NOCTURNA_DATABASE_URL` del entorno; es el mecanismo que el propio
    `env.py` documenta para este caso.
    """
    previous = os.environ.get("NOCTURNA_DATABASE_URL")
    os.environ["NOCTURNA_DATABASE_URL"] = url
    try:
        yield
    finally:
        if previous is None:
            os.environ.pop("NOCTURNA_DATABASE_URL", None)
        else:
            os.environ["NOCTURNA_DATABASE_URL"] = previous


def _alembic_config() -> AlembicConfig:
    return AlembicConfig(str(ALEMBIC_INI))


def run_alembic_upgrade(database_url: str, revision: str = "head") -> None:
    with _database_url_env(database_url):
        command.upgrade(_alembic_config(), revision)


def run_alembic_downgrade(database_url: str, revision: str = "base") -> None:
    with _database_url_env(database_url):
        command.downgrade(_alembic_config(), revision)


def _skip_if_postgres_unreachable() -> None:
    """Único punto de la suite que convierte un fallo en skip, y solo si es de conexión."""
    admin_url = _admin_database_url()
    try:
        probe_engine = sa.create_engine(admin_url, connect_args={"connect_timeout": 2})
        with probe_engine.connect() as connection:
            connection.execute(sa.text("SELECT 1"))
        probe_engine.dispose()
    except OperationalError as exc:
        pytest.skip(
            "no se puede conectar al PostgreSQL de compose en el puerto 5433; "
            f"levanta `docker compose up -d`. Detalle original: {exc}"
        )


def _ensure_test_database_exists() -> None:
    admin_url = _admin_database_url()
    admin_engine = sa.create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                sa.text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": TEST_DB_NAME},
            ).scalar()
            if not exists:
                # No hay `CREATE DATABASE IF NOT EXISTS` en PostgreSQL; de ahí
                # la comprobación explícita en `pg_database` antes de crearla.
                connection.execute(sa.text(f'CREATE DATABASE "{TEST_DB_NAME}"'))
    finally:
        admin_engine.dispose()


def create_database(name: str) -> None:
    """Crea (recreando si ya existiera) una base efímera para migraciones destructivas."""
    admin_engine = sa.create_engine(_admin_database_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
            connection.execute(sa.text(f'CREATE DATABASE "{name}"'))
    finally:
        admin_engine.dispose()


def drop_database(name: str) -> None:
    admin_engine = sa.create_engine(_admin_database_url(), isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            connection.execute(sa.text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    finally:
        admin_engine.dispose()


@pytest.fixture
def scratch_database_url() -> Generator[str, None, None]:
    """URL de una base efímera, propia de un test, para `upgrade`/`downgrade` destructivos.

    Aparte de `nocturna_test` a propósito: `test_migration.py` necesita
    dejar el esquema vacío (`downgrade base`) sin afectar a los demás
    tests de `tests/db/`, que asumen `nocturna_test` siempre al día en
    `head`.
    """
    _skip_if_postgres_unreachable()
    name = f"nocturna_test_migration_{uuid4().hex[:8]}"
    create_database(name)
    url = _with_database(name)
    try:
        yield url
    finally:
        drop_database(name)


@pytest.fixture(scope="session")
def test_database_url() -> str:
    """URL de `nocturna_test`, con el esquema al día vía `alembic upgrade head`.

    Solo salta la sesión si PostgreSQL no responde. Un fallo de la propia
    migración se propaga tal cual: es un defecto real, no un entorno
    ausente.
    """
    _skip_if_postgres_unreachable()
    _ensure_test_database_exists()
    url = _test_database_url()
    run_alembic_upgrade(url, "head")
    return url


@pytest.fixture(scope="session")
def test_engine(test_database_url: str) -> Generator[Engine, None, None]:
    """Motor de `nocturna_test`, construido con `create_db_engine`.

    Pasa por la factoría de producción (no por `sa.create_engine` a pelo)
    para que `connect_args={"options": "-c timezone=UTC"}` esté realmente en
    vigor: es la garantía que
    `test_datetimes_rehidratados_desde_postgres_son_aware_y_el_mismo_instante`
    dice comprobar.
    """
    engine = create_db_engine(Settings(database_url=test_database_url))
    yield engine
    engine.dispose()


@pytest.fixture
def db_session(test_engine: Engine) -> Generator[Session, None, None]:
    """Sesión con rollback garantizado al final del test.

    Ver docstring del módulo: `commit()` dentro del test no cierra la
    transacción externa, así que ningún test dejado aquí deja estado al
    siguiente.
    """
    connection = test_engine.connect()
    outer_transaction = connection.begin()
    # `create_session_factory` liga la sesión al motor con `bind=engine`; aquí
    # se sobreescribe `bind` a la conexión abierta y se añade
    # `join_transaction_mode="create_savepoint"` en la llamada, que
    # `sessionmaker.__call__` fusiona con el resto de kwargs (`autoflush=True`
    # incluido) fijados en `create_session_factory`. Así el test de
    # aislamiento pasa por la fábrica real de producción en vez de
    # reconstruir sus propios argumentos.
    session_factory = create_session_factory(test_engine)
    session = session_factory(bind=connection, join_transaction_mode="create_savepoint")
    try:
        yield session
    finally:
        session.close()
        outer_transaction.rollback()
        connection.close()


@pytest.fixture
def db_session_factory(test_engine: Engine) -> Generator[sessionmaker[Session], None, None]:
    """Fábrica de sesiones con `commit()` real, para atomicidad y "reinicio".

    Cada sesión que produce esta fábrica está ligada directamente al motor
    (no a una conexión/transacción compartida), así que un `commit()` en
    una sesión es visible de verdad desde otra sesión nueva, y un
    `rollback()` deshace de verdad lo que aún no se confirmó. El teardown
    vacía las cinco tablas para que el siguiente test no herede filas.

    Usa `create_session_factory`, la misma fábrica de producción, para que
    `test_una_llamada_anadida_sin_commit_ya_se_cuenta_en_la_misma_sesion`
    ejercite de verdad el `autoflush=True` que ese módulo documenta como
    deliberado, no un valor por defecto sin cubrir.
    """
    factory = create_session_factory(test_engine)
    try:
        yield factory
    finally:
        with test_engine.begin() as connection:
            tables = ", ".join(_TABLES_IN_DEPENDENCY_ORDER)
            connection.execute(sa.text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
