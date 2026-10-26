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

import anyio
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


def test_evaluate_tensions_con_alias_caido_es_fallo_parcial_y_sale_con_4(
    monkeypatch, db_session_factory, capsys
):
    """T99 (D4): un 503 del alias es un fallo POR PLANETA, no un aborto: no hay
    fila para ese planeta, se informa y el código es 4 (antes 1 con traza)."""
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_reading(db_session_factory, planet_name="Planeta Raro b")
    _serve(monkeypatch, lambda request: httpx.Response(503))

    code = cli.main(["evaluate-tensions"])

    captured = capsys.readouterr()
    assert code == 4
    assert "FALLOS de resolución (sin fila, se reintentan): 1 grupos, 1 planetas" in captured.out
    assert "Planeta Raro b (mass)" in captured.out and "503" in captured.out
    assert "Traceback" not in captured.err
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


# --- T99: el alias que no responde aísla al planeta, no a la pasada -----------

HANG_PLANET = "TOI-210 b"
FAILURES_LINE = "FALLOS de resolución (sin fila, se reintentan): 1 grupos, 1 planetas"


def _fast_alias_timeout(monkeypatch: pytest.MonkeyPatch, seconds: float = 0.2) -> None:
    """Reduce `sources.exoplanet_archive.request_timeout_s` (sin tocar el .toml)."""
    real = cli.load_pipeline_config

    def _patched(*args: object, **kwargs: object):
        config = real(*args, **kwargs)
        archive = config.sources.exoplanet_archive.model_copy(update={"request_timeout_s": seconds})
        sources = config.sources.model_copy(update={"exoplanet_archive": archive})
        return config.model_copy(update={"sources": sources})

    monkeypatch.setattr(cli, "load_pipeline_config", _patched)


def _hanging_alias(inner: Callable[[httpx.Request], httpx.Response] | None = None):
    """Handler asíncrono: el alias de TOI-210 b no contesta nunca; el resto, normal."""
    normal = inner or alias_handler()

    async def handler(request: httpx.Request) -> httpx.Response:
        if "aliaslookup" in request.url.path and request.url.params["objname"] == HANG_PLANET:
            await anyio.sleep(30)
        return normal(request)

    return handler


def _seed_with_hanging_planet(factory) -> None:
    """V1298 Tau b y e (en el índice local) + TOI-210 b (fuera: pide el alias)."""
    measurements = load_t71c_measurements(MEASURES)
    extra = dataclasses.replace(measurements[0], planet_name=HANG_PLANET)
    with unit_of_work(factory) as session:
        item = make_item(external_id=PAPER)
        SqlAlchemyItemRepository(session).add_many([item])
        session.flush()
        SqlAlchemyReadingRepository(session).add(
            make_reading(item.id, measurements=(*measurements, extra))
        )


def test_evaluate_tensions_con_alias_colgado_guarda_el_resto_y_sale_con_4(
    monkeypatch, db_session_factory, capsys
):
    """La tarea T99: el alias de un planeta agota `request_timeout_s`; antes
    abortaba la pasada entera (salida 1, nada escrito)."""
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_with_hanging_planet(db_session_factory)
    _fast_alias_timeout(monkeypatch)
    _serve(monkeypatch, _hanging_alias())

    code = cli.main(["evaluate-tensions"])

    captured = capsys.readouterr()
    assert code == 4
    assert _statuses(db_session_factory) == {
        "V1298 Tau b": "evaluated",
        "V1298 Tau e": "evaluated",
    }
    assert "creadas: 2" in captured.out
    assert FAILURES_LINE in captured.out
    assert f"{HANG_PLANET} (mass)" in captured.out
    assert "Traceback" not in captured.err


def test_evaluate_tensions_con_alias_colgado_se_reintenta_y_sale_con_0_al_volver(
    monkeypatch, db_session_factory, capsys
):
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_with_hanging_planet(db_session_factory)
    _fast_alias_timeout(monkeypatch)
    _serve(monkeypatch, _hanging_alias())
    assert cli.main(["evaluate-tensions"]) == 4
    capsys.readouterr()

    _serve(
        monkeypatch, alias_handler(alias_body='{"manifest": {"lookup_status": "System Not Found"}}')
    )
    code = cli.main(["evaluate-tensions"])

    out = capsys.readouterr().out
    assert code == 0 and "FALLOS" not in out
    assert _statuses(db_session_factory)[HANG_PLANET] == "awaiting_reference"
    assert _count(db_session_factory, "tension_evaluation") == 3


