"""`nocturna archive-digest` de punta a punta contra `nocturna_test` (T84).

Base sembrada con commits reales (la CLI abre su propia sesión). Sin red ni Claude.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa
from test_archive_repository import save, snapshot, solution

from nocturna import cli
from nocturna.domain.archive import SnapshotKind
from nocturna.infrastructure.db.repositories import SqlAlchemyArchiveRepository
from nocturna.infrastructure.db.session import unit_of_work

_WEEK = "2026-W40"


@pytest.fixture(autouse=True)
def _use_test_database(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    assert test_database_url.rsplit("/", 1)[-1] == "nocturna_test"
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)


def _all_counts(factory) -> dict[str, int]:
    with factory() as session:
        tables = session.execute(
            sa.text("select tablename from pg_tables where schemaname = 'public'")
        ).scalars()
        return {
            t: session.execute(sa.text(f'select count(*) from "{t}"')).scalar_one()
            for t in sorted(tables)
        }


def _seed(factory) -> None:
    with unit_of_work(factory) as session:
        repo = SqlAlchemyArchiveRepository(session)
        a = solution("A b", "Ref A", is_default=True, mass=1.0)
        gone = solution("G b", "Ref G", is_default=True)
        save(repo, snapshot(rows=2), [a, gone])
        a2 = solution("A b", "Ref A2", is_default=True, mass=3.0)
        n = solution("N b", "Ref N", is_default=True)
        save(
            repo,
            snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=1),
            [a2, n],
            removal_scope=frozenset({"A b", "N b", "G b"}),
        )


def test_imprime_las_secciones_con_parametros_y_unidades(db_session_factory, capsys):
    _seed(db_session_factory)

    code = cli.main(["archive-digest", "--week", _WEEK])

    captured = capsys.readouterr()
    out = captured.out
    assert code == 0 and captured.err == ""
    assert f"Resumen semanal del NASA Exoplanet Archive {_WEEK}" in out
    assert "2 snapshot(s), 3 transición(es)" in out
    assert "Referencia cambiada: 1" in out
    assert "Planetas nuevos: 1" in out
    assert "Planetas recuperados: 0" in out
    assert "Planetas que pierden la referencia: 1" in out
    # cambio con parámetro y unidad
    assert "A b (2026-10-02)" in out
    assert "anterior: Ref A" in out and "nueva:    Ref A2" in out
    mass_line = next(line for line in out.splitlines() if line.strip().startswith("masa:"))
    assert mass_line.strip() == "masa: 1,0 +0,1 / −0,2 M⊕ -> 3,0 +0,1 / −0,2 M⊕"
    # planeta nuevo en una línea
    assert any(line.strip().startswith("N b (2026-10-02): Ref N") for line in out.splitlines())
    # pérdida con la referencia anterior y sin "nueva"
    lost_block = out.split("Planetas que pierden la referencia: 1")[1]
    assert "G b (2026-10-02)" in lost_block and "anterior: Ref G" in lost_block
    assert "nueva:" not in lost_block
    assert "solution_key" not in out and "Traceback" not in out


def test_no_escribe_nada_en_ninguna_tabla(db_session_factory, capsys):
    _seed(db_session_factory)
    before = _all_counts(db_session_factory)
    assert before["archive_default_change"] == 3  # cambio + planeta nuevo + pérdida
    assert cli.main(["archive-digest", "--week", _WEEK]) == 0
    assert cli.main(["archive-digest"]) == 0
    assert cli.main(["archive-digest", "--week", "2026-W20"]) == 1
    capsys.readouterr()
    assert _all_counts(db_session_factory) == before


def test_sin_week_usa_la_ultima_semana_con_snapshot(db_session_factory, capsys):
    _seed(db_session_factory)
    assert cli.main(["archive-digest"]) == 0
    assert f"Archive {_WEEK}:" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["basura", "2026-41", "2026-W5", "2027-W53", "2026-W00"])
def test_formato_de_semana_invalido_sale_con_codigo_2(db_session_factory, capsys, bad):
    _seed(db_session_factory)
    before = _all_counts(db_session_factory)
    with pytest.raises(SystemExit) as exc:
        cli.main(["archive-digest", "--week", bad])
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "--week" in err and "Traceback" not in err
    assert _all_counts(db_session_factory) == before


def test_semana_sin_snapshot_sale_con_codigo_1_y_mensaje_en_stderr(db_session_factory, capsys):
    _seed(db_session_factory)
    assert cli.main(["archive-digest", "--week", "2026-W20"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "2026-W20" in captured.err and "Traceback" not in captured.err


def test_base_sin_snapshots_sale_con_codigo_1(db_session_factory, capsys):
    assert cli.main(["archive-digest"]) == 1
    captured = capsys.readouterr()
    assert captured.out == "" and "no hay ningún snapshot" in captured.err


def test_la_fecha_del_snapshot_usa_la_zona_horaria_de_la_configuracion(db_session_factory, capsys):
    # 23:30 UTC del domingo 4 de octubre = lunes 5 a las 01:30 en Europe/Madrid.
    with unit_of_work(db_session_factory) as session:
        repo = SqlAlchemyArchiveRepository(session)
        save(repo, snapshot(rows=1), [solution("A b", "Ref A", is_default=True)])
        late = replace(
            snapshot(kind=SnapshotKind.INCREMENTAL),
            taken_at=datetime(2026, 10, 4, 23, 30, tzinfo=UTC),
        )
        save(
            repo,
            late,
            [solution("A b", "Ref A", is_default=True), solution("N b", "Ref N", is_default=True)],
            removal_scope=frozenset({"A b", "N b"}),
        )

    code = cli.main(["archive-digest", "--week", "2026-W41"])

    out = capsys.readouterr().out
    assert code == 0
    assert "N b (2026-10-05): Ref N" in out
