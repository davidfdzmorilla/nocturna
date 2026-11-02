"""T84.r: `archive-digest --week` muestra el texto redactado y lo marca «desactualizado»
si el hash de entrada ya no coincide con el bloque actual de la semana. Solo lectura.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from factories import make_run
from helpers.archive_digest import digest_text
from test_cli_archive_digest_db import _WEEK, _all_counts, _seed, _use_test_database  # noqa: F401

from nocturna import cli
from nocturna.application.use_cases.archive_digest import GetWeeklyDigest
from nocturna.application.use_cases.write_archive_digest import block_sha256, build_digest_block
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveDigestReader,
    SqlAlchemyArchiveDigestTextRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

TITLE = "Un título del resumen de la semana"
SUMMARY = "Un cambio de referencia en un planeta.\n\nNada más que contar."


def _current_sha(factory) -> str:
    with factory() as session:
        digest = GetWeeklyDigest(SqlAlchemyArchiveDigestReader(session), ZoneInfo("Europe/Madrid"))(
            _WEEK
        )
    assert digest is not None
    return block_sha256(build_digest_block(digest))


def _store(factory, *, sha: str, status: str = "written") -> None:
    with unit_of_work(factory) as session:
        run = make_run()
        SqlAlchemyRunRepository(session).add(run)
        SqlAlchemyArchiveDigestTextRepository(session).add(
            digest_text(
                _WEEK,
                run.id,
                status=status,
                sha=sha,
                **({"title": TITLE, "summary": SUMMARY} if status == "written" else {}),
            )
        )


def test_sin_texto_el_listado_es_el_de_siempre_y_no_dice_desactualizado(
    db_session_factory, capsys
) -> None:
    _seed(db_session_factory)

    code = cli.main(["archive-digest", "--week", _WEEK])

    out = capsys.readouterr().out
    assert code == 0 and "desactualizado" not in out
    assert f"Resumen semanal del NASA Exoplanet Archive {_WEEK}" in out


def test_con_texto_vigente_lo_muestra_sin_marca(db_session_factory, capsys) -> None:
    _seed(db_session_factory)
    _store(db_session_factory, sha=_current_sha(db_session_factory))

    code = cli.main(["archive-digest", "--week", _WEEK])

    out = capsys.readouterr().out
    assert code == 0
    assert TITLE in out and "Nada más que contar." in out
    assert "desactualizado" not in out


def test_con_el_hash_distinto_lo_muestra_marcado_desactualizado(db_session_factory, capsys) -> None:
    _seed(db_session_factory)
    _store(db_session_factory, sha="0" * 64)

    code = cli.main(["archive-digest", "--week", _WEEK])

    out = capsys.readouterr().out
    assert code == 0
    assert TITLE in out and "desactualizado" in out


def test_con_texto_fallido_no_muestra_texto_y_no_rompe(db_session_factory, capsys) -> None:
    _seed(db_session_factory)
    _store(db_session_factory, sha=_current_sha(db_session_factory), status="failed")

    code = cli.main(["archive-digest", "--week", _WEEK])

    out = capsys.readouterr().out
    assert code == 0 and TITLE not in out and "desactualizado" not in out
    assert "Texto del redactor" not in out and "None" not in out
    assert f"Resumen semanal del NASA Exoplanet Archive {_WEEK}" in out


@pytest.mark.parametrize("status", ["written", "failed"])
def test_archive_digest_sigue_siendo_de_solo_lectura(db_session_factory, capsys, status) -> None:
    _seed(db_session_factory)
    _store(db_session_factory, sha="0" * 64, status=status)
    before = _all_counts(db_session_factory)

    cli.main(["archive-digest", "--week", _WEEK])

    capsys.readouterr()
    assert _all_counts(db_session_factory) == before
