"""`nocturna evaluate-tensions` y la evaluación posterior a `archive-snapshot` (T88).

Contra `nocturna_test`. Sin red (MockTransport: solo el alias o el snapshot
grabado) y sin Claude.
"""

from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import textwrap
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import sqlalchemy as sa
from factories import make_item, make_reading, seed_archive_snapshot
from fakes.clock import FakeClock
from helpers.archive import (
    alias_handler,
    fixture_rows,
    load_t71c_measurements,
    snapshot_handler,
    v1298_archive_solutions,
)
from helpers.exoplanet import make_measurement

from nocturna import cli
from nocturna.domain.entities import MeasurementUnit
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.exoplanet_archive.client import ExoplanetArchiveUnavailable
from nocturna.infrastructure.exoplanet_archive.mappers import archive_solution_from_ps_row

OCT_1 = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
TESTS_DIR = Path(__file__).resolve().parents[1]
PAPER = "2609.30038"
MEASURES = "2609.30038.reader-measures-exp1.derived-fullname.json"
_REAL_ASYNC_CLIENT = httpx.AsyncClient


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


@pytest.fixture(autouse=True)
def _frozen_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock(OCT_1)
    monkeypatch.setattr(cli, "system_clock_from_config", lambda config: clock)
    return clock


@pytest.fixture(autouse=True)
def _instant_courtesy(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cli.RateLimiter

    async def _instant(_s: float) -> None:
        return None

    monkeypatch.setattr(cli, "RateLimiter", lambda s, **kw: real(s, sleep=_instant))


def _serve(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]):
    requests: list[httpx.Request] = []

    def _recording(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return handler(request)

    def _fake(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(_recording)
        return _REAL_ASYNC_CLIENT(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fake)
    return requests


def _seed_reading(factory, *, planet_name: str | None = None, external_id: str = PAPER) -> None:
    measurements = load_t71c_measurements(MEASURES)
    if planet_name is not None:
        measurements = (dataclasses.replace(measurements[0], planet_name=planet_name),)
    with unit_of_work(factory) as session:
        item = make_item(external_id=external_id)
        SqlAlchemyItemRepository(session).add_many([item])
        session.flush()
        SqlAlchemyReadingRepository(session).add(make_reading(item.id, measurements=measurements))


def _count(factory, table: str) -> int:
    with factory() as session:
        return session.execute(sa.text(f"select count(*) from {table}")).scalar_one()


def _statuses(factory) -> dict[str, str]:
    with factory() as session:
        rows = session.execute(
            sa.text("select planet_name, status from tension_evaluation order by planet_name")
        ).all()
    return {name: status for name, status in rows}


def test_evaluate_tensions_crea_una_evaluacion_por_planeta_y_parametro(
    monkeypatch, db_session_factory, capsys
):
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_reading(db_session_factory)
    requests = _serve(monkeypatch, alias_handler())

    code = cli.main(["evaluate-tensions"])

    out = capsys.readouterr().out
    assert code == 0
    assert "creadas: 2" in out and "reevaluadas: 0" in out
    assert "por estado: evaluated=2" in out
    assert _statuses(db_session_factory) == {
        "V1298 Tau b": "evaluated",
        "V1298 Tau e": "evaluated",
    }
    assert requests == []


def test_evaluate_tensions_repetido_no_cambia_nada_y_cuenta_las_terminales(
    monkeypatch, db_session_factory, capsys
):
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_reading(db_session_factory)
    _serve(monkeypatch, alias_handler())
    assert cli.main(["evaluate-tensions"]) == 0
    capsys.readouterr()

    assert cli.main(["evaluate-tensions"]) == 0

    out = capsys.readouterr().out
    assert "creadas: 0" in out and "terminales (no se tocan): 2" in out
    assert _count(db_session_factory, "tension_evaluation") == 2


def test_evaluate_tensions_dry_run_informa_pero_no_escribe(monkeypatch, db_session_factory, capsys):
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_reading(db_session_factory)
    _serve(monkeypatch, alias_handler())

    code = cli.main(["evaluate-tensions", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0
    assert "dry-run: no se escribió nada" in out and "creadas: 2" in out
    assert _count(db_session_factory, "tension_evaluation") == 0


def test_evaluate_tensions_con_alias_caido_sale_con_1_y_no_escribe(
    monkeypatch, db_session_factory, capsys
):
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_reading(db_session_factory, planet_name="Planeta Raro b")
    _serve(monkeypatch, lambda request: httpx.Response(503))

    code = cli.main(["evaluate-tensions"])

    captured = capsys.readouterr()
    assert code == 1
    assert "evaluate-tensions falló" in captured.err and "503" in captured.err
    assert "Traceback" not in captured.err and captured.out == ""
    assert _count(db_session_factory, "tension_evaluation") == 0


def test_evaluate_tensions_sin_snapshot_local_sale_con_1(monkeypatch, db_session_factory, capsys):
    _seed_reading(db_session_factory)
    _serve(monkeypatch, alias_handler())

    code = cli.main(["evaluate-tensions"])

    captured = capsys.readouterr()
    assert code == 1 and "archive-snapshot" in captured.err
    assert _count(db_session_factory, "tension_evaluation") == 0


def test_archive_snapshot_evalua_las_tensiones_tras_guardar(
    monkeypatch, db_session_factory, capsys
):
    _seed_reading(db_session_factory)
    _serve(monkeypatch, snapshot_handler())

    code = cli.main(["archive-snapshot"])

    out = capsys.readouterr().out
    assert code == 0
    assert _count(db_session_factory, "archive_snapshot") == 1
    assert "Evaluaciones de tensión" in out and "creadas: 2" in out
    assert _count(db_session_factory, "tension_evaluation") == 2


def test_archive_snapshot_dry_run_no_evalua(monkeypatch, db_session_factory, capsys):
    _seed_reading(db_session_factory)
    _serve(monkeypatch, snapshot_handler())

    code = cli.main(["archive-snapshot", "--dry-run"])

    assert code == 0
    assert "Evaluaciones de tensión" not in capsys.readouterr().out
    assert _count(db_session_factory, "tension_evaluation") == 0


def test_si_la_evaluacion_falla_el_snapshot_queda_guardado_y_sale_con_3(
    monkeypatch, db_session_factory, capsys
):
    _seed_reading(db_session_factory)
    _serve(monkeypatch, snapshot_handler())

    async def _boom(*args: object, **kwargs: object):
        raise ExoplanetArchiveUnavailable("alias caído")

    monkeypatch.setattr(cli, "_record_tension_evaluations", _boom)

    code = cli.main(["archive-snapshot"])

    captured = capsys.readouterr()
    assert code == 3
    assert "snapshot guardado" in captured.err and "alias caído" in captured.err
    assert "ExoplanetArchiveUnavailable" in captured.err
    assert _count(db_session_factory, "archive_snapshot") == 1
    assert _count(db_session_factory, "archive_solution") > 0
    assert _count(db_session_factory, "tension_evaluation") == 0


def test_cualquier_excepcion_de_la_evaluacion_da_3_y_el_snapshot_queda(
    monkeypatch, db_session_factory, capsys
):
    _seed_reading(db_session_factory)
    _serve(monkeypatch, snapshot_handler())

    def _missing_table(self: object) -> list:
        raise sa.exc.ProgrammingError("select", {}, Exception('relation "tension_evaluation"'))

    monkeypatch.setattr(
        cli.SqlAlchemyTensionEvaluationRepository, "all", _missing_table, raising=True
    )

    code = cli.main(["archive-snapshot"])

    captured = capsys.readouterr()
    assert code == 3
    assert "snapshot guardado" in captured.err
    assert "ProgrammingError" in captured.err and "Traceback" in captured.err
    assert _count(db_session_factory, "archive_snapshot") == 1
    assert _count(db_session_factory, "archive_solution") > 0


def test_si_el_snapshot_aborta_no_se_evalua(monkeypatch, db_session_factory, capsys):
    _seed_reading(db_session_factory)
    _serve(monkeypatch, lambda r: httpx.Response(503))
    called: list[int] = []

    async def _spy(*args: object, **kwargs: object):
        called.append(1)
        raise AssertionError("no debe evaluarse")

    monkeypatch.setattr(cli, "_record_tension_evaluations", _spy)

    assert cli.main(["archive-snapshot"]) == 1
    assert called == []


_SCRIPT = textwrap.dedent(
    """
    import sys

    sys.path.insert(0, sys.argv[1])

    import nocturna.cli as cli

    code = cli.main(["evaluate-tensions", "--dry-run"])
    leaked = sorted(
        m
        for m in sys.modules
        if m == "claude_agent_sdk"
        or m.startswith("claude_agent_sdk.")
        or m == "nocturna.infrastructure.llm"
        or m.startswith("nocturna.infrastructure.llm.")
    )
    if code != 0 or leaked:
        print(f"code={code} prohibidos={leaked}", file=sys.stderr)
        sys.exit(1)
    print("OK")
    """
)


def test_evaluate_tensions_no_importa_el_sdk_ni_el_proveedor_llm(
    test_database_url: str, db_session_factory
):
    env = dict(os.environ)
    env["NOCTURNA_DATABASE_URL"] = test_database_url

    result = subprocess.run(
        [sys.executable, "-c", _SCRIPT, str(TESTS_DIR)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
    )

    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    assert "OK" in result.stdout


HIP_B = "HIP 67522 b"


def _hip_rows(*, only_barber: bool = False, chakraborty_soltype: str = "Published Confirmed"):
    rows = fixture_rows("ps_hip67522_chakraborty2026.csv")
    barber, chak_b, _chak_c = rows
    if only_barber:
        return [barber]
    return [barber, {**chak_b, "soltype": chakraborty_soltype}]


def _seed_hip_reading(factory) -> None:
    measurement = make_measurement(25.0, 7.6, 7.8, planet_name=HIP_B, unit=MeasurementUnit.M_EARTH)
    with unit_of_work(factory) as session:
        item = make_item(external_id="2610.00001")
        SqlAlchemyItemRepository(session).add_many([item])
        session.flush()
        SqlAlchemyReadingRepository(session).add(make_reading(item.id, measurements=(measurement,)))


def _evaluation_rows(factory):
    with factory() as session:
        return session.execute(
            sa.text(
                "select id, status, first_evaluated_at, "
                "jsonb_typeof(detail->'result') = 'object' "
                "from tension_evaluation order by planet_name"
            )
        ).all()


def test_segundo_snapshot_con_referencia_pasa_de_awaiting_a_evaluated_conservando_id(
    monkeypatch, db_session_factory, capsys
):
    _seed_hip_reading(db_session_factory)
    _serve(monkeypatch, snapshot_handler(_hip_rows(only_barber=True)))
    assert cli.main(["archive-snapshot"]) == 0
    ((first_id, status, first_at, _),) = _evaluation_rows(db_session_factory)
    assert status == "awaiting_reference"
    capsys.readouterr()

    _serve(monkeypatch, snapshot_handler(_hip_rows()))
    assert cli.main(["archive-snapshot", "--full"]) == 0

    ((second_id, status, second_at, has_result),) = _evaluation_rows(db_session_factory)
    out = capsys.readouterr().out
    assert status == "evaluated" and has_result
    assert (second_id, second_at) == (first_id, first_at)
    assert "reevaluadas: 1" in out and "creadas: 0" in out
    assert _count(db_session_factory, "tension_evaluation") == 1


def test_awaiting_con_resultado_informado_es_idempotente_tras_el_round_trip_jsonb(
    monkeypatch, db_session_factory, capsys
):
    # Previas que no son Published Confirmed: sin referencia, pero con comparaciones.
    seed_archive_snapshot(
        db_session_factory,
        [
            archive_solution_from_ps_row(
                {**row, "releasedate": row["releasedate"], "default_flag": "1"}
            )
            for row in _hip_rows(chakraborty_soltype="Controversial")[1:]
        ],
    )
    _seed_hip_reading(db_session_factory)
    _serve(monkeypatch, alias_handler())
    assert cli.main(["evaluate-tensions"]) == 0
    ((first_id, status, _, has_result),) = _evaluation_rows(db_session_factory)
    assert status == "awaiting_reference" and has_result
    capsys.readouterr()

    assert cli.main(["evaluate-tensions"]) == 0

    out = capsys.readouterr().out
    assert "sin cambios: 1" in out and "reevaluadas: 0" in out
    ((again_id, _, _, _),) = _evaluation_rows(db_session_factory)
    assert again_id == first_id
