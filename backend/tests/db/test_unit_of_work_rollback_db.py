"""`unit_of_work(commit=False)` (T87): transacción que siempre se deshace."""

from __future__ import annotations

import pytest
from factories import make_item
from sqlalchemy.orm import Session, sessionmaker

from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository
from nocturna.infrastructure.db.session import unit_of_work


def _count_items(factory: sessionmaker[Session]) -> int:
    with unit_of_work(factory) as session:
        return len(SqlAlchemyItemRepository(session).next_unread(100))


def test_commit_false_deshace_lo_escrito(db_session_factory: sessionmaker[Session]) -> None:
    with unit_of_work(db_session_factory, commit=False) as session:
        added = SqlAlchemyItemRepository(session).add_many([make_item()])
        assert added == 1
    assert _count_items(db_session_factory) == 0


def test_commit_dentro_de_commit_false_lanza_y_no_persiste(
    db_session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(RuntimeError, match="commit"):  # noqa: PT012
        with unit_of_work(db_session_factory, commit=False) as session:
            SqlAlchemyItemRepository(session).add_many([make_item()])
            session.commit()
    assert _count_items(db_session_factory) == 0


def test_excepcion_del_llamador_se_propaga_y_no_persiste(
    db_session_factory: sessionmaker[Session],
) -> None:
    with pytest.raises(ValueError, match="boom"):  # noqa: PT012
        with unit_of_work(db_session_factory, commit=False) as session:
            SqlAlchemyItemRepository(session).add_many([make_item()])
            raise ValueError("boom")
    assert _count_items(db_session_factory) == 0


def test_commit_true_confirma(db_session_factory: sessionmaker[Session]) -> None:
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyItemRepository(session).add_many([make_item()])
    assert _count_items(db_session_factory) == 1
