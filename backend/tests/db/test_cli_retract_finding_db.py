"""`nocturna retract-finding` contra PostgreSQL (T100). Sin LLM, sin red."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from factories import make_finding, make_item, make_run
from fakes.clock import FakeClock

from nocturna import cli
from nocturna.cli import main
from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    assert test_database_url.rsplit("/", 1)[-1] == "nocturna_test"
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "system_clock_from_config", lambda _config: FakeClock(NOW))


def _seed(factory, *, published: bool):
    with unit_of_work(factory) as session:
        item = make_item()
        run = make_run()
        run.finish(RunStatus.COMPLETED, at=run.started_at)
        SqlAlchemyItemRepository(session).add_many([item])
        SqlAlchemyRunRepository(session).add(run)
        session.flush()
        finding = make_finding(item_id=item.id, run_id=run.id, title="Hallazgo")
        if published:
            finding.publish(confidence=0.8, at=PUBLISHED)
        SqlAlchemyFindingRepository(session).add(finding)
        return finding.id


def _stored(factory, finding_id):
    with factory() as session:
        return SqlAlchemyFindingRepository(session).get(finding_id)


def test_retira_un_publicado(db_session_factory, capsys) -> None:
    finding_id = _seed(db_session_factory, published=True)

    code = main(["retract-finding", str(finding_id), "--note", "Era una masa mínima."])

    assert code == 0
    assert "retirado" in capsys.readouterr().out
    stored = _stored(db_session_factory, finding_id)
    assert stored.retracted_at == NOW and stored.retraction_note == "Era una masa mínima."


def test_dry_run_no_escribe(db_session_factory, capsys) -> None:
    finding_id = _seed(db_session_factory, published=True)

    code = main(["retract-finding", str(finding_id), "--note", "nota", "--dry-run"])

    assert code == 0
    assert "[dry-run]" in capsys.readouterr().out
    assert _stored(db_session_factory, finding_id).retracted_at is None


def test_inexistente_da_codigo_1(db_session_factory, capsys) -> None:
    code = main(["retract-finding", str(uuid4()), "--note", "nota"])

    assert code == 1
    assert "FindingNotFound" in capsys.readouterr().err


def test_no_publicado_da_codigo_1_y_no_escribe(db_session_factory, capsys) -> None:
    finding_id = _seed(db_session_factory, published=False)

    code = main(["retract-finding", str(finding_id), "--note", "nota"])

    assert code == 1
    assert "InvalidTransition" in capsys.readouterr().err
    assert _stored(db_session_factory, finding_id).retracted_at is None


def test_ya_retirado_da_codigo_1(db_session_factory, capsys) -> None:
    finding_id = _seed(db_session_factory, published=True)
    assert main(["retract-finding", str(finding_id), "--note", "primera"]) == 0
    capsys.readouterr()

    code = main(["retract-finding", str(finding_id), "--note", "segunda"])

    assert code == 1
    assert _stored(db_session_factory, finding_id).retraction_note == "primera"


def test_la_nota_es_obligatoria() -> None:
    with pytest.raises(SystemExit) as exc:
        main(["retract-finding", str(uuid4())])
    assert exc.value.code == 2
