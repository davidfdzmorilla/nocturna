"""`run-night --dry-run` no escribe nada en la base (T87), contra PostgreSQL.

Sin red: arXiv (feed Atom grabado, vía `api`) y el NASA Exoplanet Archive
(fixtures reales) se sirven por `httpx.MockTransport`, enrutando por host.
Cada test compara una instantanea de `count(*)` de TODAS las tablas del
esquema `public` (incluida `alembic_version`) antes y despues.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest
import sqlalchemy as sa
from factories import (
    make_agent_call,
    make_item,
    make_reading,
    make_run,
    seed_archive_snapshot,
)
from helpers.archive import alias_handler, load_t71c_measurements, v1298_archive_solutions

from nocturna import cli
from nocturna.cli import main
from nocturna.domain.entities import ItemStatus, RunStatus
from nocturna.infrastructure.config import Settings
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

FEED = (
    Path(__file__).resolve().parents[1] / "fixtures" / "arxiv" / "feed_three_entries.xml"
).read_text(encoding="utf-8")
ARCHIVE_HOST = "exoplanetarchive.ipac.caltech.edu"
ARGS = [
    "run-night",
    "--dry-run",
    "--since",
    "2000-01-01",
    "--categories",
    "astro-ph.EP,astro-ph.GA",
]
MARKER = "[dry-run: no se escribió nada en la base]"
PAPER_V1298 = "2609.30038"


@pytest.fixture(autouse=True)
def _via_api(monkeypatch: pytest.MonkeyPatch) -> None:
    real = cli.load_pipeline_config

    def _patched(*args: object, **kwargs: object):
        config = real(*args, **kwargs)
        arxiv = config.sources.arxiv.model_copy(update={"ingest_via": "api"})
        sources = config.sources.model_copy(update={"arxiv": arxiv})
        return config.model_copy(update={"sources": sources})

    monkeypatch.setattr(cli, "load_pipeline_config", _patched)


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    assert test_database_url.rsplit("/", 1)[-1] == "nocturna_test"
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


async def _instant_sleep(_delay_s: float) -> None:
    return None


@pytest.fixture(autouse=True)
def _fast_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    real_limiter = cli.RateLimiter
    real_policy = cli.arxiv_retry_policy_from_config

    def _fast_limiter(min_interval_s: float, **kwargs: object) -> object:
        kwargs.setdefault("sleep", _instant_sleep)
        return real_limiter(min_interval_s, **kwargs)

    def _fast_policy(config: object):
        policy = real_policy(config)
        return type(policy)(max_attempts=policy.max_attempts, base_delay_s=0.001, max_elapsed_s=1.0)

    monkeypatch.setattr(cli, "RateLimiter", _fast_limiter)
    monkeypatch.setattr(cli, "arxiv_retry_policy_from_config", _fast_policy)


def _route(
    monkeypatch: pytest.MonkeyPatch,
    *,
    feed: str = FEED,
    arxiv_status: int = 200,
    archive: Callable[[httpx.Request], httpx.Response] | None = None,
) -> None:
    archive_fn = archive or alias_handler()

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.host == ARCHIVE_HOST:
            return archive_fn(request)
        if arxiv_status != 200:
            return httpx.Response(arxiv_status, content=b"error")
        return httpx.Response(200, content=feed.encode("utf-8"))

    real_async_client = httpx.AsyncClient

    def _fake(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(handle)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _fake)


def _feed_with_only(*external_ids: str) -> str:
    """El feed de tres entradas reducido a las entradas pedidas."""
    head, *rest = re.split(r"(?=<entry>)", FEED)
    last = rest[-1]
    tail = last[last.index("</entry>") + len("</entry>") :]
    rest[-1] = last[: last.index("</entry>") + len("</entry>")]
    kept = [e for e in rest if any(i in e for i in external_ids)]
    return head + "".join(kept) + tail


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
        assert "alembic_version" in tables and "items" in tables
        snap: dict[str, object] = {
            t: session.execute(sa.text(f'select count(*) from public."{t}"')).scalar_one()
            for t in tables
        }
        snap["alembic_version.version_num"] = session.execute(
            sa.text("select version_num from alembic_version")
        ).scalar_one()
        return snap


def _item_row(factory, external_id: str):
    with factory() as session:
        row = session.execute(
            sa.text("select * from items where external_id = :e"), {"e": external_id}
        ).one()
        return tuple(row)


def _seed_base(factory) -> None:
    """Dos ítems previos (uno duplicado del feed), una Reading con medidas de
    V1298 Tau y un Run cerrado."""
    measurements = load_t71c_measurements("2609.30038.reader-measures-exp1.derived-fullname.json")
    seed_archive_snapshot(factory, v1298_archive_solutions())
    with unit_of_work(factory) as session:
        dup = make_item(external_id="2609.17526", title="Duplicado previo")
        other = make_item(external_id="2601.00002", title="Otro previo")
        v1298 = make_item(external_id=PAPER_V1298, status=ItemStatus.READ)
        SqlAlchemyItemRepository(session).add_many([dup, other, v1298])
        session.flush()
        SqlAlchemyReadingRepository(session).add(make_reading(v1298.id, measurements=measurements))
        runs = SqlAlchemyRunRepository(session)
        run = make_run(status=RunStatus.COMPLETED, finished_at=make_run().started_at)
        runs.add(run)
        session.flush()
        SqlAlchemyAgentCallRepository(session).add(make_agent_call(run.id, item_id=v1298.id))


def test_dry_run_no_cambia_ninguna_tabla_ni_la_version_de_alembic(
    monkeypatch, db_session_factory, capsys
):
    _seed_base(db_session_factory)
    before = _snapshot(db_session_factory)
    dup_before = _item_row(db_session_factory, "2609.17526")
    _route(monkeypatch)

    code = main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    assert _snapshot(db_session_factory) == before
    assert _item_row(db_session_factory, "2609.17526") == dup_before
    with db_session_factory() as session:
        runs = session.execute(sa.text("select count(*) from runs")).scalar_one()
    assert runs == 1  # solo el Run cerrado sembrado
    assert "fetched=3 new=2 duplicates=1 skipped=0" in out
    assert MARKER in out


def test_dos_dry_run_seguidos_dan_el_mismo_new(monkeypatch, db_session_factory, capsys):
    _route(monkeypatch)

    assert main(ARGS) == 0
    first = capsys.readouterr().out
    assert main(ARGS) == 0
    second = capsys.readouterr().out

    assert "fetched=3 new=3 duplicates=0 skipped=0" in first
    assert "fetched=3 new=3 duplicates=0 skipped=0" in second


def test_el_plan_cuenta_los_items_nuevos_nunca_guardados_y_les_da_prioridad(
    monkeypatch, db_session_factory, capsys
):
    """`max_items_per_night=2`, 3 ítems previos `new` sin marca y 1 ítem EP que
    casa el filtro (2609.17505) llegado en el feed: entra con prioridad."""
    real = cli.load_pipeline_config

    def _limited(*args: object, **kwargs: object):
        config = real(*args, **kwargs)
        limits = config.limits.model_copy(update={"max_items_per_night": 2})
        return config.model_copy(update={"limits": limits})

    monkeypatch.setattr(cli, "load_pipeline_config", _limited)
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyItemRepository(session).add_many(
            [make_item(external_id=f"2601.0000{n}") for n in (1, 2, 3)]
        )
    before = _snapshot(db_session_factory)
    _route(monkeypatch, feed=_feed_with_only("2609.17505"))

    code = main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    assert "fetched=1 new=1 duplicates=0 skipped=0" in out
    assert "ítems que se leerían esta noche: 2" in out
    assert "de los 2 a leer, 1 con reader-v3 y 1 con reader-v2" in out
    assert _snapshot(db_session_factory) == before


def test_si_el_alias_del_archivo_cae_devuelve_1_y_la_base_queda_intacta(
    monkeypatch, db_session_factory, capsys
):
    """T88: soluciones e índice salen de la base; el archivo solo se consulta
    para el alias de un planeta que el índice local no conoce."""
    _seed_base(db_session_factory)
    with unit_of_work(db_session_factory) as session:
        odd = make_item(external_id="2609.99999", status=ItemStatus.READ)
        SqlAlchemyItemRepository(session).add_many([odd])
        session.flush()
        odd_measure = load_t71c_measurements(
            "2609.30038.reader-measures-exp1.derived-fullname.json"
        )[0]
        odd_measure = dataclasses.replace(odd_measure, planet_name="Planeta Raro b")
        SqlAlchemyReadingRepository(session).add(make_reading(odd.id, measurements=(odd_measure,)))
    before = _snapshot(db_session_factory)
    _route(monkeypatch, archive=lambda request: httpx.Response(503, text="mantenimiento"))

    code = main(ARGS)

    captured = capsys.readouterr()
    assert code == 1
    assert "error consultando el NASA Exoplanet Archive" in captured.err
    assert MARKER not in captured.out
    assert _snapshot(db_session_factory) == before


def test_si_arxiv_cae_devuelve_1_y_la_base_queda_intacta(monkeypatch, db_session_factory, capsys):
    _seed_base(db_session_factory)
    before = _snapshot(db_session_factory)
    _route(monkeypatch, arxiv_status=500)

    code = main(ARGS)

    captured = capsys.readouterr()
    assert code == 1
    assert "arXiv" in captured.err
    assert "Traceback" not in captured.err
    assert _snapshot(db_session_factory) == before


def test_tras_el_dry_run_no_queda_ninguna_conexion_idle_in_transaction(
    monkeypatch, db_session_factory, capsys
):
    _seed_base(db_session_factory)
    _route(monkeypatch)

    assert main(ARGS) == 0

    with db_session_factory() as session:
        stuck = session.execute(
            sa.text(
                "select pid, state from pg_stat_activity "
                "where datname = 'nocturna_test' "
                "and state like 'idle in transaction%' and pid <> pg_backend_pid()"
            )
        ).all()
    assert stuck == []


def test_la_ingesta_de_la_noche_real_sigue_persistiendo(
    monkeypatch, db_session_factory, test_database_url
):
    """Regresión: `_run_ingest` (la que usa la noche real) hace commit; solo
    `--dry-run` deshace."""
    import asyncio
    from datetime import UTC, datetime

    _route(monkeypatch)
    config = cli.load_pipeline_config()

    result = asyncio.run(
        cli._run_ingest(
            since=datetime(2000, 1, 1, tzinfo=UTC),
            categories=["astro-ph.EP", "astro-ph.GA"],
            config=config,
            settings=Settings(database_url=test_database_url),
        )
    )

    assert result.new == 3
    with db_session_factory() as session:
        count = session.execute(sa.text("select count(*) from items")).scalar_one()
    assert count == 3
