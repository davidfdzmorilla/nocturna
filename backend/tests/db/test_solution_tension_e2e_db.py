"""T85 de punta a punta contra `nocturna_test`, con las filas REALES de `ps_t81_planets.csv`.

Sin red (archivo servido por `httpx.MockTransport`) y sin Claude. Cubre: HD 202206 c
(masa y periodo frente a Benedict & Harrison 2017), frontera de `since` en SQL,
`--dry-run` sin escrituras en NINGUNA tabla, `archive-snapshot` con código 3 si el
paso nuevo falla, la sección de `run-night --dry-run`, `next_unread` mezclado y
`/findings?type=solution_tension` -> 422.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, date, datetime

import httpx
import pytest
import sqlalchemy as sa
from factories import make_item, seed_archive_snapshot
from fakes.clock import FakeClock
from helpers.archive import fixture_rows, snapshot_handler
from sqlalchemy.orm import Session
from test_cli_archive_snapshot_db import (  # noqa: F401 - fixtures autouse reutilizadas
    _frozen_clock,
    _instant_courtesy,
    _serve,
)
from test_cli_archive_snapshot_db import _use_test_database as _use_test_db_snapshot  # noqa: F401
from test_cli_dry_run_no_writes_db import (  # noqa: F401 - fixtures autouse reutilizadas
    ARGS as RUN_NIGHT_ARGS,
)
from test_cli_dry_run_no_writes_db import (
    _fast_waits,  # noqa: F401
    _route,
    _snapshot,
    _via_api,  # noqa: F401
)
from test_domain_solution_tension import RULE, sol

from nocturna import cli
from nocturna.api.app import create_app
from nocturna.api.deps import get_session
from nocturna.application.use_cases.compute_solution_tensions import ComputeSolutionTensions
from nocturna.domain.archive import ArchiveParameterValue as PV
from nocturna.domain.entities import (
    ITEM_SOURCE_EXOPLANET_ARCHIVE,
    ItemStatus,
    MeasuredParameter,
)
from nocturna.domain.solution_tension import SkipReason
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyItemRepository,
    SqlAlchemySolutionTensionRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.exoplanet_archive.mappers import archive_solution_from_ps_row

MASS = MeasuredParameter.MASS
PERIOD = MeasuredParameter.PERIOD
NOW = datetime(2026, 10, 14, 3, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _frozen_solution_clock(monkeypatch: pytest.MonkeyPatch) -> FakeClock:
    clock = FakeClock(NOW)
    monkeypatch.setattr(cli, "system_clock_from_config", lambda config: clock)
    return clock


def _real_solutions():
    return [archive_solution_from_ps_row(row) for row in fixture_rows("ps_t81_planets.csv")]


def _all_rows(factory) -> list:
    with unit_of_work(factory) as session:
        return SqlAlchemySolutionTensionRepository(session).all()


# --- fixture real: HD 202206 c ------------------------------------------------------


def test_hd_202206_c_real_masa_y_periodo_frente_a_benedict_y_harrison_2017(
    db_session_factory, capsys
):
    seed_archive_snapshot(db_session_factory, _real_solutions())

    assert cli.main(["solution-tensions"]) == 0

    rows = {t.parameter: t for t in _all_rows(db_session_factory)}
    assert set(rows) == {MASS, PERIOD}  # la fixture real no trae radio utilizable
    for tension in rows.values():
        assert tension.planet_name == "HD 202206 c"
        assert "Cao" in tension.new.reference
        assert "Benedict" in tension.reference.reference and "2017" in tension.reference.reference
        assert tension.skip_reason is None
        assert tension.item_id is not None
    assert rows[MASS].sigma == pytest.approx(8.2, abs=0.05)
    assert rows[PERIOD].sigma == pytest.approx(3.46, abs=0.05)
    assert rows[MASS].reference_role.value == "most_recent"
    # La solucion Msini/sin(i) de Correia 2005 y Msini de Gozdziewski quedan fuera de masa.
    assert all(c.solution.parameter == MASS for c in rows[MASS].others)
    assert not any("Correia" in c.solution.reference for c in rows[MASS].others)

    with db_session_factory() as session:
        items = session.execute(
            sa.text("select source, title, status, categories from items")
        ).all()
    assert len(items) == 1
    assert items[0].source == ITEM_SOURCE_EXOPLANET_ARCHIVE
    assert items[0].title.startswith("HD 202206 c: ")
    assert str(items[0].status).lower().endswith(ItemStatus.NEW.value)


def test_con_toda_la_fixture_real_solo_hay_una_alta_desde_since(db_session_factory, capsys):
    seed_archive_snapshot(db_session_factory, _real_solutions())
    assert cli.main(["solution-tensions", "--dry-run"]) == 0
    out = capsys.readouterr().out
    line = out.split("altas Published Confirmed activas desde", 1)[1].split("\n")[0]
    assert line.endswith(": 1")


# --- since: frontera exacta en SQL --------------------------------------------------


def _planet(name: str, release: date):
    alta = sol(
        pl_name=name,
        ref_key=f"ALTA-{name}",
        releasedate=release,
        pl_pubdate=f"{release.year}-{release.month:02d}",
    )
    ref = sol(
        pl_name=name,
        ref_key=f"REF-{name}",
        releasedate=date(2017, 2, 1),
        pl_pubdate="2017-03",
        is_default=False,
        mass=PV(5689.0, 922.0, -572.0, 0),
        period=PV(1260.0, 11.0, -11.0, 0),
    )
    return [alta, ref]


def test_since_en_sql_incluye_el_dia_exacto_y_excluye_el_anterior(db_session_factory):
    seed_archive_snapshot(
        db_session_factory,
        _planet("Frontera b", RULE.since) + _planet("Antes b", date(2026, 7, 31)),
    )
    assert cli.main(["solution-tensions"]) == 0
    assert {t.planet_name for t in _all_rows(db_session_factory)} == {"Frontera b"}


def test_avalancha_de_soluciones_antiguas_en_la_base_no_genera_filas(db_session_factory, capsys):
    solutions = []
    for n in range(150):
        solutions += _planet(f"P{n:03d} b", date(2019, 1 + n % 12, 1))
    seed_archive_snapshot(db_session_factory, solutions)

    assert cli.main(["solution-tensions"]) == 0

    assert _all_rows(db_session_factory) == []
    with db_session_factory() as session:
        assert session.execute(sa.text("select count(*) from items")).scalar_one() == 0


# --- dry-run: ninguna tabla cambia -------------------------------------------------


def test_dry_run_del_caso_de_uso_y_del_cli_no_cambia_ninguna_tabla(db_session_factory):
    seed_archive_snapshot(db_session_factory, _real_solutions())
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyItemRepository(session).add_many([make_item(external_id="2601.00077")])
    before = _snapshot(db_session_factory)

    assert cli.main(["solution-tensions", "--dry-run"]) == 0
    assert _snapshot(db_session_factory) == before

    with unit_of_work(db_session_factory, commit=False) as session:
        use_case = cli.compute_solution_tensions_from_config(
            cli.load_pipeline_config(), session, FakeClock(NOW)
        )
        report = use_case(dry_run=True)
    assert isinstance(use_case, ComputeSolutionTensions)
    assert len(report.computed) == 2 and report.items_created == 1
    assert _snapshot(db_session_factory) == before


def test_run_night_dry_run_lista_elegibles_y_excluidas_sin_escribir(
    monkeypatch, db_session_factory, capsys
):
    antigua = sol(
        pl_name="Vieja b",
        ref_key="ALTA-Vieja",
        releasedate=date(2026, 9, 1),
        pl_pubdate="2019-01",
    )
    ref_vieja = sol(
        pl_name="Vieja b",
        ref_key="REF-Vieja",
        is_default=False,
        releasedate=date(2017, 2, 1),
        pl_pubdate="2017-03",
        mass=PV(5689.0, 922.0, -572.0, 0),
        period=PV(1260.0, 11.0, -11.0, 0),
    )
    seed_archive_snapshot(db_session_factory, [*_real_solutions(), antigua, ref_vieja])
    before = _snapshot(db_session_factory)
    _route(monkeypatch)

    code = cli.main(RUN_NIGHT_ARGS)

    out = capsys.readouterr().out
    assert code == 0
    section = out.split("Tensiones entre soluciones:", 1)[1]
    assert "elegibles:" in section and "excluidas:" in section
    assert "HD 202206 c · mass" in section and "HD 202206 c · period" in section
    assert "Vieja b · mass" in section and SkipReason.OLD_PAPER.value in section
    assert "[dry-run: no se escribió nada]" in section
    assert _snapshot(db_session_factory) == before


# --- archive-snapshot: paso nuevo, código 3 ----------------------------------------


def test_archive_snapshot_calcula_las_tensiones_tras_el_snapshot(
    monkeypatch, db_session_factory, capsys
):
    _serve(monkeypatch, snapshot_handler())

    assert cli.main(["archive-snapshot"]) == 0

    out = capsys.readouterr().out
    assert "Tensiones entre soluciones del NASA Exoplanet Archive" in out
    names = {t.planet_name for t in _all_rows(db_session_factory)}
    assert "HD 202206 c" in names


def test_archive_snapshot_sale_con_3_si_el_paso_nuevo_falla_y_conserva_el_snapshot(
    monkeypatch, db_session_factory, capsys
):
    _serve(monkeypatch, snapshot_handler())

    def _boom(*args: object, **kwargs: object) -> object:
        raise RuntimeError("fallo simulado del paso nuevo")

    monkeypatch.setattr(cli, "compute_solution_tensions_from_config", _boom)

    code = cli.main(["archive-snapshot"])

    captured = capsys.readouterr()
    assert code == 3
    assert "snapshot guardado" in captured.err and "tensiones entre soluciones" in captured.err
    with db_session_factory() as session:
        snapshots = session.execute(sa.text("select count(*) from archive_snapshot")).scalar_one()
        solutions = session.execute(sa.text("select count(*) from archive_solution")).scalar_one()
        tensions = session.execute(sa.text("select count(*) from solution_tension")).scalar_one()
    assert snapshots == 1 and solutions > 0
    assert tensions == 0


def test_archive_snapshot_dry_run_no_calcula_tensiones(monkeypatch, db_session_factory):
    _serve(monkeypatch, snapshot_handler())
    assert cli.main(["archive-snapshot", "--dry-run"]) == 0
    with db_session_factory() as session:
        assert session.execute(sa.text("select count(*) from solution_tension")).scalar_one() == 0


def test_solution_tensions_nunca_devuelve_3(monkeypatch, db_session_factory, capsys):
    def _boom(*args: object, **kwargs: object) -> object:
        raise cli.InvariantViolation("dato roto")

    monkeypatch.setattr(cli, "compute_solution_tensions_from_config", _boom)
    code = cli.main(["solution-tensions"])
    assert code == 1
    assert "solution-tensions falló" in capsys.readouterr().err


# --- guarda de gasto: next_unread --------------------------------------------------


def test_next_unread_nunca_devuelve_items_del_archivo_aunque_vayan_antes_y_con_match(
    db_session_factory,
):
    def _at(minute: int) -> datetime:
        return datetime(2026, 10, 14, 0, minute, tzinfo=UTC)

    archive_first = make_item(
        source=ITEM_SOURCE_EXOPLANET_ARCHIVE,
        external_id="a" * 64,
        exoplanet_match=True,
        fetched_at=_at(0),
    )
    archive_second = make_item(
        source=ITEM_SOURCE_EXOPLANET_ARCHIVE,
        external_id="b" * 64,
        exoplanet_match=True,
        fetched_at=_at(1),
    )
    arxiv_old = make_item(external_id="2601.00001", fetched_at=_at(30))
    arxiv_match = make_item(external_id="2601.00002", exoplanet_match=True, fetched_at=_at(40))
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyItemRepository(session).add_many(
            [archive_first, archive_second, arxiv_old, arxiv_match]
        )

    with unit_of_work(db_session_factory, commit=False) as session:
        repo = SqlAlchemyItemRepository(session)
        for limit in (1, 2, 3, 100):
            got = repo.next_unread(limit)
            assert {i.source for i in got} == {"arxiv"}
            assert [i.id for i in got] == [arxiv_match.id, arxiv_old.id][:limit]


# --- API: el tipo aun no se expone -------------------------------------------------


@pytest.fixture
async def client(db_session: Session) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = create_app()

    def _override() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.mark.anyio
async def test_findings_con_type_solution_tension_devuelve_422(client: httpx.AsyncClient):
    response = await client.get("/findings", params={"type": "solution_tension"})
    assert response.status_code == 422
    body = response.text.lower()
    assert "sqlalchemy" not in body and "psycopg" not in body and "traceback" not in body


@pytest.mark.anyio
@pytest.mark.parametrize(
    "type_", ["paper_explained", "catalog_tension", "primera_medida", "confirmacion_independiente"]
)
async def test_los_cuatro_tipos_vigentes_siguen_aceptados(client: httpx.AsyncClient, type_: str):
    response = await client.get("/findings", params={"type": type_})
    assert response.status_code == 200
