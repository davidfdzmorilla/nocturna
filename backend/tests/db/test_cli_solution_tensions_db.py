"""`nocturna solution-tensions`, su sección en `run-night --dry-run` y el rechazo de
ítems del archivo en `run-item` (T85). Contra `nocturna_test`. Sin red ni Claude."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from factories import make_item, seed_archive_snapshot
from fakes.clock import FakeClock
from test_domain_solution_tension import MSINI, NEW, OLD

from nocturna import cli
from nocturna.domain.entities import ITEM_SOURCE_EXOPLANET_ARCHIVE
from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository
from nocturna.infrastructure.db.session import unit_of_work

NOW = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "system_clock_from_config", lambda config: FakeClock(NOW))


def _count(factory, table: str) -> int:
    with factory() as session:
        return session.execute(sa.text(f"select count(*) from {table}")).scalar_one()


def test_solution_tensions_dry_run_no_escribe(db_session_factory, capsys):
    seed_archive_snapshot(db_session_factory, [NEW, OLD, MSINI])

    assert cli.main(["solution-tensions", "--dry-run"]) == 0

    out = capsys.readouterr().out
    assert "[dry-run: no se escribió nada]" in out
    assert "elegibles 2" in out
    assert _count(db_session_factory, "solution_tension") == 0
    assert _count(db_session_factory, "items") == 0


def test_solution_tensions_escribe_y_es_idempotente(db_session_factory, capsys):
    seed_archive_snapshot(db_session_factory, [NEW, OLD, MSINI])

    assert cli.main(["solution-tensions"]) == 0
    assert _count(db_session_factory, "solution_tension") == 2
    assert _count(db_session_factory, "items") == 1
    with db_session_factory() as session:
        source = session.execute(sa.text("select source from items")).scalar_one()
        linked = session.execute(
            sa.text("select count(*) from solution_tension where item_id is not null")
        ).scalar_one()
    assert source == ITEM_SOURCE_EXOPLANET_ARCHIVE
    assert linked == 2

    capsys.readouterr()
    assert cli.main(["solution-tensions"]) == 0
    assert "ya calculados: 2" in capsys.readouterr().out
    assert _count(db_session_factory, "solution_tension") == 2
    assert _count(db_session_factory, "items") == 1


def test_el_item_del_archivo_no_lo_lee_next_unread(db_session_factory):
    seed_archive_snapshot(db_session_factory, [NEW, OLD])
    assert cli.main(["solution-tensions"]) == 0
    with unit_of_work(db_session_factory) as session:
        assert SqlAlchemyItemRepository(session).next_unread(10) == []


@pytest.mark.parametrize("argv_tail", [[], ["--reader", "v3", "--force"]])
def test_run_item_rechaza_un_item_del_archivo_sin_runs_ni_llamadas(
    db_session_factory, monkeypatch, capsys, argv_tail
):
    def _boom(*args: object, **kwargs: object) -> object:
        raise AssertionError("no debe construirse ningún AgentRunner")

    monkeypatch.setattr(cli, "_build_read_item", _boom)
    monkeypatch.setattr(cli, "_agent_work_factory", _boom)
    item = make_item(source=ITEM_SOURCE_EXOPLANET_ARCHIVE, external_id="a" * 64)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyItemRepository(session).add_many([item])

    code = cli.main(["run-item", str(item.id), *argv_tail])

    assert code == 10
    assert "ningún agente lee ítems del archivo" in capsys.readouterr().err
    assert _count(db_session_factory, "agent_calls") == 0
    assert _count(db_session_factory, "runs") == 0
