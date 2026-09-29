"""Test `-m db` de la consulta Q8 de `scripts/night_report.sql` (T71.c).

Q8 lista las medidas del Reader de la noche (`reader-v3`): ítem, planeta,
parámetro, valor, errores, unidad, límite, origen y evidencia. Este fichero
inserta datos reales (con `commit()`, vía `db_session_factory`) en
`nocturna_test` y ejecuta el propio SQL del script -- no una reescritura a
mano -- con el `psql` del sistema, contra esa misma base. Si `psql` no está
instalado en el entorno que corre la suite, no hay arnés posible y el test
se salta explícitamente, sin fingir que pasó.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from factories import make_agent_call, make_item, make_reading, make_run
from sqlalchemy.engine import make_url

from nocturna.domain.entities import (
    AgentCallStatus,
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
)
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)

BACKEND_DIR = Path(__file__).resolve().parents[2]
NIGHT_REPORT_SQL = BACKEND_DIR / "scripts" / "night_report.sql"


def _extract_q8_sql() -> str:
    """Extrae, del propio fichero de producción, la consulta Q8 -- desde su
    `\\echo` de cabecera hasta el final del fichero (es la última
    consulta)."""
    text = NIGHT_REPORT_SQL.read_text()
    marker_index = text.index("=== Q8")
    start = text.rfind("\n\\echo", 0, marker_index)
    assert start != -1, "no se encontró la cabecera \\echo de Q8 en night_report.sql"
    return text[start:]


def _psql_url(sqlalchemy_url: str) -> str:
    """`postgresql+psycopg://...` -> `postgresql://...`: `psql` no entiende
    el sufijo de driver de SQLAlchemy."""
    return (
        make_url(sqlalchemy_url).set(drivername="postgresql").render_as_string(hide_password=False)
    )


def test_q8_lista_las_medidas_del_reader_de_la_ultima_noche(test_database_url, db_session_factory):
    if shutil.which("psql") is None:
        pytest.skip(
            "psql no está instalado en este entorno: no hay arnés para ejecutar "
            "night_report.sql directamente, solo se puede comprobar por inspección manual"
        )

    session = db_session_factory()
    try:
        items = SqlAlchemyItemRepository(session)
        readings = SqlAlchemyReadingRepository(session)
        runs = SqlAlchemyRunRepository(session)
        agent_calls = SqlAlchemyAgentCallRepository(session)

        run = make_run()
        runs.add(run)
        item = make_item(external_id="2609.99999", categories=["astro-ph.EP"])
        items.add_many([item])
        session.flush()

        measurement = Measurement(
            planet_name="Kepler-Q8 b",
            parameter=MeasuredParameter.MASS,
            value=2.8,
            err_plus=0.5,
            err_minus=0.5,
            unit=MeasurementUnit.M_JUP,
            limit=MeasurementLimit.NONE,
            origin=MeasurementOrigin.THIS_WORK,
            evidence="a mass of 2.8 M_jup, ZQXQ8EVIDENCE",
        )
        reading = make_reading(item_id=item.id, measurements=(measurement,))
        readings.add(reading)

        agent_call = make_agent_call(
            run.id,
            item_id=item.id,
            agent=AgentRole.READER,
            status=AgentCallStatus.OK,
            prompt_version="reader-v3",
        )
        agent_calls.add(agent_call)
        session.commit()
    finally:
        session.close()

    q8_sql = _extract_q8_sql()
    psql_url = _psql_url(test_database_url)

    result = subprocess.run(
        ["psql", psql_url, "-v", "ON_ERROR_STOP=1", "--no-psqlrc"],
        input=q8_sql,
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, f"psql falló ejecutando Q8 de night_report.sql: {result.stderr}"
    assert "2609.99999" in result.stdout
    assert "reader-v3" in result.stdout
    assert "Kepler-Q8 b" in result.stdout
    assert "mass" in result.stdout
    assert "ZQXQ8EVIDENCE" in result.stdout
