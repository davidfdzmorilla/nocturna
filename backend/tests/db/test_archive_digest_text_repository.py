"""`SqlAlchemyArchiveDigestTextRepository` y la tabla `archive_digest_text` (T84.r) en PostgreSQL.

Sin Claude. Fichero en español.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from factories import make_run
from helpers.archive_digest import digest_text
from sqlalchemy.exc import IntegrityError

from nocturna.domain.archive_digest import DigestText, DigestTextStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveDigestTextRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

_NOW = datetime(2026, 11, 10, 2, 5, tzinfo=UTC)


def _run(session) -> uuid.UUID:
    run = make_run()
    SqlAlchemyRunRepository(session).add(run)
    session.flush()
    return run.id


def _insert(session, **overrides: object) -> None:
    values = {
        "week": "2026-W42",
        "status": "written",
        "title": "t",
        "summary": "s",
        "sha": "a" * 64,
        "pv": "writer-digest-v1",
        "run_id": None,
        "now": _NOW,
    }
    values.update(overrides)
    if values["run_id"] is None:
        values["run_id"] = _run(session)
    session.execute(
        sa.text(
            "INSERT INTO archive_digest_text "
            "(week, status, title, summary, input_sha256, prompt_version, run_id, created_at) "
            "VALUES (:week, :status, :title, :summary, :sha, :pv, :run_id, :now)"
        ),
        values,
    )
    session.flush()


def test_get_de_una_semana_sin_texto_es_none(db_session) -> None:
    assert SqlAlchemyArchiveDigestTextRepository(db_session).get("2026-W42") is None


def test_all_sin_textos_es_un_dict_vacio(db_session) -> None:
    assert SqlAlchemyArchiveDigestTextRepository(db_session).all() == {}


def test_ida_y_vuelta_de_un_texto_written(db_session) -> None:
    repo = SqlAlchemyArchiveDigestTextRepository(db_session)
    text = digest_text("2026-W42", _run(db_session), title="Título ñ", summary="Uno.\n\nDos.")

    repo.add(text)
    db_session.expire_all()

    assert repo.get("2026-W42") == text
    assert repo.get("2026-W42").created_at.tzinfo is not None


def test_ida_y_vuelta_de_un_texto_failed(db_session) -> None:
    repo = SqlAlchemyArchiveDigestTextRepository(db_session)
    text = digest_text("2026-W42", _run(db_session), status="failed", sha="c" * 64)

    repo.add(text)
    db_session.expire_all()

    stored = repo.get("2026-W42")
    assert stored == text
    assert stored.status is DigestTextStatus.FAILED
    assert stored.title is None and stored.summary is None


def test_all_devuelve_un_dict_por_semana(db_session) -> None:
    repo = SqlAlchemyArchiveDigestTextRepository(db_session)
    run_id = _run(db_session)
    repo.add(digest_text("2026-W40", run_id))
    repo.add(digest_text("2026-W42", run_id, status="failed"))
    db_session.expire_all()

    found = repo.all()

    assert set(found) == {"2026-W40", "2026-W42"}
    assert all(isinstance(v, DigestText) for v in found.values())
    assert found["2026-W42"].status is DigestTextStatus.FAILED


def test_la_clave_primaria_es_la_semana_un_segundo_texto_falla(db_session_factory) -> None:
    with unit_of_work(db_session_factory) as session:
        run_id = _run(session)
        SqlAlchemyArchiveDigestTextRepository(session).add(digest_text("2026-W42", run_id))

    with pytest.raises(IntegrityError, match="duplicate key|unique"):
        with unit_of_work(db_session_factory) as session:
            SqlAlchemyArchiveDigestTextRepository(session).add(
                digest_text("2026-W42", run_id, status="failed")
            )


def test_un_texto_persistido_sobrevive_a_una_sesion_nueva(db_session_factory) -> None:
    with unit_of_work(db_session_factory) as session:
        run_id = _run(session)
        SqlAlchemyArchiveDigestTextRepository(session).add(digest_text("2026-W42", run_id))

    with db_session_factory() as session:
        assert SqlAlchemyArchiveDigestTextRepository(session).get("2026-W42") is not None


# --- CHECK y claves ajenas reales ------------------------------------------------------


@pytest.mark.parametrize("week", ["2026-W5", "2026W42", "26-W42", "2026-W420", "semana"])
def test_check_formato_de_semana(db_session, week: str) -> None:
    with pytest.raises(IntegrityError, match="check constraint"):
        _insert(db_session, week=week)


def test_check_estado_solo_written_o_failed(db_session) -> None:
    with pytest.raises(IntegrityError, match="check constraint"):
        _insert(db_session, status="pending")


def test_check_written_exige_titulo_y_resumen(db_session) -> None:
    with pytest.raises(IntegrityError, match="check constraint"):
        _insert(db_session, title=None)


def test_check_written_exige_resumen(db_session) -> None:
    with pytest.raises(IntegrityError, match="check constraint"):
        _insert(db_session, summary=None)


def test_check_failed_no_lleva_titulo(db_session) -> None:
    with pytest.raises(IntegrityError, match="check constraint"):
        _insert(db_session, status="failed", title="algo", summary=None)


def test_check_failed_no_lleva_resumen(db_session) -> None:
    with pytest.raises(IntegrityError, match="check constraint"):
        _insert(db_session, status="failed", title=None, summary="algo")


def test_failed_sin_titulo_ni_resumen_entra(db_session) -> None:
    _insert(db_session, status="failed", title=None, summary=None)


def test_input_sha256_no_pasa_de_64(db_session) -> None:
    # CHAR(64) sin CHECK de longitud, como payload_sha256 y solution_key: el
    # hash lo calcula siempre `block_sha256`; PostgreSQL solo rechaza lo largo.
    with pytest.raises(sa.exc.DBAPIError):
        _insert(db_session, sha="a" * 65)


def test_el_run_debe_existir(db_session) -> None:
    with pytest.raises(IntegrityError, match="foreign key"):
        _insert(db_session, run_id=uuid.uuid4())


def test_prompt_version_es_obligatoria(db_session) -> None:
    with pytest.raises(IntegrityError, match="null value|not-null"):
        _insert(db_session, pv=None)
