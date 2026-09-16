"""Motor, fábrica de sesión y unidad de trabajo de SQLAlchemy.

Este módulo es el **único sitio del proyecto donde se llama a
`Session.commit()` o `Session.rollback()`**. Los repositorios
(`repositories.py`) nunca lo hacen: si un repositorio confirmara su propia
transacción, un `AgentCall` y la actualización de `Run.tokens_used` que
tienen que viajar juntos (ver `CLAUDE.md`, control de gasto) podrían acabar
en transacciones distintas, y un fallo a mitad de camino dejaría el
acumulado de gasto sin el `AgentCall` que ya se cobró.

Nada de motor global creado al importar este módulo: `create_db_engine`
recibe `Settings` por argumento, igual que el resto de la infraestructura no
lee configuración global por su cuenta.
"""

from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker

from nocturna.infrastructure.config import Settings


def create_db_engine(settings: Settings) -> Engine:
    """Crea el motor de conexión a PostgreSQL.

    `connect_args={"options": "-c timezone=UTC"}` fija la zona horaria de la
    sesión de PostgreSQL a UTC en la conexión, no solo en el driver: así la
    rehidratación de timestamps es determinista sin importar la zona
    horaria configurada en la máquina donde corra el pipeline.
    """
    return create_engine(
        settings.database_url,
        connect_args={"options": "-c timezone=UTC"},
    )


def create_session_factory(engine: Engine) -> sessionmaker[Session]:
    """Fábrica de sesiones.

    `autoflush=True` es deliberado, no el valor por defecto sin pensar: un
    `AgentCall` añadido a la sesión (con `session.add`, sin `commit` todavía)
    debe contarse ya en el `SELECT SUM(...)` que ejecuta
    `AgentCallRepository.tokens_used_for_run` dentro de la misma unidad de
    trabajo. Con `autoflush=False`, esa suma podría no ver la llamada recién
    añadida y `BudgetGuard` autorizaría una llamada siguiente ignorando el
    gasto que ya está en camino.
    """
    return sessionmaker(bind=engine, autoflush=True, expire_on_commit=True)


@contextmanager
def unit_of_work(session_factory: sessionmaker[Session]) -> Generator[Session, None, None]:
    """Unidad de trabajo: una sesión, `commit()` si todo va bien, `rollback()` si no.

    Abre la sesión, la cede a quien la use (típicamente para construir los
    repositorios de esta transacción), confirma al salir sin excepción,
    deshace ante cualquier excepción, y cierra siempre. Es el único lugar
    del proyecto que decide el límite de una transacción.
    """
    session = session_factory()
    try:
        yield session
    except Exception:
        session.rollback()
        raise
    else:
        session.commit()
    finally:
        session.close()
