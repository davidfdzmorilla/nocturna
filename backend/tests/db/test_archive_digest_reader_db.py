"""Pérdida de default en `save_snapshot` y lector del resumen semanal (T84), contra PostgreSQL.

Complementa `test_archive_digest_db.py` (humo) sin repetirlo.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
import sqlalchemy as sa
from test_archive_repository import count, save, snapshot, solution

from nocturna.application.use_cases.archive_digest import GetWeeklyDigest, ListDigestWeeks
from nocturna.domain.archive import SnapshotKind
from nocturna.domain.archive_digest import TransitionKind
from nocturna.infrastructure.db import repositories as repositories_module
from nocturna.infrastructure.db.models import (
    ArchiveDefaultChangeRow,
    ArchiveSnapshotRow,
    ArchiveSolutionRow,
)
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveDigestReader,
    SqlAlchemyArchiveRepository,
)
from nocturna.infrastructure.db.session import unit_of_work

_TZ = ZoneInfo("Europe/Madrid")
_WIDE = (datetime(2000, 1, 1, tzinfo=UTC), datetime(2100, 1, 1, tzinfo=UTC))


def _inc(offset_days: int):
    return snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=offset_days)


def _at(snap, when: datetime):
    return replace(snap, taken_at=when)


def _rows(session):
    return session.execute(sa.select(ArchiveDefaultChangeRow)).scalars().all()


def _transitions(session, start=_WIDE[0], end=_WIDE[1]):
    return SqlAlchemyArchiveDigestReader(session).transitions_between(start, end)


# --- save_snapshot: fila de pérdida --------------------------------------


def test_la_fila_de_perdida_lleva_la_clave_del_default_vigente_y_no_la_de_otra_solucion(
    db_session,
):
    repo = SqlAlchemyArchiveRepository(db_session)
    default = solution("G b", "R1", is_default=True, mass=1.0)
    other = solution("G b", "R0", mass=2.0)
    save(repo, snapshot(), [other, default])
    s2 = _inc(1)
    save(repo, s2, [], removal_scope=frozenset({"G b"}))

    (lost,) = _rows(db_session)
    assert lost.pl_name == "G b"
    assert lost.old_solution_key == default.solution_key
    assert lost.new_solution_key is None
    assert lost.snapshot_id == s2.id and lost.detected_at == s2.taken_at


def test_snapshot_sin_perdidas_no_deja_filas_nuevas(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    a = solution("A b", "R1", is_default=True)
    save(repo, snapshot(), [a])
    save(repo, _inc(1), [a], removal_scope=frozenset({"A b"}))
    assert count(db_session, ArchiveDefaultChangeRow) == 0

    # un cambio de default deja una fila, pero no de pérdida (new no nulo)
    a2 = solution("A b", "R2", is_default=True, mass=9.0)
    save(repo, _inc(2), [a2], removal_scope=frozenset({"A b"}))
    (row,) = _rows(db_session)
    assert row.old_solution_key == a.solution_key and row.new_solution_key == a2.solution_key

    # un snapshot posterior sin cambios no añade nada
    save(repo, _inc(3), [a2], removal_scope=frozenset({"A b"}))
    assert count(db_session, ArchiveDefaultChangeRow) == 1


def test_un_planeta_ya_perdido_no_genera_otra_fila_de_perdida_en_el_snapshot_siguiente(
    db_session,
):
    repo = SqlAlchemyArchiveRepository(db_session)
    save(repo, snapshot(), [solution("G b", "R1", is_default=True)])
    save(repo, _inc(1), [], removal_scope=frozenset({"G b"}))
    save(repo, _inc(2), [], removal_scope=frozenset({"G b"}))
    assert count(db_session, ArchiveDefaultChangeRow) == 1


def test_perdida_fuera_del_alcance_no_deja_fila(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    save(repo, snapshot(), [solution("G b", "R1", is_default=True)])
    save(repo, _inc(1), [], removal_scope=frozenset({"otro b"}))
    assert count(db_session, ArchiveDefaultChangeRow) == 0


def _counts(session):
    return (
        count(session, ArchiveSnapshotRow),
        count(session, ArchiveSolutionRow),
        count(session, ArchiveDefaultChangeRow),
    )


def test_atomicidad_si_falla_despues_de_guardar_no_queda_nada(db_session_factory):
    g = solution("G b", "R1", is_default=True)
    with unit_of_work(db_session_factory) as s:
        save(SqlAlchemyArchiveRepository(s), snapshot(), [g])
    with unit_of_work(db_session_factory, commit=False) as s:
        before = _counts(s)
    assert before == (1, 1, 0)

    class Boom(Exception):
        pass

    with pytest.raises(Boom):
        with unit_of_work(db_session_factory) as s:
            save(SqlAlchemyArchiveRepository(s), _inc(1), [], removal_scope=frozenset({"G b"}))
            assert count(s, ArchiveDefaultChangeRow) == 1  # visible dentro de la transacción
            raise Boom

    with unit_of_work(db_session_factory, commit=False) as s:
        assert _counts(s) == before
        # y el default vigente sigue siendo el de antes
        assert SqlAlchemyArchiveRepository(s).current_defaults() == {"G b": g.solution_key}


def test_atomicidad_si_falla_la_escritura_de_la_fila_de_perdida_no_queda_ni_el_snapshot(
    db_session_factory, monkeypatch
):
    with unit_of_work(db_session_factory) as s:
        save(SqlAlchemyArchiveRepository(s), snapshot(), [solution("G b", "R1", is_default=True)])

    def _boom(*args, **kwargs):
        raise RuntimeError("fallo al escribir la pérdida")

    monkeypatch.setattr(repositories_module, "archive_lost_default_to_row", _boom)
    with pytest.raises(RuntimeError, match="pérdida"):
        with unit_of_work(db_session_factory) as s:
            save(SqlAlchemyArchiveRepository(s), _inc(1), [], removal_scope=frozenset({"G b"}))

    with unit_of_work(db_session_factory, commit=False) as s:
        assert _counts(s) == (1, 1, 0)


# --- lector: transitions_between / planet_seen_before --------------------


def test_planet_seen_before_es_falso_en_el_alta_y_verdadero_al_recuperar(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    x = solution("X b", "R1", is_default=True)
    anchor = solution("Z b", "R1", is_default=True)
    save(repo, snapshot(), [x, anchor])  # snapshot 1
    save(repo, _inc(1), [anchor], removal_scope=frozenset({"X b"}))  # X pierde el default
    n = solution("N b", "R1", is_default=True)
    save(repo, _inc(2), [anchor, x, n], removal_scope=frozenset({"X b", "N b"}))  # X vuelve, N nace

    by_key = {(t.pl_name, t.new_key is None): t for t in _transitions(db_session)}
    assert set(by_key) == {("X b", True), ("X b", False), ("N b", False)}
    assert by_key[("X b", True)].planet_seen_before is True  # pérdida
    regained = by_key[("X b", False)]
    assert regained.old_key is None and regained.new_key == x.solution_key
    assert regained.planet_seen_before is True
    new = by_key[("N b", False)]
    assert new.old_key is None and new.planet_seen_before is False


def test_planet_seen_before_no_cuenta_el_propio_snapshot_ni_los_posteriores(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    save(repo, snapshot(), [solution("A b", "R1", is_default=True)])
    save(repo, _inc(1), [solution("A b", "R1", is_default=True)], removal_scope=frozenset())
    n = solution("N b", "R1", is_default=True)
    save(repo, _inc(2), [solution("A b", "R1", is_default=True), n], removal_scope=frozenset())
    # N sigue visible en un snapshot posterior: el alta del 2 sigue siendo "nueva"
    save(repo, _inc(3), [solution("A b", "R1", is_default=True), n], removal_scope=frozenset())
    (t,) = [t for t in _transitions(db_session) if t.pl_name == "N b"]
    assert t.planet_seen_before is False


def test_renombrado_sale_como_planeta_nuevo_mas_perdida_del_viejo(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    other = solution("Z b", "R1", is_default=True)
    save(repo, snapshot(), [solution("Old b", "R1", is_default=True), other])
    save(
        repo,
        _inc(1),
        [solution("New b", "R1", is_default=True), other],
        removal_scope=frozenset({"Old b", "New b"}),
    )
    digest = GetWeeklyDigest(SqlAlchemyArchiveDigestReader(db_session), _TZ)("2026-W40")
    assert digest is not None
    assert [(e.kind, e.pl_name) for e in digest.entries] == [
        (TransitionKind.NEW_PLANET, "New b"),
        (TransitionKind.LOST, "Old b"),
    ]


def test_regained_con_la_misma_solucion_reactivada_y_con_una_nueva(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    r = solution("R b", "R1", is_default=True)
    s = solution("S b", "R1", is_default=True)
    anchor = solution("Z b", "R1", is_default=True)  # evita el caso "sin defaults previos"
    save(repo, snapshot(), [r, s, anchor])
    save(repo, _inc(1), [anchor], removal_scope=frozenset({"R b", "S b"}))
    s_new = solution("S b", "R2", is_default=True, mass=7.0)
    save(repo, _inc(8), [r, s_new, anchor], removal_scope=frozenset({"R b", "S b"}))

    reader = SqlAlchemyArchiveDigestReader(db_session)
    digest = GetWeeklyDigest(reader, _TZ)("2026-W41")
    assert digest is not None
    assert [(e.kind, e.pl_name) for e in digest.entries] == [
        (TransitionKind.REGAINED, "R b"),
        (TransitionKind.REGAINED, "S b"),
    ]
    assert digest.entries[0].new == r and digest.entries[1].new == s_new
    assert all(e.old is None and e.parameter_changes == () for e in digest.entries)
    week40 = GetWeeklyDigest(reader, _TZ)("2026-W40")
    assert week40 is not None and [e.kind for e in week40.entries] == [
        TransitionKind.LOST,
        TransitionKind.LOST,
    ]


def test_semana_sin_cambios_existe_con_cero_entradas(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    a = solution("A b", "R1", is_default=True)
    save(repo, snapshot(), [a])
    save(repo, _inc(8), [a], removal_scope=frozenset({"A b"}))

    reader = SqlAlchemyArchiveDigestReader(db_session)
    digest = GetWeeklyDigest(reader, _TZ)("2026-W41")
    assert digest is not None and digest.snapshots == 1 and digest.entries == ()
    weeks = ListDigestWeeks(reader, _TZ)()
    assert [(w.week, w.snapshots, sum(w.counts.values())) for w in weeks] == [
        ("2026-W41", 1, 0),
        ("2026-W40", 1, 0),
    ]


def test_semana_con_dos_snapshots_junta_las_transiciones_de_ambos(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    a = solution("A b", "R1", is_default=True, mass=1.0)
    g = solution("G b", "R1", is_default=True)
    save(repo, snapshot(), [a, g])  # jueves 1-oct (W40)
    a2 = solution("A b", "R2", is_default=True, mass=2.0)
    save(repo, _inc(1), [a2, g], removal_scope=frozenset({"A b"}))  # viernes (W40)
    n = solution("N b", "R1", is_default=True)
    save(repo, _inc(2), [a2, n], removal_scope=frozenset({"G b", "N b"}))  # sábado (W40)
    save(repo, _inc(9), [a2, n], removal_scope=frozenset())  # W41, sin cambios

    reader = SqlAlchemyArchiveDigestReader(db_session)
    assert reader.snapshot_weeks(_TZ) == [("2026-W40", 3), ("2026-W41", 1)]
    digest = GetWeeklyDigest(reader, _TZ)("2026-W40")
    assert digest is not None and digest.snapshots == 3
    assert [(e.kind, e.pl_name) for e in digest.entries] == [
        (TransitionKind.CHANGED, "A b"),
        (TransitionKind.NEW_PLANET, "N b"),
        (TransitionKind.LOST, "G b"),
    ]
    assert digest.entries[0].snapshot_taken_at.day == 2
    assert digest.entries[2].snapshot_taken_at.day == 3
    assert all(e.snapshot_taken_at.tzinfo is not None for e in digest.entries)


def test_el_limite_de_semana_usa_la_zona_local_en_el_cambio_de_hora(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    a = solution("A b", "R1", is_default=True, mass=1.0)
    first = _at(snapshot(), datetime(2026, 10, 19, 12, tzinfo=UTC))
    save(repo, first, [a])
    a2 = solution("A b", "R2", is_default=True, mass=2.0)
    sunday = _at(_inc(0), datetime(2026, 10, 25, 22, 59, tzinfo=UTC))  # 23:59 CET, W43
    save(repo, sunday, [a2], removal_scope=frozenset({"A b"}))
    a3 = solution("A b", "R3", is_default=True, mass=3.0)
    monday = _at(_inc(0), datetime(2026, 10, 25, 23, 0, tzinfo=UTC))  # 00:00 CET, W44
    save(repo, monday, [a3], removal_scope=frozenset({"A b"}))

    reader = SqlAlchemyArchiveDigestReader(db_session)
    assert reader.snapshot_weeks(_TZ) == [("2026-W43", 2), ("2026-W44", 1)]
    w43 = GetWeeklyDigest(reader, _TZ)("2026-W43")
    w44 = GetWeeklyDigest(reader, _TZ)("2026-W44")
    assert w43 is not None and w44 is not None
    assert [e.new for e in w43.entries] == [a2]
    assert [e.new for e in w44.entries] == [a3]


def test_solutions_by_key_incluye_dadas_de_baja_y_omite_desconocidas(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    keep = solution("A b", "R1", is_default=True)
    gone = solution("A b", "R2", mass=5.0)
    save(repo, snapshot(), [keep, gone])
    save(repo, _inc(1), [keep], removal_scope=frozenset({"A b"}))
    reader = SqlAlchemyArchiveDigestReader(db_session)
    got = reader.solutions_by_key([keep.solution_key, gone.solution_key, "0" * 64])
    assert got == {keep.solution_key: keep, gone.solution_key: gone}
    assert reader.solutions_by_key([]) == {}


def test_lector_sin_snapshots(db_session):
    reader = SqlAlchemyArchiveDigestReader(db_session)
    assert reader.snapshot_weeks(_TZ) == []
    assert ListDigestWeeks(reader, _TZ)() == []
    assert GetWeeklyDigest(reader, _TZ)("2026-W40") is None
