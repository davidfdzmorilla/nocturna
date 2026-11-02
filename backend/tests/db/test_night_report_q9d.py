"""Test `-m db` de la consulta Q9d de `scripts/night_report.sql` (T84.r): textos del resumen
semanal de la noche. Siembra textos y llamadas del redactor sin ítem, ejecuta con `psql`.
"""

from __future__ import annotations

import shutil

import pytest
from factories import aware, make_agent_call, make_run
from helpers.archive_digest import digest_text
from test_night_report_q9 import _psql, _segment

from nocturna.domain.entities import AgentCallStatus, RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyArchiveDigestTextRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work


def _seed(factory):
    with unit_of_work(factory) as session:
        run = make_run()
        SqlAlchemyRunRepository(session).add(run)
        texts = SqlAlchemyArchiveDigestTextRepository(session)
        texts.add(digest_text("2026-W42", run.id, title="Título de la W42"))
        texts.add(digest_text("2026-W43", run.id, status="failed"))
        calls = SqlAlchemyAgentCallRepository(session)
        for status, tokens_in, tokens_out in (
            (AgentCallStatus.OK, 800, 200),
            (AgentCallStatus.INVALID_OUTPUT, 600, 100),
            (AgentCallStatus.INVALID_OUTPUT, 600, 100),
        ):
            calls.add(
                make_agent_call(
                    run.id,
                    item_id=None,
                    agent=AgentRole.WRITER,
                    status=status,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    prompt_version="writer-digest-v1",
                )
            )
        return run.id


def test_q9d_lista_los_textos_de_la_noche_con_estado_y_las_llamadas_aparecen_en_q9b(
    test_database_url, db_session_factory
):
    if shutil.which("psql") is None:
        pytest.skip("psql no está instalado en este entorno")
    _seed(db_session_factory)

    q9b = _psql(test_database_url, _segment("=== Q9b", "=== Q9c"))
    q9d = _psql(test_database_url, _segment("=== Q9d", "=== Q10"))

    assert ["writer-digest-v1", "invalid_output", "2", "0", "1400"] in q9b  # item_id NULL
    assert ["writer-digest-v1", "ok", "1", "0", "1000"] in q9b
    flat = [cell for row in q9d for cell in row]
    assert len(q9d) == 2
    assert "2026-W42" in flat and "2026-W43" in flat
    assert "written" in flat and "failed" in flat
    assert "Título de la W42" in flat


def test_q9d_sin_textos_de_la_noche_no_devuelve_filas(test_database_url, db_session_factory):
    if shutil.which("psql") is None:
        pytest.skip("psql no está instalado en este entorno")
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyRunRepository(session).add(make_run())

    assert _psql(test_database_url, _segment("=== Q9d", "=== Q10")) == []


def test_q9d_solo_cuenta_los_textos_de_la_ultima_noche(test_database_url, db_session_factory):
    if shutil.which("psql") is None:
        pytest.skip("psql no está instalado en este entorno")
    with unit_of_work(db_session_factory) as session:
        old = make_run(
            started_at=aware(-3 * 24 * 60),
            status=RunStatus.COMPLETED,
            finished_at=aware(-3 * 24 * 60 + 60),
        )
        SqlAlchemyRunRepository(session).add(old)
        SqlAlchemyArchiveDigestTextRepository(session).add(digest_text("2026-W40", old.id))
    _seed(db_session_factory)  # la noche de `_seed` es la última

    q9d = _psql(test_database_url, _segment("=== Q9d", "=== Q10"))

    assert len(q9d) == 2
    assert "2026-W40" not in [cell for row in q9d for cell in row]
