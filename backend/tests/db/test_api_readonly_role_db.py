"""T109: el rol `nocturna_ro` creado por `deploy/postgres/api-readonly-role.sql`.

Contra el PostgreSQL de compose, pero solo en una base scratch propia y un rol de nombre
unico (`nocturna_ro_t109_<hex>`): los roles son del cluster, asi que el teardown borra la
base y despues el rol. Nunca toca `nocturna` ni `nocturna_test`. Nombres en espanol.
"""

from __future__ import annotations

import secrets
from collections.abc import Generator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
import sqlalchemy as sa
from conftest import (
    _admin_database_url,
    _skip_if_postgres_unreachable,
    _with_database,
    create_database,
    drop_database,
    run_alembic_upgrade,
)
from factories import make_finding, make_item, make_run
from fastapi.testclient import TestClient
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session

from nocturna.api import deps
from nocturna.api.app import create_app
from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.config import Settings
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)

SQL_FILE = Path(__file__).resolve().parents[3] / "deploy" / "postgres" / "api-readonly-role.sql"
PASSWORD = "T109RoPassword01"


@dataclass
class RoleEnv:
    owner_url: URL
    owner_role: str
    role: str
    db: str
    finding_id: str

    def role_url(self, password: str = PASSWORD) -> URL:
        return self.owner_url.set(username=self.role, password=password)

    def role_engine(self) -> sa.Engine:
        return sa.create_engine(self.role_url(), isolation_level="AUTOCOMMIT")

    def owner_engine(self) -> sa.Engine:
        return sa.create_engine(self.owner_url, isolation_level="AUTOCOMMIT")


def _libpq_dsn(url: URL) -> dict[str, object]:
    return {
        "host": url.host,
        "port": url.port,
        "user": url.username,
        "password": url.password,
        "dbname": url.database,
    }


def apply_script(env_url: URL, role: str, owner: str, password: str) -> None:
    """Reproduce `10-api-readonly-role.sh`: parametros por GUC de sesion (PGOPTIONS)."""
    options = (
        f"-c nocturna.ro_role={role} -c nocturna.owner_role={owner} "
        f"-c nocturna.ro_password={password}"
    )
    with psycopg.connect(**_libpq_dsn(env_url), options=options, autocommit=True) as conn:
        conn.execute(SQL_FILE.read_text())


def _seed_finding(url: URL) -> str:
    engine = sa.create_engine(url)
    try:
        with Session(engine) as s:
            item = make_item(external_id=f"2601.{uuid4().hex[:5]}")
            run = make_run()
            run.finish(RunStatus.COMPLETED, at=run.started_at)
            SqlAlchemyItemRepository(s).add_many([item])
            SqlAlchemyRunRepository(s).add(run)
            s.flush()
            finding = make_finding(item_id=item.id, run_id=run.id)
            finding.publish(confidence=0.8, at=datetime(2026, 3, 2, 12, tzinfo=UTC))
            SqlAlchemyFindingRepository(s).add(finding)
            s.commit()
            return str(finding.id)
    finally:
        engine.dispose()


@pytest.fixture
def env() -> Generator[RoleEnv, None, None]:
    _skip_if_postgres_unreachable()
    suffix = secrets.token_hex(4)
    db = f"nocturna_t109_{suffix}"
    role = f"nocturna_ro_t109_{suffix}"
    admin = make_url(_admin_database_url())
    owner_url = make_url(_with_database(db))
    create_database(db)
    try:
        run_alembic_upgrade(owner_url.render_as_string(hide_password=False))
        finding_id = _seed_finding(owner_url)
        apply_script(owner_url, role, str(admin.username), PASSWORD)
        yield RoleEnv(owner_url, str(admin.username), role, db, finding_id)
    finally:
        drop_database(db)
        eng = sa.create_engine(admin, isolation_level="AUTOCOMMIT")
        try:
            with eng.connect() as c:
                c.execute(sa.text(f'DROP ROLE IF EXISTS "{role}"'))
        finally:
            eng.dispose()


def test_script_es_idempotente_y_reaplicar_cambia_la_contrasena(env: RoleEnv) -> None:
    apply_script(env.owner_url, env.role, env.owner_role, PASSWORD)
    apply_script(env.owner_url, env.role, env.owner_role, "OtraPassword02")
    engine = sa.create_engine(env.role_url("OtraPassword02"))
    with engine.connect() as c:
        assert c.execute(sa.text("SELECT current_user")).scalar_one() == env.role
    engine.dispose()
    bad = sa.create_engine(env.role_url(PASSWORD))
    with pytest.raises(sa.exc.OperationalError), bad.connect():
        pass
    bad.dispose()


def test_script_con_contrasena_vacia_falla_y_no_crea_el_rol(env: RoleEnv) -> None:
    other = f"{env.role}_x"
    with pytest.raises(psycopg.errors.RaiseException):
        apply_script(env.owner_url, other, env.owner_role, "")
    with env.owner_engine().connect() as c:
        n = c.execute(sa.text("SELECT count(*) FROM pg_roles WHERE rolname = :r"), {"r": other})
        assert n.scalar_one() == 0


