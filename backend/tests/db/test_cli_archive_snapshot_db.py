"""`nocturna archive-snapshot` de punta a punta contra `nocturna_test` (T81).

El archivo se sustituye por `httpx.MockTransport` + `snapshot_handler` (fixtures
reales grabadas); el reloj, por un `FakeClock`; el espaciado de cortesia, por
uno instantaneo. Sin red y sin Claude.
"""

from __future__ import annotations

import os
import re
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
from fakes.clock import FakeClock
from helpers.archive import fixture_rows, snapshot_handler

from nocturna import cli

OCT_1 = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Europe/Madrid"))
TESTS_DIR = Path(__file__).resolve().parents[1]
_REAL_ASYNC_CLIENT = httpx.AsyncClient  # antes de cualquier parche


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


def _counts(factory) -> dict[str, int]:
    with factory() as session:
        return {
            t: session.execute(sa.text(f"select count(*) from {t}")).scalar_one()
            for t in ("archive_snapshot", "archive_solution", "archive_default_change")
        }


def _num(text: str, label: str) -> int:
    match = re.search(rf"^\s*{re.escape(label)}: (\d+)", text, re.MULTILINE)
    assert match, f"{label!r} no aparece en:\n{text}"
    return int(match.group(1))


def test_archivo_caido_503_sale_con_codigo_1_y_no_escribe_nada(
    monkeypatch, db_session_factory, capsys
):
    requests = _serve(monkeypatch, lambda r: httpx.Response(503))

    code = cli.main(["archive-snapshot"])

    captured = capsys.readouterr()
    assert code == 1
    assert "archive-snapshot falló" in captured.err and "503" in captured.err
    assert "Traceback" not in captured.err and captured.out == ""
    assert len(requests) == 1
    assert _counts(db_session_factory) == {
        "archive_snapshot": 0,
        "archive_solution": 0,
        "archive_default_change": 0,
    }


def test_dry_run_no_escribe_nada_en_las_tres_tablas(monkeypatch, db_session_factory, capsys):
    requests = _serve(monkeypatch, snapshot_handler())

    code = cli.main(["archive-snapshot", "--dry-run"])

    out = capsys.readouterr().out
    assert code == 0 and len(requests) == 1
    assert _counts(db_session_factory) == {
        "archive_snapshot": 0,
        "archive_solution": 0,
        "archive_default_change": 0,
    }
    assert "modo=full" in out and "dry-run: no se escribió nada" in out
    assert _num(out, "peticiones") == 1 and _num(out, "altas") > 0


def test_ejecucion_real_escribe_y_la_segunda_sale_incremental_con_cero_cambios(
    monkeypatch, db_session_factory, capsys, _frozen_clock
):
    requests = _serve(monkeypatch, snapshot_handler())

    assert cli.main(["archive-snapshot"]) == 0
    first = capsys.readouterr().out
    first_counts = _counts(db_session_factory)

    assert first_counts["archive_snapshot"] == 1
    assert first_counts["archive_solution"] == _num(first, "filas") > 0
    assert first_counts["archive_default_change"] == 0
    assert "modo=full" in first and "dry-run" not in first
    assert _num(first, "altas") == first_counts["archive_solution"]

    _frozen_clock.set(datetime(2026, 10, 8, 10, 0, tzinfo=ZoneInfo("Europe/Madrid")))
    before = len(requests)
    assert cli.main(["archive-snapshot"]) == 0
    second = capsys.readouterr().out

    assert "modo=incremental" in second
    assert _num(second, "peticiones") == len(requests) - before >= 3
    for label in ("altas", "bajas", "reactivadas", "cambios de default"):
        assert _num(second, label) == 0, label
    assert _num(second, "planetas que pierden default") == 0
    after = _counts(db_session_factory)
    assert after["archive_snapshot"] == 2
    assert after["archive_solution"] == first_counts["archive_solution"]
    assert after["archive_default_change"] == 0


def test_dry_run_sobre_una_base_con_datos_no_la_modifica(monkeypatch, db_session_factory, capsys):
    _serve(monkeypatch, snapshot_handler())
    assert cli.main(["archive-snapshot"]) == 0
    capsys.readouterr()
    before = _counts(db_session_factory)

    assert cli.main(["archive-snapshot", "--full", "--dry-run"]) == 0

    assert _counts(db_session_factory) == before
    assert "modo=full" in capsys.readouterr().out


def test_bajas_masivas_salen_con_codigo_1_y_no_tocan_la_base(
    monkeypatch, db_session_factory, capsys
):
    _serve(monkeypatch, snapshot_handler())
    assert cli.main(["archive-snapshot"]) == 0
    capsys.readouterr()
    before = _counts(db_session_factory)

    _serve(monkeypatch, snapshot_handler(fixture_rows("ps_t81_planets.csv")[:3]))
    code = cli.main(["archive-snapshot", "--full"])

    captured = capsys.readouterr()
    assert code == 1 and "bajas" in captured.err and "Traceback" not in captured.err
    assert _counts(db_session_factory) == before


_SCRIPT = textwrap.dedent(
    """
    import sys

    sys.path.insert(0, sys.argv[1])

    import httpx
    from helpers.archive import snapshot_handler

    _real = httpx.AsyncClient

    def _fake(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(snapshot_handler())
        return _real(*args, **kwargs)

    httpx.AsyncClient = _fake

    import nocturna.cli as cli

    _limiter = cli.RateLimiter

    async def _instant(_s):
        return None

    cli.RateLimiter = lambda s, **kw: _limiter(s, sleep=_instant)

    for argv in (["archive-snapshot", "--dry-run"], ["archive-snapshot"]):
        code = cli.main(argv)
        if code != 0:
            print(f"exit code inesperado {code} con {argv}", file=sys.stderr)
            sys.exit(1)
        leaked = sorted(
            m
            for m in sys.modules
            if m == "claude_agent_sdk"
            or m.startswith("claude_agent_sdk.")
            or m == "nocturna.infrastructure.llm"
            or m.startswith("nocturna.infrastructure.llm.")
        )
        if leaked:
            print(f"modulos prohibidos tras {argv}: {leaked}", file=sys.stderr)
            sys.exit(1)
    print("OK")
    """
)


def test_archive_snapshot_no_importa_el_sdk_ni_el_proveedor_llm(
    test_database_url: str, db_session_factory
):
    """Subproceso limpio: `sys.modules` arranca vacio, asi que un import
    accidental de `claude_agent_sdk` o `nocturna.infrastructure.llm` si se vería."""
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
    assert "Traceback" not in result.stderr
