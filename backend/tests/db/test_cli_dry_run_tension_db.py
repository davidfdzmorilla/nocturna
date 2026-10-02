"""`run-night --dry-run` con la seccion de tensiones (T74, T88), contra PostgreSQL.

T88: las soluciones salen de `archive_solution` en la base, sembrada con las
filas REALES de V1298 Tau de `ps_v1298tau.csv` (con `soltype="Published
Confirmed"` y la fecha de captura como `releasedate`: ese CSV es anterior a T81
y no trae ambas columnas; ver `helpers.archive.archive_solutions_from_fixture`).

Sin red: `httpx.AsyncClient` se sustituye por uno sobre `httpx.MockTransport`
que enruta por host (arXiv: feed OAI grabado; archivo: solo el alias). Las URLs
del archivo son las reales de `pipeline.toml`, `cli.RateLimiter` se sustituye por uno con
`sleep` instantaneo para no esperar los 2 s de cortesia.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from factories import make_item, make_reading, seed_archive_snapshot
from helpers.archive import alias_handler, load_t71c_measurements, v1298_archive_solutions

from nocturna import cli
from nocturna.cli import main
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

OAI_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "arxiv" / "oai" / "list_records_ep.xml"
)
ARCHIVE_HOST = "exoplanetarchive.ipac.caltech.edu"
PAPER = "2609.30038"


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


async def _instant_sleep(_delay_s: float) -> None:
    return None


@pytest.fixture(autouse=True)
def _fast_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cli.RateLimiter

    def _fast(min_interval_s: float, **kwargs: object) -> object:
        kwargs.setdefault("sleep", _instant_sleep)
        return real(min_interval_s, **kwargs)

    monkeypatch.setattr(cli, "RateLimiter", _fast)


def _route(
    monkeypatch: pytest.MonkeyPatch, archive: Callable[[httpx.Request], httpx.Response]
) -> list[httpx.Request]:
    """Enruta por host; devuelve la lista de peticiones que llegan al archivo."""
    archive_requests: list[httpx.Request] = []
    oai = OAI_FIXTURE.read_bytes()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == ARCHIVE_HOST:
            archive_requests.append(request)
            return archive(request)
        return httpx.Response(200, content=oai)

    real_async_client = httpx.AsyncClient

    def _fake_async_client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handle)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fake_async_client)
    return archive_requests


def _seed_v1298_reading(db_session_factory) -> None:
    measurements = load_t71c_measurements("2609.30038.reader-measures-exp1.derived-fullname.json")
    seed_archive_snapshot(db_session_factory, v1298_archive_solutions())
    with unit_of_work(db_session_factory) as session:
        item = make_item(external_id=PAPER)
        SqlAlchemyItemRepository(session).add_many([item])
        session.flush()
        SqlAlchemyReadingRepository(session).add(make_reading(item.id, measurements=measurements))


def test_dry_run_calcula_las_tensiones_de_v1298_e_imprime_la_seccion(
    monkeypatch, db_session_factory, capsys
):
    _seed_v1298_reading(db_session_factory)
    requests = _route(monkeypatch, alias_handler())

    code = main(["run-night", "--dry-run", "--since", "2000-01-01"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Plan de gasto de la noche:" in out
    assert "Tensiones frente al NASA Exoplanet Archive:" in out
    assert "medidas: total=7 utilizables=7 omitidas por TTV=0" in out
    assert "evaluaciones: 2 (con referencia: 2, sin ella: 0)" in out
    assert "por estado: evaluated=2" in out
    assert "candidatos (threshold_sigma=3.0): 1" in out
    assert f"{PAPER} · V1298 Tau b · mass · sigma ref=3.37 · candidato=sí" in out
    assert f"{PAPER} · V1298 Tau e · mass · sigma ref=2.69 · candidato=no" in out
    assert "ref=Livingston et al. 2026" in out
    assert "https://exoplanetarchive.ipac.caltech.edu/overview/V1298%20Tau%20b" in out
    assert "peticiones al archivo (alias): 0" in out
    assert requests == []  # soluciones e índice salen de la base, no del archivo


def test_dry_run_sin_lecturas_con_medidas_no_llama_al_archivo(
    monkeypatch, db_session_factory, capsys
):
    requests = _route(monkeypatch, alias_handler())

    code = main(["run-night", "--dry-run", "--since", "2000-01-01"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Tensiones frente al NASA Exoplanet Archive:" in out
    assert "peticiones al archivo (alias): 0" in out
    assert requests == []


def test_si_el_alias_del_archivo_falla_el_dry_run_imprime_error_por_stderr_y_devuelve_1(
    monkeypatch, db_session_factory, capsys
):
    """Un planeta que el índice local no conoce obliga a consultar el alias;
    si el archivo cae, el dry-run falla con 1 (sin escribir nada)."""
    _seed_v1298_reading(db_session_factory)
    with unit_of_work(db_session_factory) as session:
        odd = make_item(external_id="2609.99999")
        SqlAlchemyItemRepository(session).add_many([odd])
        session.flush()
        measurement = load_t71c_measurements(
            "2609.30038.reader-measures-exp1.derived-fullname.json"
        )[0]
        measurement = dataclasses.replace(measurement, planet_name="Planeta Raro b")
        SqlAlchemyReadingRepository(session).add(make_reading(odd.id, measurements=(measurement,)))
    _route(monkeypatch, lambda request: httpx.Response(503, text="mantenimiento"))

    code = main(["run-night", "--dry-run", "--since", "2000-01-01"])

    captured = capsys.readouterr()
    assert code == 1
    # La ingesta y el plan ya se habian impreso antes del fallo.
    assert "fetched=" in captured.out
    assert "Plan de gasto de la noche:" in captured.out
    assert "Tensiones frente al NASA Exoplanet Archive:" not in captured.out
    assert "error consultando el NASA Exoplanet Archive" in captured.err
    assert "503" in captured.err
    assert "Traceback" not in captured.err


def test_dry_run_con_lecturas_y_sin_snapshot_local_falla_con_1_y_explica_el_motivo(
    monkeypatch, db_session_factory, capsys
):
    measurements = load_t71c_measurements("2609.30038.reader-measures-exp1.derived-fullname.json")
    with unit_of_work(db_session_factory) as session:
        item = make_item(external_id=PAPER)
        SqlAlchemyItemRepository(session).add_many([item])
        session.flush()
        SqlAlchemyReadingRepository(session).add(make_reading(item.id, measurements=measurements))
    _route(monkeypatch, alias_handler())

    code = main(["run-night", "--dry-run", "--since", "2000-01-01"])

    captured = capsys.readouterr()
    assert code == 1
    assert "archive-snapshot" in captured.err
