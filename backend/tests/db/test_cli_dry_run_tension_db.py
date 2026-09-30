"""`run-night --dry-run` con la seccion de tensiones (T74), contra PostgreSQL.

Sin red: `httpx.AsyncClient` se sustituye por uno sobre `httpx.MockTransport`
que enruta por host (arXiv: feed OAI grabado; archivo: fixtures reales del
Exoplanet Archive). Las URLs del archivo son las reales de `pipeline.toml`,
pero nunca salen del proceso. `cli.RateLimiter` se sustituye por uno con
`sleep` instantaneo para no esperar los 2 s de cortesia.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
from factories import make_item, make_reading
from helpers.archive import archive_handler, load_t71c_measurements

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
    with unit_of_work(db_session_factory) as session:
        item = make_item(external_id=PAPER)
        SqlAlchemyItemRepository(session).add_many([item])
        session.flush()
        SqlAlchemyReadingRepository(session).add(make_reading(item.id, measurements=measurements))


def test_dry_run_calcula_las_tensiones_de_v1298_e_imprime_la_seccion(
    monkeypatch, db_session_factory, capsys
):
    _seed_v1298_reading(db_session_factory)
    requests = _route(monkeypatch, archive_handler())

    code = main(["run-night", "--dry-run", "--since", "2000-01-01"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Plan de gasto de la noche:" in out
    assert "Tensiones frente al NASA Exoplanet Archive:" in out
    assert "medidas: total=7 utilizables=7 emparejadas=7 sin previas=0" in out
    assert "candidatos (threshold_sigma=3.0): 1" in out
    assert f"{PAPER} · V1298 Tau b · mass · sigma ref=3.37 · candidato=sí" in out
    assert f"{PAPER} · V1298 Tau e · mass · sigma ref=2.69 · candidato=no" in out
    assert "https://exoplanetarchive.ipac.caltech.edu/overview/V1298%20Tau%20b" in out
    assert "peticiones al archivo: 3" in out
    assert len(requests) == 3  # indice + ps(b) + ps(e)


def test_dry_run_sin_lecturas_con_medidas_no_llama_al_archivo(
    monkeypatch, db_session_factory, capsys
):
    requests = _route(monkeypatch, archive_handler())

    code = main(["run-night", "--dry-run", "--since", "2000-01-01"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Tensiones frente al NASA Exoplanet Archive:" in out
    assert "peticiones al archivo: 0" in out
    assert requests == []


def test_si_el_archivo_falla_el_dry_run_imprime_error_por_stderr_y_devuelve_1(
    monkeypatch, db_session_factory, capsys
):
    _seed_v1298_reading(db_session_factory)
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