def test_evaluate_tensions_dry_run_con_alias_colgado_sale_con_4_y_no_escribe(
    monkeypatch, db_session_factory, capsys
):
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_with_hanging_planet(db_session_factory)
    _fast_alias_timeout(monkeypatch)
    _serve(monkeypatch, _hanging_alias())

    code = cli.main(["evaluate-tensions", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 4
    assert "dry-run: no se escribió nada" in out and "creadas: 2" in out
    assert FAILURES_LINE in out
    assert _count(db_session_factory, "tension_evaluation") == 0


def test_evaluate_tensions_sin_fallos_sigue_saliendo_con_0(monkeypatch, db_session_factory):
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    _seed_reading(db_session_factory)
    _serve(monkeypatch, alias_handler())

    assert cli.main(["evaluate-tensions"]) == 0


def test_evaluate_tensions_con_indice_vacio_sigue_abortando_con_1_aunque_haya_alias(
    monkeypatch, db_session_factory, capsys
):
    """El índice local vacío no es un fallo por planeta: aborta (exit 1) sin
    pedir el alias y sin escribir."""
    _seed_with_hanging_planet(db_session_factory)
    requests = _serve(monkeypatch, alias_handler())

    code = cli.main(["evaluate-tensions"])

    captured = capsys.readouterr()
    assert code == 1
    assert "evaluate-tensions falló" in captured.err and "vacío" in captured.err
    assert "FALLOS" not in captured.out
    assert requests == []
    assert _count(db_session_factory, "tension_evaluation") == 0


def _snapshot_and_hanging_alias():
    snapshot = snapshot_handler()
    alias = _hanging_alias()

    async def handler(request: httpx.Request) -> httpx.Response:
        if "aliaslookup" in request.url.path:
            return await alias(request)
        return snapshot(request)

    return handler


def test_archive_snapshot_con_alias_colgado_guarda_todo_y_sale_con_4(
    monkeypatch, db_session_factory, capsys
):
    """D6: snapshot + evaluación del resto + tensiones entre soluciones guardados;
    el planeta sin resolver solo cambia el código a 4."""
    _seed_with_hanging_planet(db_session_factory)
    _fast_alias_timeout(monkeypatch)
    _serve(monkeypatch, _snapshot_and_hanging_alias())

    code = cli.main(["archive-snapshot"])

    captured = capsys.readouterr()
    assert code == 4
    assert _count(db_session_factory, "archive_snapshot") == 1
    assert _statuses(db_session_factory) == {
        "V1298 Tau b": "evaluated",
        "V1298 Tau e": "evaluated",
    }
    assert FAILURES_LINE in captured.out
    # La pasada de tensiones entre soluciones corrió (su informe está en stdout).
    assert "Tensiones entre soluciones del NASA Exoplanet Archive" in captured.out
    assert "Traceback" not in captured.err


def test_archive_snapshot_el_3_prevalece_sobre_el_4(monkeypatch, db_session_factory, capsys):
    """Fallos parciales (4) y, además, excepción en tensiones entre soluciones (3)."""
    _seed_with_hanging_planet(db_session_factory)
    _fast_alias_timeout(monkeypatch)
    _serve(monkeypatch, _snapshot_and_hanging_alias())

    def _boom(*args: object, **kwargs: object):
        raise RuntimeError("tensiones entre soluciones caídas")

    monkeypatch.setattr(cli, "compute_solution_tensions_from_config", _boom)

    code = cli.main(["archive-snapshot"])

    captured = capsys.readouterr()
    assert code == 3
    assert "tensiones entre soluciones fallaron" in captured.err
    assert _count(db_session_factory, "archive_snapshot") == 1


def test_archive_snapshot_sin_fallos_sale_con_0(monkeypatch, db_session_factory):
    _seed_reading(db_session_factory)
    _serve(monkeypatch, snapshot_handler())

    assert cli.main(["archive-snapshot"]) == 0
