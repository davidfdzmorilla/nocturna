"""T109: la sesión de la API rechaza escrituras aunque el rol sea propietario."""

import pytest
import sqlalchemy as sa
from conftest import run_alembic_upgrade
from sqlalchemy.exc import DBAPIError

from nocturna.api import deps
from nocturna.infrastructure.config import Settings


def test_get_session_factory_rejects_writes_and_reads(
    scratch_database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_alembic_upgrade(scratch_database_url)
    monkeypatch.setattr(deps, "get_settings", lambda: Settings(database_url=scratch_database_url))
    deps.get_session_factory.cache_clear()
    try:
        factory = deps.get_session_factory()
        with factory() as session:
            assert session.execute(sa.text("SELECT count(*) FROM items")).scalar_one() == 0
            with pytest.raises(DBAPIError) as exc:
                session.execute(
                    sa.text(
                        "INSERT INTO items (source, external_id, title, abstract) "
                        "VALUES ('arxiv', 'x', 't', 'a')"
                    )
                )
            assert "ReadOnlySqlTransaction" in type(exc.value.orig).__name__
            session.rollback()
            assert session.execute(sa.text("SELECT count(*) FROM items")).scalar_one() == 0
    finally:
        deps.get_session_factory.cache_clear()
