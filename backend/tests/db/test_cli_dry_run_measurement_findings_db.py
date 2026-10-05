"""`run-night --dry-run` con la sección de findings de medidas (T89), contra PostgreSQL.

Sin red: el feed OAI de arXiv y el servicio de alias del archivo se sirven por
`httpx.MockTransport`. La fecha de la noche se fija (el reloj real movería la
ventana de 30 días de la confirmación). El ensayo no escribe nada: el recuento
de TODAS las tablas del esquema `public` es idéntico antes y después.
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from factories import make_item, make_reading, seed_archive_snapshot
from fakes.clock import FakeClock
from helpers.archive import fixture_rows
from helpers.measurement_findings import FIXTURE, hip67522_b, toi_6981_b

from nocturna import cli
from nocturna.cli import main
from nocturna.domain.entities import ItemStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyTensionEvaluationRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.exoplanet_archive.mappers import archive_solution_from_ps_row

OAI_FIXTURE = (
    Path(__file__).resolve().parents[1] / "fixtures" / "arxiv" / "oai" / "list_records_ep.xml"
)
ARCHIVE_HOST = "exoplanetarchive.ipac.caltech.edu"
NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
ARGS = ["run-night", "--dry-run", "--since", "2000-01-01"]
MARKER = "[dry-run: no se escribió nada en la base]"
NOT_FOUND = '{"manifest": {"lookup_status": "System Not Found"}}'


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    assert test_database_url.rsplit("/", 1)[-1] == "nocturna_test"
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


@pytest.fixture(autouse=True)
def _fixed_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "system_clock_from_config", lambda _config: FakeClock(NOW))


async def _instant_sleep(_delay_s: float) -> None:
    return None


@pytest.fixture(autouse=True)
def _fast_rate_limiter(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cli.RateLimiter

    def _fast(min_interval_s: float, **kwargs: object) -> object:
        kwargs.setdefault("sleep", _instant_sleep)
        return real(min_interval_s, **kwargs)

    monkeypatch.setattr(cli, "RateLimiter", _fast)


def _route(monkeypatch: pytest.MonkeyPatch, alias_body: str = NOT_FOUND) -> None:
    oai = OAI_FIXTURE.read_bytes()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == ARCHIVE_HOST:
            assert "aliaslookup" in request.url.path, f"petición inesperada: {request.url}"
            return httpx.Response(200, text=alias_body)
        return httpx.Response(200, content=oai)

    real_async_client = httpx.AsyncClient

    def _fake(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handle)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fake)


def _enable_confirmation(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cli.load_pipeline_config

    def _patched(*args: object, **kwargs: object):
        config = real(*args, **kwargs)
        mf = config.measurement_findings.model_copy(update={"confirmation_enabled": True})
        return config.model_copy(update={"measurement_findings": mf})

    monkeypatch.setattr(cli, "load_pipeline_config", _patched)


def _snapshot(factory) -> dict[str, object]:
    with factory() as session:
        tables = (
            session.execute(
                sa.text(
                    "select table_name from information_schema.tables "
                    "where table_schema = 'public' and table_type = 'BASE TABLE' "
                    "order by table_name"
                )
            )
            .scalars()
            .all()
        )
        assert {"alembic_version", "items", "findings", "tension_evaluation"} <= set(tables)
        return {
            t: session.execute(sa.text(f'select count(*) from public."{t}"')).scalar_one()
            for t in tables
        }


def _seed(factory) -> None:
    """TOI-6981 b (ausente del archivo, evaluación pendiente) y HIP 67522 b
    (evaluada frente a Chakraborty et al. 2026), con su snapshot del archivo."""
    rows = [
        {**row, "soltype": row.get("soltype") or "Published Confirmed"}
        for row in fixture_rows(FIXTURE)
    ]
    seed_archive_snapshot(factory, [archive_solution_from_ps_row(r) for r in rows])
    with unit_of_work(factory) as session:
        items = SqlAlchemyItemRepository(session)
        toi = make_item(external_id="2609.37597", status=ItemStatus.READ)
        hip = make_item(
            external_id="2609.35979",
            status=ItemStatus.READ,
            published_at=datetime(2026, 10, 2, 8, 0, tzinfo=UTC),
        )
        items.add_many([toi, hip])
        session.flush()
        toi_eval = toi_6981_b(toi.id)
        hip_eval = hip67522_b(hip.id)
        readings = SqlAlchemyReadingRepository(session)
        toi_reading = make_reading(toi.id, measurements=toi_eval.measurements)
        hip_reading = make_reading(hip.id, measurements=hip_eval.measurements)
        readings.add(toi_reading)
        readings.add(hip_reading)
        session.flush()
        evaluations = SqlAlchemyTensionEvaluationRepository(session)
        evaluations.add(dataclasses.replace(toi_eval, reading_id=toi_reading.id))
        evaluations.add(dataclasses.replace(hip_eval, reading_id=hip_reading.id))


def test_el_dry_run_lista_toi_6981_b_y_hip_67522_b_bloqueada_sin_escribir(
    monkeypatch, db_session_factory, capsys
):
    _seed(db_session_factory)
    before = _snapshot(db_session_factory)
    _route(monkeypatch)

    code = main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    assert "Findings de medidas que se generarían esta noche" in out
    assert "primera_medida=1 confirmacion_independiente=0 bloqueadas=1" in out
    assert (
        "primera_medida · 2609.37597 · Primera medida del radio de TOI-6981 b: 2,4 ± 0,1 R⊕" in out
    )
    assert (
        "confirmacion_independiente · 2609.35979 · HIP 67522 b (mass) · "
        "bloqueado (confirmation_enabled=false)"
    ) in out
    assert MARKER in out
    assert _snapshot(db_session_factory) == before, "ninguna tabla cambia, ni findings"


def test_con_la_confirmacion_activada_el_dry_run_la_lista_igualmente_sin_escribir(
    monkeypatch, db_session_factory, capsys
):
    _seed(db_session_factory)
    before = _snapshot(db_session_factory)
    _enable_confirmation(monkeypatch)
    _route(monkeypatch)

    code = main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    assert "primera_medida=1 confirmacion_independiente=1 bloqueadas=0" in out
    assert "confirmacion_independiente · 2609.35979 · Confirmación independiente de la masa" in out
    assert _snapshot(db_session_factory) == before


def test_un_dry_run_no_impide_que_la_noche_real_genere_los_mismos_candidatos(
    monkeypatch, db_session_factory, capsys
):
    from nocturna.application.use_cases.generate_measurement_findings import (
        GenerateMeasurementFindings,
    )

    _seed(db_session_factory)
    _route(monkeypatch)
    assert main(ARGS) == 0
    capsys.readouterr()

    generator = cli.generate_measurement_findings_from_config(
        cli.load_pipeline_config(),
        cli._measurement_findings_work_factory(db_session_factory),
        FakeClock(NOW),
    )
    assert isinstance(generator, GenerateMeasurementFindings)
    from nocturna.domain.entities import Run

    with unit_of_work(db_session_factory) as session:
        from nocturna.infrastructure.db.repositories import SqlAlchemyRunRepository

        run = Run(started_at=NOW, budget_tokens=300_000)
        SqlAlchemyRunRepository(session).add(run)
    report = generator(run_id=run.id, dry_run=False)

    assert [f.type.value for f in report.created] == ["primera_medida"]
    assert len(report.blocked_confirmations) == 1
    with db_session_factory() as session:
        stored = session.execute(sa.text("select type, published_at from findings")).all()
    assert [tuple(r) for r in stored] == [("primera_medida", None)]


def test_la_seccion_de_tensiones_muestra_los_fallos_de_resolucion_del_planeta(
    monkeypatch, db_session_factory, capsys
):
    """D16: una respuesta anómala del alias no es "ausente": el dry-run lo
    muestra como fallo (sin evaluación, se reintenta) y no devuelve error."""
    _seed(db_session_factory)
    with unit_of_work(db_session_factory) as session:
        odd = make_item(external_id="2609.99999", status=ItemStatus.READ)
        SqlAlchemyItemRepository(session).add_many([odd])
        session.flush()
        measurement = dataclasses.replace(
            toi_6981_b(odd.id).measurements[0], planet_name="Raro-1 b"
        )
        SqlAlchemyReadingRepository(session).add(make_reading(odd.id, measurements=(measurement,)))
    before = _snapshot(db_session_factory)
    _route(monkeypatch, alias_body='{"manifest": {"lookup_status": "Server Error"}}')

    code = main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    assert "FALLOS de resolución del planeta (sin evaluación, se reintentan): 2" in out
    # TOI-6981 b sigue pendiente y se vuelve a resolver: también falla esta vez.
    assert "Raro-1 b (radius)" in out and "TOI-6981 b (radius)" in out
    assert "estado guardado (terminales, no recalculado)=1" in out
    assert MARKER in out
    assert _snapshot(db_session_factory) == before
