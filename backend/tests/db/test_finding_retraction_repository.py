"""Retirada de findings y `items.authors` en los repositorios (T100)."""

from __future__ import annotations

from datetime import UTC, datetime

from factories import make_finding, make_item, make_run

from nocturna.domain.entities import FindingType
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)

_PUBLISHED = datetime(2026, 3, 2, tzinfo=UTC)
_RETRACTED = datetime(2026, 3, 9, tzinfo=UTC)


def _seed(db_session):
    item = make_item()
    run = make_run()
    SqlAlchemyItemRepository(db_session).add_many([item])
    SqlAlchemyRunRepository(db_session).add(run)
    db_session.flush()
    return item, run


def _published(item, run, **overrides):
    finding = make_finding(item_id=item.id, run_id=run.id, **overrides)
    finding.publish(confidence=0.9, at=_PUBLISHED)
    return finding


def test_save_retraction_persiste_solo_la_retirada(db_session):
    findings = SqlAlchemyFindingRepository(db_session)
    item, run = _seed(db_session)
    finding = _published(item, run)
    findings.add(finding)
    db_session.flush()

    finding.retract(_RETRACTED, "Retirado por los autores.")
    findings.save_retraction(finding)
    db_session.flush()
    db_session.expire_all()

    stored = findings.get_published(finding.id)
    assert stored is not None
    assert stored.retracted_at == _RETRACTED
    assert stored.retraction_note == "Retirado por los autores."
    assert stored.published_at == _PUBLISHED
    assert stored.confidence == 0.9


def test_save_retraction_de_finding_inexistente_falla(db_session):
    import pytest

    item, run = _seed(db_session)
    finding = _published(item, run)
    finding.retract(_RETRACTED, "n")
    with pytest.raises(LookupError):
        SqlAlchemyFindingRepository(db_session).save_retraction(finding)


def test_published_page_y_count_excluyen_retirados(db_session):
    findings = SqlAlchemyFindingRepository(db_session)
    item, run = _seed(db_session)
    kept = _published(item, run, title="visible")
    gone = _published(item, run, title="retirado")
    findings.add(kept)
    findings.add(gone)
    db_session.flush()
    gone.retract(_RETRACTED, "nota")
    findings.save_retraction(gone)
    db_session.flush()

    assert [f.id for f in findings.published_page(10, 0)] == [kept.id]
    assert findings.count_published() == 1
    kind = FindingType.PAPER_EXPLAINED
    assert [f.id for f in findings.published_page(10, 0, finding_type=kind)] == [kept.id]
    assert findings.count_published(finding_type=kind) == 1


def test_get_published_devuelve_el_retirado_con_su_nota(db_session):
    findings = SqlAlchemyFindingRepository(db_session)
    item, run = _seed(db_session)
    finding = _published(item, run)
    findings.add(finding)
    db_session.flush()
    finding.retract(_RETRACTED, "nota visible")
    findings.save_retraction(finding)
    db_session.flush()
    db_session.expire_all()

    stored = findings.get_published(finding.id)
    assert stored is not None
    assert stored.retraction_note == "nota visible"


def test_item_authors_ida_y_vuelta_con_none_y_con_valor(db_session):
    items = SqlAlchemyItemRepository(db_session)
    sin = make_item(external_id="2601.10001")
    con = make_item(external_id="2601.10002", authors="A. Uno, B. Dos")
    items.add_many([sin, con])
    db_session.flush()
    db_session.expire_all()

    got_sin = items.get(sin.id)
    got_con = items.get(con.id)
    assert got_sin is not None and got_sin.authors is None
    assert got_con is not None and got_con.authors == "A. Uno, B. Dos"