def test_rol_lee_todas_las_tablas_del_esquema_public(env: RoleEnv) -> None:
    with env.owner_engine().connect() as c:
        tables = [
            r[0]
            for r in c.execute(
                sa.text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            )
        ]
    assert len(tables) > 5
    with env.role_engine().connect() as c:
        for t in tables:
            c.execute(sa.text(f'SELECT 1 FROM public."{t}" LIMIT 1'))


def test_tabla_creada_por_el_propietario_despues_del_script_es_legible(env: RoleEnv) -> None:
    with env.owner_engine().connect() as c:
        c.execute(sa.text("CREATE TABLE public.t109_posterior (id int)"))
        c.execute(sa.text("INSERT INTO public.t109_posterior VALUES (1)"))
    with env.role_engine().connect() as c:
        assert c.execute(sa.text("SELECT id FROM public.t109_posterior")).scalar_one() == 1


def test_consultas_de_la_api_responden_con_una_sesion_del_rol(
    env: RoleEnv, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = Settings(database_url=env.role_url().render_as_string(hide_password=False))
    monkeypatch.setattr(deps, "get_settings", lambda: settings)
    deps.get_session_factory.cache_clear()
    try:
        client = TestClient(create_app(settings))
        feed = client.get("/findings")
        assert feed.status_code == 200, feed.text
        assert [f["id"] for f in feed.json()["items"]] == [env.finding_id]
        one = client.get(f"/findings/{env.finding_id}")
        assert one.status_code == 200, one.text
        weeks = client.get("/archive/weeks")
        assert weeks.status_code == 200, weeks.text
    finally:
        deps.get_session_factory.cache_clear()


_WRITES = [
    "INSERT INTO items (source, external_id, title, abstract) VALUES ('arxiv', 'x', 't', 'a')",
    "UPDATE items SET title = 'x'",
    "DELETE FROM items",
    "TRUNCATE items CASCADE",
    "CREATE TABLE public.t109_x (id int)",
    "CREATE TEMP TABLE t109_tmp (id int)",
]


@pytest.mark.parametrize("sql", _WRITES, ids=lambda s: s.split(" (")[0][:28])
def test_con_read_only_desactivado_el_rol_sigue_sin_privilegios_de_escritura(
    env: RoleEnv, sql: str
) -> None:
    # AUTOCOMMIT: el SET va en sentencia separada y cada sentencia abre su transaccion,
    # ya con `default_transaction_read_only = off`; solo los privilegios pueden frenarla.
    with env.role_engine().connect() as c:
        c.execute(sa.text("SET default_transaction_read_only = off"))
        assert c.execute(sa.text("SHOW default_transaction_read_only")).scalar_one() == "off"
        with pytest.raises(sa.exc.ProgrammingError) as exc:
            c.execute(sa.text(sql))
        assert isinstance(exc.value.orig, psycopg.errors.InsufficientPrivilege)


def test_sin_set_el_rol_nace_en_solo_lectura_y_rechaza_insert(env: RoleEnv) -> None:
    with env.role_engine().connect() as c:
        assert c.execute(sa.text("SHOW default_transaction_read_only")).scalar_one() == "on"
        with pytest.raises(sa.exc.DBAPIError) as exc:
            c.execute(sa.text(_WRITES[0]))
        assert exc.value.orig is not None
        assert isinstance(
            exc.value.orig,
            psycopg.errors.ReadOnlySqlTransaction | psycopg.errors.InsufficientPrivilege,
        )


def test_rol_sin_superusuario_ni_createdb_ni_createrole(env: RoleEnv) -> None:
    with env.owner_engine().connect() as c:
        row = c.execute(
            sa.text(
                "SELECT rolsuper, rolcreatedb, rolcreaterole, rolreplication, rolbypassrls "
                "FROM pg_roles WHERE rolname = :r"
            ),
            {"r": env.role},
        ).one()
    assert tuple(row) == (False, False, False, False, False)


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO public.t109_posterior VALUES (2)",
        "UPDATE public.t109_posterior SET id = 3",
        "DELETE FROM public.t109_posterior",
        "TRUNCATE public.t109_posterior",
    ],
)
def test_tabla_posterior_tampoco_admite_escrituras_del_rol(env: RoleEnv, sql: str) -> None:
    with env.owner_engine().connect() as c:
        c.execute(sa.text("CREATE TABLE public.t109_posterior (id int)"))
    with env.role_engine().connect() as c:
        c.execute(sa.text("SET default_transaction_read_only = off"))
        with pytest.raises(sa.exc.ProgrammingError) as exc:
            c.execute(sa.text(sql))
        assert isinstance(exc.value.orig, psycopg.errors.InsufficientPrivilege)
