"""`scripts/backfill_exoplanet_match.py` contra `nocturna_test` (T79).

El script abre su propio motor con `Settings().database_url`, así que se
fuerza `NOCTURNA_DATABASE_URL` a la base de test (y se comprueba que lo es:
nunca debe tocar la base real `nocturna`). Sin Claude.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from factories import aware, make_item

from nocturna.infrastructure.db.models import ItemRow
from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "backfill_exoplanet_match.py"

_A = "2609.00001"  # casa (TOI-6981 b), marca inicial False
_B = "2609.00002"  # no casa, marca inicial True (obsoleta)
_C = "2609.00003"  # no casa, marca inicial False


@pytest.fixture
def backfill(monkeypatch, test_database_url):
    assert test_database_url.rsplit("/", 1)[-1] == "nocturna_test"
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)
    spec = importlib.util.spec_from_file_location("backfill_exoplanet_match", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def seeded(db_session_factory):
    with db_session_factory() as session:
        SqlAlchemyItemRepository(session).add_many(
            [
                make_item(
                    external_id=_A,
                    title="Mass of TOI-6981 b from radial velocities",
                    abstract="We measure it.",
                    fetched_at=aware(3),
                ),
                make_item(
                    external_id=_B,
                    title="Galaxy clusters",
                    abstract="Halo mass function.",
                    exoplanet_match=True,
                    fetched_at=aware(1),
                ),
                make_item(
                    external_id=_C,
                    title="Stellar ages",
                    abstract="Isochrones.",
                    fetched_at=aware(2),
                ),
            ]
        )
        session.commit()
    return db_session_factory


def _marks(factory) -> dict[str, bool]:
    with factory() as session:
        rows = session.execute(sa.select(ItemRow.external_id, ItemRow.exoplanet_match)).all()
    return {external_id: match for external_id, match in rows}


def test_marca_los_que_casan_y_desmarca_los_obsoletos(backfill, seeded, capsys):
    code = backfill.main([])

    assert code == 0
    assert _marks(seeded) == {_A: True, _B: False, _C: False}
    out = capsys.readouterr().out
    assert "total: 3" in out
    assert "casan: 1" in out
    assert "cambiados: 2" in out


def test_es_idempotente_la_segunda_ejecucion_cambia_cero(backfill, seeded, capsys):
    backfill.main([])
    capsys.readouterr()

    backfill.main([])

    out = capsys.readouterr().out
    assert "cambiados: 0" in out
    assert _marks(seeded) == {_A: True, _B: False, _C: False}


def test_dry_run_no_escribe_pero_informa_de_los_cambios(backfill, seeded, capsys):
    before = _marks(seeded)

    code = backfill.main(["--dry-run"])

    assert code == 0
    assert _marks(seeded) == before == {_A: False, _B: True, _C: False}
    out = capsys.readouterr().out
    assert "cambiados: 2 (dry-run, sin escribir)" in out


def test_imprime_el_top_n_de_next_unread_con_marca_y_variante(backfill, seeded, capsys):
    backfill.main([])

    lines = capsys.readouterr().out.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("primeros "))
    listing = [line.strip() for line in lines[header + 1 :] if line.strip()]
    # El marcado va primero (v3: categoría astro-ph.EP); después fetched_at: C (2) y B (1)... B
    # ya está desmarcado, así que B (fetched 1) precede a C (fetched 2).
    assert [row.split()[0] for row in listing] == [_A, _B, _C]
    assert "match=1" in listing[0]
    assert " v3 " in listing[0]
    assert "Mass of TOI-6981 b" in listing[0]
    assert "match=0" in listing[1]
    assert " v2 " in listing[1]


def test_el_dry_run_tambien_imprime_el_top_n_con_las_marcas_recalculadas(backfill, seeded, capsys):
    backfill.main(["--dry-run"])

    lines = capsys.readouterr().out.splitlines()
    header = next(i for i, line in enumerate(lines) if line.startswith("primeros "))
    first = lines[header + 1].split()[0]
    assert first == _A
