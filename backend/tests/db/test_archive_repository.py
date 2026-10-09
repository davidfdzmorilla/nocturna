"""`SqlAlchemyArchiveRepository` contra PostgreSQL (T81)."""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.orm import Session

from nocturna.domain.archive import (
    ArchiveParameterValue,
    ArchiveSnapshot,
    ArchiveSolution,
    DefaultChange,
    SnapshotDiff,
    SnapshotKind,
    diff_snapshot,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.infrastructure.db import repositories as repositories_module
from nocturna.infrastructure.db.mappers import archive_solution_from_row
from nocturna.infrastructure.db.models import (
    ArchiveDefaultChangeRow,
    ArchiveSnapshotRow,
    ArchiveSolutionRow,
)
from nocturna.infrastructure.db.repositories import SqlAlchemyArchiveRepository
from nocturna.infrastructure.db.session import unit_of_work

_T0 = datetime(2026, 10, 1, 1, 0, tzinfo=UTC)


def snapshot(
    *, kind: SnapshotKind = SnapshotKind.FULL, offset_days: int = 0, rows: int = 0
) -> ArchiveSnapshot:
    return ArchiveSnapshot(
        id=uuid4(),
        taken_at=_T0 + timedelta(days=offset_days),
        kind=kind,
        max_releasedate=date(2026, 9, 30),
        rows_total=rows,
        defaults_total=0,
        duplicate_rows=0,
        payload_sha256="a" * 64,
        requests=2,
        duration_ms=1234,
    )


def solution(
    pl_name: str = "WASP-12 b",
    ref_key: str = "Smith 2020",
    *,
    mass: float | None = 1.47,
    is_default: bool = False,
    **overrides,
) -> ArchiveSolution:
    fields = {
        "pl_name": pl_name,
        "hostname": "WASP-12",
        "pl_refname": f"<a>{ref_key}</a>",
        "ref_key": ref_key,
        "ref_text": ref_key,
        "arxiv_id": "2001.00001",
        "soltype": "Published Confirmed",
        "releasedate": date(2026, 9, 1),
        "pl_pubdate": "2020-01",
        "is_default": is_default,
        "mass": ArchiveParameterValue(value=mass, err1=0.1, err2=-0.2, lim=0),
        "radius": ArchiveParameterValue(value=1.9, err1=0.05, err2=-0.05, lim=0),
        "period": ArchiveParameterValue(value=1.09, err1=None, err2=None, lim=None),
        "pl_bmassprov": "Mass",
        "st_rad": ArchiveParameterValue(value=1.6, err1=0.1, err2=-0.1),
        "st_mass": ArchiveParameterValue(value=1.4, err1=0.1, err2=-0.1),
        "discoverymethod": "Transit",
        "ttv_flag": None,
        "pl_controv_flag": False,
    }
    fields.update(overrides)
    return ArchiveSolution(**fields)


def save(
    repo: SqlAlchemyArchiveRepository,
    snap: ArchiveSnapshot,
    solutions: list[ArchiveSolution],
    *,
    removal_scope: frozenset[str] | None = None,
) -> SnapshotDiff:
    """Calcula el diff con el dominio contra el estado actual y guarda."""
    diff = diff_snapshot(
        solutions,
        active=repo.active_keys(),
        removed=repo.removed_keys([s.solution_key for s in solutions]),
        previous_defaults=repo.current_defaults(),
        removal_scope=removal_scope,
    )
    repo.save_snapshot(snap, solutions, diff)
    return diff


def row(session: Session, sol: ArchiveSolution) -> ArchiveSolutionRow:
    session.expire_all()
    found = session.get(ArchiveSolutionRow, sol.solution_key)
    assert found is not None
    return found


def count(session: Session, model) -> int:
    return session.execute(sa.select(sa.func.count()).select_from(model)).scalar_one()


def test_snapshots_ida_y_vuelta_y_ultimos(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    assert repo.last_snapshot() is None
    assert repo.last_full_snapshot() is None

    full = snapshot(offset_days=0)
    inc = snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=1)
    save(repo, full, [])
    save(repo, inc, [])

    assert repo.last_snapshot() == inc
    assert repo.last_full_snapshot() == full


def test_solucion_ida_y_vuelta_incluidos_nulos_y_signo_de_errores(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    sol = solution(is_default=True, soltype=None, ttv_flag=None, pl_controv_flag=True)
    save(repo, snapshot(), [sol])

    stored = archive_solution_from_row(row(db_session, sol))
    assert stored == sol
    assert stored.solution_key == sol.solution_key
    assert stored.mass.err2 == -0.2
    assert row(db_session, sol).soltype == ""


def test_primer_snapshot_marca_defaults_vigentes_y_activas(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    default = solution(ref_key="A", is_default=True, mass=1.0)
    other = solution(ref_key="B", mass=2.0)
    b = solution("HD 1 b", "C", is_default=True)
    save(repo, snapshot(), [default, other, b])

    assert repo.current_defaults() == {
        "WASP-12 b": default.solution_key,
        "HD 1 b": b.solution_key,
    }
    assert repo.active_keys() == {
        default.solution_key: "WASP-12 b",
        other.solution_key: "WASP-12 b",
        b.solution_key: "HD 1 b",
    }
    assert repo.active_keys(["HD 1 b"]) == {b.solution_key: "HD 1 b"}
    assert repo.active_keys(["nadie"]) == {}


def test_upsert_actualiza_last_seen_y_descriptivos_y_conserva_first_seen(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    s1 = snapshot()
    s2 = snapshot(offset_days=1)
    original = solution(is_default=True)
    save(repo, s1, [original])

    changed = solution(
        is_default=True,
        releasedate=date(2026, 9, 20),
        pl_pubdate="2026-09",
        arxiv_id="2609.00001",
        discoverymethod="Radial Velocity",
        ttv_flag=True,
        st_rad=ArchiveParameterValue(value=1.7, err1=0.2, err2=-0.2),
    )
    assert changed.solution_key == original.solution_key
    diff = save(repo, s2, [changed])

    stored = row(db_session, original)
    assert (stored.first_seen_snapshot_id, stored.last_seen_snapshot_id) == (s1.id, s2.id)
    assert stored.releasedate == date(2026, 9, 20)
    assert stored.arxiv_id == "2609.00001"
    assert stored.discoverymethod == "Radial Velocity"
    assert stored.ttv_flag is True
    assert stored.st_rad_value == 1.7
    assert diff.added == () and diff.default_changes == ()
    assert count(db_session, ArchiveSolutionRow) == 1


def test_baja_y_reactivacion(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    keep = solution(ref_key="A", is_default=True, mass=1.0)
    gone = solution(ref_key="B", mass=2.0)
    save(repo, snapshot(), [keep, gone])

    s2 = snapshot(offset_days=1)
    diff = save(repo, s2, [keep])
    assert diff.removed == (gone.solution_key,)
    assert row(db_session, gone).removed_at == s2.taken_at
    assert repo.active_keys() == {keep.solution_key: "WASP-12 b"}
    assert repo.removed_keys([gone.solution_key, keep.solution_key]) == {gone.solution_key}
    assert repo.removed_keys([]) == frozenset()

    s3 = snapshot(offset_days=2)
    diff = save(repo, s3, [keep, gone])
    assert diff.reactivated == (gone.solution_key,)
    stored = row(db_session, gone)
    assert stored.removed_at is None
    assert stored.last_seen_snapshot_id == s3.id
    assert repo.removed_keys([gone.solution_key]) == frozenset()
    assert set(repo.active_keys()) == {keep.solution_key, gone.solution_key}


def test_la_baja_de_la_default_limpia_is_default_current(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    only = solution(is_default=True)
    save(repo, snapshot(), [only])
    assert repo.current_defaults() == {"WASP-12 b": only.solution_key}

    diff = save(repo, snapshot(offset_days=1), [])

    assert diff.removed == (only.solution_key,)
    assert diff.lost_defaults == ("WASP-12 b",)
    assert repo.current_defaults() == {}


def test_cambio_de_default_se_recalcula_y_se_registra(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    old = solution(ref_key="A", is_default=True, mass=1.0)
    new = solution(ref_key="B", is_default=False, mass=2.0)
    s1 = snapshot()
    save(repo, s1, [old, new])
    assert count(db_session, ArchiveDefaultChangeRow) == 0  # primer snapshot: sin cambios

    s2 = snapshot(offset_days=1)
    new_default = solution(ref_key="B", is_default=True, mass=2.0)
    old_not_default = solution(ref_key="A", is_default=False, mass=1.0)
    diff = save(repo, s2, [old_not_default, new_default])

    assert diff.default_changes == (DefaultChange("WASP-12 b", old.solution_key, new.solution_key),)
    assert repo.current_defaults() == {"WASP-12 b": new.solution_key}
    assert row(db_session, old).is_default_current is False
    assert row(db_session, old).is_default is False
    changes = db_session.execute(sa.select(ArchiveDefaultChangeRow)).scalars().all()
    assert len(changes) == 1
    change = changes[0]
    assert (change.pl_name, change.old_solution_key, change.new_solution_key) == (
        "WASP-12 b",
        old.solution_key,
        new.solution_key,
    )
    assert change.snapshot_id == s2.id
    assert change.detected_at == s2.taken_at


def test_planeta_nuevo_registra_cambio_con_old_nulo(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    save(repo, snapshot(), [solution(is_default=True)])
    fresh = solution("HD 9 b", "Z", is_default=True)
    save(repo, snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=1), [fresh])

    # T84: el planeta que desaparece del snapshot (alcance completo) deja su fila de pérdida.
    change = db_session.execute(
        sa.select(ArchiveDefaultChangeRow).where(ArchiveDefaultChangeRow.pl_name == "HD 9 b")
    ).scalar_one()
    assert (change.pl_name, change.old_solution_key) == ("HD 9 b", None)


def test_incremental_no_toca_planetas_fuera_de_alcance(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    a = solution("A b", "R", is_default=True)
    b = solution("B b", "R", is_default=True)
    save(repo, snapshot(), [a, b])

    inc = solution("A b", "R2", is_default=True, mass=9.0)
    save(
        repo,
        snapshot(kind=SnapshotKind.INCREMENTAL, offset_days=1),
        [inc],
        removal_scope=frozenset({"A b"}),
    )

    assert repo.current_defaults() == {"A b": inc.solution_key, "B b": b.solution_key}
    # La vieja de A b esta fuera de lo visto y dentro del alcance: baja. B b intacta.
    assert set(repo.active_keys()) == {inc.solution_key, b.solution_key}
    assert row(db_session, a).removed_at is not None


def test_dos_defaults_vigentes_para_un_planeta_lo_impide_la_base(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    first = solution(ref_key="A", is_default=True, mass=1.0)
    save(repo, snapshot(), [first])
    clash = solution(ref_key="B", mass=2.0)
    save(repo, snapshot(offset_days=1), [first, clash])

    with pytest.raises(sa.exc.IntegrityError):
        db_session.execute(
            sa.update(ArchiveSolutionRow)
            .where(ArchiveSolutionRow.solution_key == clash.solution_key)
            .values(is_default_current=True)
        )


def test_save_snapshot_es_atomico(db_session_factory, monkeypatch):
    first = solution(ref_key="A", is_default=True, mass=1.0)
    second = solution("HD 2 b", "B", is_default=True, mass=2.0)
    snap = snapshot()
    diff = SnapshotDiff(
        added=(first.solution_key,),
        default_changes=(DefaultChange("HD 2 b", None, second.solution_key),),
    )

    def boom(*args, **kwargs):
        raise RuntimeError("fallo a mitad de save_snapshot")

    monkeypatch.setattr(repositories_module, "archive_default_change_to_row", boom)

    with pytest.raises(RuntimeError, match="a mitad"):
        with unit_of_work(db_session_factory) as session:
            SqlAlchemyArchiveRepository(session).save_snapshot(snap, [first, second], diff)

    with unit_of_work(db_session_factory) as session:
        assert count(session, ArchiveSnapshotRow) == 0
        assert count(session, ArchiveSolutionRow) == 0
        assert count(session, ArchiveDefaultChangeRow) == 0


def test_save_snapshot_confirmado_persiste_en_otra_sesion(db_session_factory):
    sol = solution(is_default=True)
    snap = snapshot()
    with unit_of_work(db_session_factory) as session:
        save(SqlAlchemyArchiveRepository(session), snap, [sol])

    with unit_of_work(db_session_factory) as session:
        repo = SqlAlchemyArchiveRepository(session)
        assert repo.last_snapshot() == snap
        assert repo.current_defaults() == {"WASP-12 b": sol.solution_key}


def test_carga_sintetica_de_40000_soluciones(db_session_factory, capsys):
    total = 40_000
    sols = [
        solution(
            f"Planeta {i // 3} b",
            f"Ref {i % 3}",
            mass=float(i),
            is_default=(i % 3 == 0),
        )
        for i in range(total)
    ]
    assert len({s.solution_key for s in sols}) == total

    started = time.perf_counter()
    with unit_of_work(db_session_factory) as session:
        save(SqlAlchemyArchiveRepository(session), snapshot(rows=total), sols)
    first_elapsed = time.perf_counter() - started

    started = time.perf_counter()
    with unit_of_work(db_session_factory) as session:
        save(SqlAlchemyArchiveRepository(session), snapshot(offset_days=1, rows=total), sols)
    second_elapsed = time.perf_counter() - started

    with unit_of_work(db_session_factory) as session:
        assert count(session, ArchiveSolutionRow) == total
        repo = SqlAlchemyArchiveRepository(session)
        assert len(repo.active_keys()) == total
        assert len(repo.current_defaults()) == len({s.pl_name for s in sols if s.is_default})
        assert count(session, ArchiveDefaultChangeRow) == 0

    with capsys.disabled():
        print(
            f"\n[T81] 40.000 soluciones: primer snapshot {first_elapsed:.1f}s, "
            f"segundo (todo upsert) {second_elapsed:.1f}s"
        )


def test_default_perdido_sin_clave_vigente_lanza_invariant_violation(db_session):
    repo = SqlAlchemyArchiveRepository(db_session)
    save(repo, snapshot(), [solution(is_default=False)])
    bad = SnapshotDiff(lost_defaults=("WASP-12 b",))  # diff incoherente con la base

    with pytest.raises(InvariantViolation, match="WASP-12 b"):
        repo.save_snapshot(snapshot(offset_days=1), [], bad)
