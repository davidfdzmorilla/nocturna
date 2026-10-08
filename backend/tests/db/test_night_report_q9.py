"""Test `-m db` de las consultas Q9 y Q9b de `scripts/night_report.sql` (T76).

Siembra un `catalog_tension` y dos llamadas del redactor sobre su ítem, ejecuta
las consultas con `psql` y comprueba que el gasto del redactor no se duplica.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from factories import make_agent_call, make_finding
from helpers.exoplanet import catalog_tension_v1298_b
from helpers.tension_smoke import seed_v1298_tension
from sqlalchemy.engine import make_url

from nocturna.domain.entities import AgentCallStatus, FindingType
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyFindingRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

NIGHT_REPORT_SQL = Path(__file__).resolve().parents[2] / "scripts" / "night_report.sql"


def _segment(start_marker: str, end_marker: str | None) -> str:
    text = NIGHT_REPORT_SQL.read_text()
    marker = text.index(start_marker)
    start = text.rfind("\n\\echo", 0, marker)
    if end_marker is None:
        return text[start:]
    end = text.rfind("\n\\echo", 0, text.index(end_marker, marker))
    return text[start:end]


def _psql(url: str, sql: str) -> list[list[str]]:
    plain = make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)
    result = subprocess.run(
        ["psql", plain, "-v", "ON_ERROR_STOP=1", "--no-psqlrc", "-A", "-t", "-F", "|"],
        input=sql,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"psql falló: {result.stderr}"
    return [
        line.split("|")
        for line in result.stdout.splitlines()
        if line.strip() and not line.startswith("===")
    ]


def test_q9_y_q9b_no_duplican_el_gasto_del_redactor(test_database_url, db_session_factory):
    if shutil.which("psql") is None:
        pytest.skip("psql no está instalado en este entorno")
    seeded = seed_v1298_tension(db_session_factory, run_budget_tokens=24_000)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyFindingRepository(session).add(
            make_finding(
                seeded.item.id,
                seeded.run_id,
                type=FindingType.CATALOG_TENSION,
                catalog_tension=catalog_tension_v1298_b(),
                tension_evaluation_id=seeded.evaluation.id,
            )
        )
        calls = SqlAlchemyAgentCallRepository(session)
        for status, tokens_in, tokens_out in (
            (AgentCallStatus.INVALID_OUTPUT, 700, 100),
            (AgentCallStatus.OK, 800, 200),
        ):
            calls.add(
                make_agent_call(
                    seeded.run_id,
                    item_id=seeded.item.id,
                    agent=AgentRole.WRITER,
                    status=status,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    prompt_version="writer-v1",
                )
            )

    q9 = _psql(test_database_url, _segment("=== Q9 ", "=== Q9b"))
    q9b = _psql(test_database_url, _segment("=== Q9b", None))

    (row,) = q9
    assert row[0] == "2609.30038" and row[1] == "V1298 Tau b" and row[2] == "mass"
    assert row[7] == "2" and row[8] == "1800", "las dos llamadas, contadas una sola vez"
    assert sorted(q9b) == [["invalid_output", "1", "1", "800"], ["ok", "1", "1", "1000"]]
    assert sum(int(r[3]) for r in q9b) == int(row[8])
