"""T110.a: `phase3-check` contra PostgreSQL (base efimera): lee `Run` reales y solo emite SELECT.

Sin Claude ni red. Los registros de ficheros viven en `tmp_path`.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
import sqlalchemy as sa
from conftest import run_alembic_upgrade
from helpers.phase3 import (
    at,
    night_run,
    perfect_window,
    probe,
    write_delivery_log,
    write_probe_log,
)
from sqlalchemy.engine import Engine

from nocturna import cli
from nocturna.domain.entities import Run, RunStatus
from nocturna.infrastructure.config import Settings
from nocturna.infrastructure.db.repositories import SqlAlchemyRunRepository
from nocturna.infrastructure.db.session import (
    create_db_engine,
    create_session_factory,
    unit_of_work,
)


@pytest.fixture
def window(scratch_database_url, tmp_path, monkeypatch):
    run_alembic_upgrade(scratch_database_url)
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", scratch_database_url)
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("NOCTURNA_LOG_DIR", str(log_dir))
    engine = create_db_engine(Settings(database_url=scratch_database_url))
    factory = create_session_factory(engine)

    def save(*runs: Run) -> None:
        with unit_of_work(factory) as session:
            repo = SqlAlchemyRunRepository(session)
            for run in runs:
                repo.add(run)

    class _Window:
        def __init__(self) -> None:
            self.log_dir = log_dir
            self.save = save

        def runs(self, *extra: Run) -> None:
            _, _, finished = perfect_window(FIRST)
            self.save(
                *(night_run(FIRST + timedelta(days=i), f) for i, f in enumerate(finished)), *extra
            )

        def write(self, *, first_day_footer=None):
            probes, deliveries, _ = perfect_window(FIRST)
            if first_day_footer is not None:
                probes[0] = probe(probes[0].checked_at, footer=first_day_footer)
            write_probe_log(log_dir / "phase3-probes.log", probes)
            write_delivery_log(log_dir / "morning-deliveries.log", deliveries)

    yield _Window()
    engine.dispose()


# Ventana cerrada y en el pasado (acaba el 25-oct, dia del cambio de hora): el reloj es el real.
FIRST = date(2026, 10, 12)
ARGS = ["phase3-check", "--from", FIRST.isoformat()]
FIRST_ROW = "2026-10-12 ok  ok  ok"


def test_lee_los_run_de_noche_de_la_base_y_cumple(window, capsys):
    window.runs()
    window.write()

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 0, out
    assert "estado: cumple" in out
    assert FIRST_ROW in out


def test_un_pie_anterior_al_run_de_la_base_hace_fallar_ese_dia(window, capsys):
    window.runs()
    window.write(first_day_footer=at(FIRST, 3))

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 0  # 13 de 14 en (b): sigue por encima del umbral de 12
    assert FIRST_ROW not in out
    assert "b: pie anterior al último Run" in out


def test_un_run_de_run_item_de_la_base_no_cuenta(window, capsys):
    run_item = Run(started_at=at(FIRST, 4, 40), budget_tokens=300_000)
    run_item.finish(RunStatus.COMPLETED, at(FIRST, 5, 30), None)  # posterior al pie (04:30)
    window.runs(run_item)
    window.write()

    cli.main(ARGS)

    assert FIRST_ROW in capsys.readouterr().out


def test_sin_run_de_noche_en_la_base_los_dias_fallan_en_b(window, capsys):
    window.write()

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 3
    assert "b: sin Run previo" in out


def test_solo_emite_select_y_no_cambia_el_contenido_de_la_base(window, scratch_database_url):
    window.runs()
    window.write()
    statements: list[str] = []

    def spy(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    def snapshot() -> list[tuple]:
        engine = sa.create_engine(scratch_database_url)
        try:
            with engine.connect() as conn:
                return list(
                    conn.execute(sa.text("SELECT id, status, finished_at FROM runs ORDER BY id"))
                )
        finally:
            engine.dispose()

    before = snapshot()
    sa.event.listen(Engine, "before_cursor_execute", spy)
    try:
        code = cli.main(ARGS)
    finally:
        sa.event.remove(Engine, "before_cursor_execute", spy)

    assert code == 0
    assert statements, "el comando debia consultar los Run"
    assert all(s.lstrip().upper().startswith("SELECT") for s in statements), statements
    assert snapshot() == before and len(before) == 14


def test_la_sesion_del_comando_es_de_solo_lectura(window, monkeypatch):
    window.runs()
    window.write()
    seen = {}
    real = cli.create_db_engine

    def spy(settings, **kwargs):
        seen.update(kwargs)
        return real(settings, **kwargs)

    monkeypatch.setattr(cli, "create_db_engine", spy)

    cli.main(ARGS)

    assert seen["read_only"] is True


# --- T110.a ciclo 2: ultimo Run de noche cerrado antes de una fecha -------------------------


@pytest.fixture
def factory(scratch_database_url):
    run_alembic_upgrade(scratch_database_url)
    engine = create_db_engine(Settings(database_url=scratch_database_url))
    yield create_session_factory(engine)
    engine.dispose()


def _run(start, *, notes="ingest=ok | tokens=1", status=RunStatus.COMPLETED) -> Run:
    run = Run(started_at=start, budget_tokens=300_000)
    if status is RunStatus.RUNNING:
        run.notes = notes
    else:
        run.finish(status, start + timedelta(hours=4), notes)
    return run


def _persist(factory, *runs: Run) -> None:
    with unit_of_work(factory) as session:
        repo = SqlAlchemyRunRepository(session)
        for run in runs:
            repo.add(run)


def _last(factory, before, *, via_report_data: bool = False):
    from zoneinfo import ZoneInfo

    from nocturna.infrastructure.db.morning_report_data import SqlMorningReportData

    with unit_of_work(factory) as session:
        if via_report_data:
            data = SqlMorningReportData(session, ZoneInfo("Europe/Madrid"))
            return data.last_night_run_finished_before(before)
        return SqlAlchemyRunRepository(session).last_night_run_finished_before(before)


@pytest.mark.parametrize("via_report_data", [False, True], ids=["repositorio", "morning-data"])
def test_el_limite_es_estricto_y_devuelve_el_mas_reciente(factory, via_report_data):
    old = _run(at(FIRST - timedelta(days=9), 0, 5))
    newer = _run(at(FIRST - timedelta(days=8), 0, 5))
    at_limit = _run(at(FIRST - timedelta(days=7), 0, 5))
    _persist(factory, old, newer, at_limit)

    found = _last(factory, at_limit.started_at, via_report_data=via_report_data)

    assert found is not None and found.id == newer.id  # `<` estricto, orden descendente
    assert _last(factory, old.started_at, via_report_data=via_report_data) is None


def test_no_tiene_limite_inferior_aunque_haya_muchos_run_de_relectura_recientes(factory):
    night = _run(at(FIRST - timedelta(days=60), 0, 5))
    noise = [
        _run(at(FIRST - timedelta(days=40), 1, 0) + timedelta(minutes=i), notes="reread")
        for i in range(60)
    ]
    _persist(factory, night, *noise)

    found = _last(factory, at(FIRST, 0, 0))

    assert found is not None and found.id == night.id


def test_el_filtro_sql_coincide_con_is_night_run(factory):
    from nocturna.domain.night_report import ORPHAN_NOTE, is_night_run

    notes_options = [
        "",
        "reread",
        "ingest=ok | x",
        "ingest=error:Boom",
        ORPHAN_NOTE,
        "otra cosa",
        "xingest=ok",
    ]
    terminal = [RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.KILLED]
    base = at(FIRST - timedelta(days=30), 0, 0)
    runs: list[Run] = []
    for notes in notes_options:
        for status in terminal:
            runs.append(_run(base + timedelta(hours=len(runs)), notes=notes, status=status))
    # Un solo RUNNING (indice unico parcial), con notas de noche, y el ultimo de todos.
    runs.append(_run(base + timedelta(hours=len(runs)), status=RunStatus.RUNNING))
    _persist(factory, *runs)

    for run in runs:
        before = run.started_at + timedelta(minutes=30)
        expected = max(
            (r for r in runs if r.started_at < before and is_night_run(r) and r.finished_at),
            key=lambda r: r.started_at,
            default=None,
        )
        found = _last(factory, before)
        assert (found.id if found else None) == (expected.id if expected else None), run.notes
    # Para una fecha final, la excluida es la abierta (RUNNING): sin finished_at no cuenta.
    last = _last(factory, runs[-1].started_at + timedelta(days=1))
    assert last is not None and last.status is not RunStatus.RUNNING


def test_un_run_de_noche_de_hace_cinco_dias_evita_sin_run_previo_en_b(window, capsys):
    window.save(night_run(FIRST - timedelta(days=5), at(FIRST - timedelta(days=5), 4)))
    window.write()  # nada en N-1 ni dentro de la ventana

    cli.main(ARGS)

    out = capsys.readouterr().out
    assert "sin Run previo" not in out
    assert FIRST_ROW in out
