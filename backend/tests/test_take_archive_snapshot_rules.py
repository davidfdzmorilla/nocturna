"""Reglas de `TakeArchiveSnapshot` (T81): modo, incremental, guardas, idempotencia.

Fuente falsa (`FakeArchiveSource`) + `InMemoryArchiveRepository`. Sin red ni BD.
"""

import random
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest
from fakes.archive import InMemoryArchiveRepository
from fakes.archive_source import FakeArchiveSource, make_catalog, make_solution
from fakes.clock import FakeClock
from helpers.archive import make_client

from nocturna.application.use_cases.take_archive_snapshot import (
    SnapshotAborted,
    TakeArchiveSnapshot,
)
from nocturna.domain.archive import DefaultChange, SnapshotKind
from nocturna.infrastructure.exoplanet_archive.client import ExoplanetArchiveUnavailable
from nocturna.infrastructure.exoplanet_archive.snapshot import ArchiveSnapshotSource

pytestmark = pytest.mark.anyio

MADRID = ZoneInfo("Europe/Madrid")
OCT_1 = datetime(2026, 10, 1, 10, 0, tzinfo=MADRID)
OCT_8 = datetime(2026, 10, 8, 10, 0, tzinfo=MADRID)


def _uc(source, repo, clock, *, max_requests=6, batch=75, fraction=0.5, tz=MADRID):
    return TakeArchiveSnapshot(
        source=source,
        archive=repo,
        clock=clock,
        max_requests=max_requests,
        planet_batch_size=batch,
        max_change_fraction=fraction,
        timezone=tz,
    )


async def _run(catalog, repo, now, *, full=False, dry=False, fail_on=None, **kw):
    source = FakeArchiveSource(catalog, planet_batch_size=kw.get("batch", 75), fail_on=fail_on)
    report = await _uc(source, repo, FakeClock(now), **kw)(force_full=full, dry_run=dry)
    return report, source


def _with_newest(catalog, *names, release="2026-09-25"):
    """Añade a cada planeta de `names` una solución nueva (`Newest 2026`)."""
    return catalog + [make_solution(n, "Newest 2026", release=release, mass=9.0) for n in names]


# ------------------------------------------------------------- modo y primera vez


async def test_primera_ejecucion_es_completa_y_sin_default_changes():
    repo = InMemoryArchiveRepository()
    report, source = await _run(make_catalog(10), repo, OCT_1)

    assert report.snapshot.kind is SnapshotKind.FULL and report.persisted
    assert len(report.diff.added) == 20
    assert report.diff.default_changes == () and report.diff.lost_defaults == ()
    assert report.snapshot.rows_total == 20 and report.snapshot.defaults_total == 10
    assert report.snapshot.requests == 1 and source.calls == [("all",)]
    assert repo.default_changes == [] and len(repo.current_defaults()) == 10


def _base(planets: int = 10):
    """Catalogo base cuya releasedate maxima es 2026-09-25 y solo afecta a P09 b:
    en incremental, `released_since(max)` devuelve siempre esa fila."""
    return _with_newest(make_catalog(planets), "P09 b")


@pytest.mark.parametrize("n_planets,batch", [(1, 2), (2, 2), (3, 2), (4, 2), (5, 3)])
async def test_mismo_mes_es_incremental_con_dos_peticiones_mas_los_lotes(n_planets, batch):
    repo = InMemoryArchiveRepository()
    catalog = _base()
    await _run(catalog, repo, OCT_1, batch=batch)
    touched = [f"P{i:02d} b" for i in range(n_planets)]
    catalog2 = _with_newest(catalog, *touched)

    report, source = await _run(catalog2, repo, OCT_8, batch=batch)

    # Afectados: los tocados + P09 b (su fila lleva la releasedate maxima).
    affected = tuple(sorted([*touched, "P09 b"]))
    lotes = -(-len(affected) // batch)
    assert report.snapshot.kind is SnapshotKind.INCREMENTAL and not report.fell_back_to_full
    assert report.snapshot.requests == 2 + lotes == source.requests_made
    assert [c[0] for c in source.calls] == [
        "released_since",
        "default_solutions",
        "solutions_for_planets",
    ]
    assert source.calls[0] == ("released_since", date(2026, 9, 25))
    assert source.calls[2][1] == affected
    assert len(report.diff.added) == n_planets


async def test_incremental_pide_desde_la_ultima_releasedate_vista():
    repo = InMemoryArchiveRepository()
    catalog = _with_newest(make_catalog(3), "P00 b")
    await _run(catalog, repo, OCT_1)
    assert repo.last_snapshot().max_releasedate == date(2026, 9, 25)

    _, source = await _run(catalog, repo, OCT_8)

    assert source.calls[0] == ("released_since", date(2026, 9, 25))


async def test_incremental_sin_releasedate_previa_pide_todo():
    repo = InMemoryArchiveRepository()
    first, _ = await _run([], repo, OCT_1)
    assert first.snapshot.max_releasedate is None and first.snapshot.kind is SnapshotKind.FULL

    report, source = await _run(make_catalog(2), repo, OCT_8)

    assert report.snapshot.kind is SnapshotKind.INCREMENTAL
    assert source.calls[0] == ("all",) and source.calls[1] == ("default_solutions",)
    assert len(report.diff.added) == 4


async def test_force_full_fuerza_completo_aunque_sea_el_mismo_mes():
    repo = InMemoryArchiveRepository()
    await _run(make_catalog(5), repo, OCT_1)

    report, source = await _run(make_catalog(5), repo, OCT_8, full=True)

    assert report.snapshot.kind is SnapshotKind.FULL and not report.fell_back_to_full
    assert source.calls == [("all",)]


@pytest.mark.parametrize(
    "first,now,tz_name,expected",
    [
        # Borde: 23:30 UTC del 30-sep es 01:30 del 1-oct en Madrid (CEST) -> mes nuevo.
        (
            datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
            datetime(2026, 9, 30, 23, 30, tzinfo=UTC),
            "Europe/Madrid",
            SnapshotKind.FULL,
        ),
        # El mismo instante en UTC sigue siendo septiembre -> incremental.
        (
            datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
            datetime(2026, 9, 30, 23, 30, tzinfo=UTC),
            "UTC",
            SnapshotKind.INCREMENTAL,
        ),
        # 21:30 UTC = 23:30 Madrid: aun septiembre.
        (
            datetime(2026, 9, 15, 10, 0, tzinfo=UTC),
            datetime(2026, 9, 30, 21, 30, tzinfo=UTC),
            "Europe/Madrid",
            SnapshotKind.INCREMENTAL,
        ),
        # El completo previo se tomo 23:30 UTC del 30-sep = 1-oct en Madrid.
        (
            datetime(2026, 9, 30, 23, 30, tzinfo=UTC),
            datetime(2026, 10, 15, 10, 0, tzinfo=UTC),
            "Europe/Madrid",
            SnapshotKind.INCREMENTAL,
        ),
        # ... pero en UTC era septiembre, y octubre es mes nuevo.
        (
            datetime(2026, 9, 30, 23, 30, tzinfo=UTC),
            datetime(2026, 10, 15, 10, 0, tzinfo=UTC),
            "UTC",
            SnapshotKind.FULL,
        ),
        # Mismo numero de mes, otro anio.
        (
            datetime(2025, 10, 5, 10, 0, tzinfo=UTC),
            datetime(2026, 10, 5, 10, 0, tzinfo=UTC),
            "Europe/Madrid",
            SnapshotKind.FULL,
        ),
    ],
    ids=[
        "borde-madrid-mes-nuevo",
        "mismo-instante-utc-incremental",
        "23:30-madrid-aun-septiembre",
        "completo-previo-ya-octubre-madrid",
        "completo-previo-septiembre-utc",
        "otro-anio",
    ],
)
async def test_el_cambio_de_mes_se_decide_en_la_zona_configurada(first, now, tz_name, expected):
    repo = InMemoryArchiveRepository()
    tz = ZoneInfo(tz_name)
    await _run(make_catalog(5), repo, first, tz=tz)

    report, _ = await _run(make_catalog(5), repo, now, tz=tz)

    assert report.snapshot.kind is expected


async def test_el_completo_del_mes_se_busca_entre_completos_no_entre_incrementales():
    repo = InMemoryArchiveRepository()
    await _run(make_catalog(5), repo, datetime(2026, 9, 1, 10, tzinfo=MADRID))
    await _run(make_catalog(5), repo, datetime(2026, 9, 20, 10, tzinfo=MADRID))  # incremental
    assert repo.last_snapshot().kind is SnapshotKind.INCREMENTAL

    report, _ = await _run(make_catalog(5), repo, datetime(2026, 10, 2, 10, tzinfo=MADRID))

    assert report.snapshot.kind is SnapshotKind.FULL


# --------------------------------------------------------- salto a completo


async def test_salta_a_completo_cuando_los_lotes_no_caben():
    repo = InMemoryArchiveRepository()
    catalog = _base()
    await _run(catalog, repo, OCT_1, max_requests=5, batch=1)
    catalog2 = _with_newest(catalog, "P00 b", "P01 b", "P02 b")

    # 4 lotes (3 + P09 b), quedan 5 - 2 = 3.
    report, source = await _run(catalog2, repo, OCT_8, max_requests=5, batch=1)

    assert report.fell_back_to_full and report.snapshot.kind is SnapshotKind.FULL
    assert [c[0] for c in source.calls] == ["released_since", "default_solutions", "all"]
    assert report.snapshot.requests == 3 <= 5
    assert len(report.diff.added) == 3 and report.persisted
    assert repo.last_full_snapshot().id == report.snapshot.id


async def test_los_lotes_que_caben_justo_no_provocan_salto():
    repo = InMemoryArchiveRepository()
    catalog = _base()
    await _run(catalog, repo, OCT_1, max_requests=6, batch=1)
    catalog2 = _with_newest(catalog, "P00 b", "P01 b", "P02 b")

    # 4 lotes (3 + P09 b), quedan 6 - 2 = 4: caben exactos.
    report, _ = await _run(catalog2, repo, OCT_8, max_requests=6, batch=1)

    assert not report.fell_back_to_full and report.snapshot.kind is SnapshotKind.INCREMENTAL
    assert report.snapshot.requests == 6


async def test_el_salto_a_completo_ya_puede_afirmar_bajas_de_cualquier_planeta():
    repo = InMemoryArchiveRepository()
    catalog = _base()
    await _run(catalog, repo, OCT_1, max_requests=5, batch=1)
    gone = [s for s in catalog if s.pl_name == "P09 b" and not s.is_default][0]
    catalog2 = _with_newest([s for s in catalog if s is not gone], "P00 b", "P01 b", "P02 b")

    report, _ = await _run(catalog2, repo, OCT_8, max_requests=5, batch=1)

    assert report.fell_back_to_full and report.diff.removed == (gone.solution_key,)


# ---------------------------------------------------------- defaults en incremental


def _flip_default(catalog, name, *, to_ref=None):
    """Quita el default de `name`; si `to_ref`, lo pone en esa referencia."""
    out = []
    for s in catalog:
        if s.pl_name == name:
            is_default = to_ref is not None and s.pl_refname == to_ref
            out.append(
                make_solution(
                    name,
                    s.pl_refname,
                    default=is_default,
                    release=str(s.releasedate),
                    mass=s.mass.value,
                )
            )
        else:
            out.append(s)
    return out


async def test_planeta_que_pierde_el_default_en_incremental_es_afectado_sin_filas_nuevas():
    repo = InMemoryArchiveRepository()
    catalog = _with_newest(make_catalog(5), "P04 b")
    await _run(catalog, repo, OCT_1)
    catalog2 = _flip_default(catalog, "P01 b")  # sin default; sin filas nuevas

    report, source = await _run(catalog2, repo, OCT_8)

    assert report.snapshot.kind is SnapshotKind.INCREMENTAL
    assert report.diff.lost_defaults == ("P01 b",)
    assert report.diff.default_changes == ()
    # P01 se consulta por nombre aunque no tenga filas nuevas.
    assert source.calls[-1] == ("solutions_for_planets", ("P01 b", "P04 b"))
    assert "P01 b" not in repo.current_defaults()


async def test_cambio_de_default_en_incremental_trae_la_clave_anterior():
    repo = InMemoryArchiveRepository()
    catalog = _with_newest(make_catalog(5), "P04 b")
    await _run(catalog, repo, OCT_1)
    old_key = repo.current_defaults()["P02 b"]
    catalog2 = _flip_default(catalog, "P02 b", to_ref="Jones 2021")
    new_key = [s for s in catalog2 if s.pl_name == "P02 b" and s.is_default][0].solution_key

    report, _ = await _run(catalog2, repo, OCT_8)

    assert report.diff.default_changes == (DefaultChange("P02 b", old_key, new_key),)
    assert report.diff.lost_defaults == ()
    assert repo.current_defaults()["P02 b"] == new_key
    assert [c for _, _, c in repo.default_changes] == [DefaultChange("P02 b", old_key, new_key)]


async def test_planeta_nuevo_con_default_en_incremental_es_cambio_con_old_none():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(5)
    await _run(catalog, repo, OCT_1)
    new = make_solution("Z99 b", "Fresh 2026", default=True, release="2026-09-30")

    report, _ = await _run(catalog + [new], repo, OCT_8)

    assert report.diff.default_changes == (DefaultChange("Z99 b", None, new.solution_key),)


async def test_baja_fuera_del_alcance_no_se_afirma_en_incremental_pero_si_en_completo():
    repo = InMemoryArchiveRepository()
    catalog = _with_newest(make_catalog(10), "P00 b")
    await _run(catalog, repo, OCT_1)
    gone = [s for s in catalog if s.pl_name == "P07 b" and not s.is_default][0]
    catalog2 = [s for s in catalog if s is not gone]

    incremental, _ = await _run(catalog2, repo, OCT_8)
    assert incremental.diff.removed == ()

    full, _ = await _run(catalog2, repo, OCT_8, full=True)
    assert full.diff.removed == (gone.solution_key,)


async def test_baja_dentro_del_alcance_se_afirma_en_incremental():
    repo = InMemoryArchiveRepository()
    catalog = _with_newest(make_catalog(10), "P03 b")
    await _run(catalog, repo, OCT_1)
    gone = [s for s in catalog if s.pl_name == "P03 b" and s.pl_refname == "Jones 2021"][0]
    catalog2 = [s for s in catalog if s is not gone]

    report, _ = await _run(catalog2, repo, OCT_8)

    assert report.snapshot.kind is SnapshotKind.INCREMENTAL
    assert report.diff.removed == (gone.solution_key,)


# --------------------------------------------------------------------- guardas


async def test_bajas_masivas_abortan_sin_persistir():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(10)
    await _run(catalog, repo, OCT_1)
    before = dict(repo.active_keys())

    with pytest.raises(SnapshotAborted, match="bajas"):
        await _run(catalog[:8], repo, OCT_8, full=True)  # 12 bajas de 20 > 50 %

    assert len(repo.snapshots) == 1
    assert repo.active_keys() == before
    assert all(st.removed_at is None for st in repo.stored.values())


async def test_las_bajas_justo_en_el_limite_no_abortan():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(10)
    await _run(catalog, repo, OCT_1)

    report, _ = await _run(catalog[::2], repo, OCT_8, full=True)  # 10 bajas de 20 = 50 %

    assert len(report.diff.removed) == 10 and report.persisted
    assert len(repo.snapshots) == 2


async def test_cambios_de_default_masivos_abortan_sin_persistir():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(10)
    await _run(catalog, repo, OCT_1)
    flipped = catalog
    for i in range(6):
        flipped = _flip_default(flipped, f"P{i:02d} b", to_ref="Jones 2021")

    with pytest.raises(SnapshotAborted, match="default"):
        await _run(flipped, repo, OCT_8, full=True)  # 6 cambios de 10 > 50 %

    assert len(repo.snapshots) == 1 and repo.default_changes == []


async def test_cambios_de_default_justo_en_el_limite_no_abortan():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(10)
    await _run(catalog, repo, OCT_1)
    flipped = catalog
    for i in range(5):
        flipped = _flip_default(flipped, f"P{i:02d} b", to_ref="Jones 2021")

    report, _ = await _run(flipped, repo, OCT_8, full=True)

    assert len(report.diff.default_changes) == 5 and report.persisted


async def test_perdida_masiva_de_defaults_aborta_sin_persistir():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(10)
    await _run(catalog, repo, OCT_1)
    stripped = catalog
    for i in range(10):
        stripped = _flip_default(stripped, f"P{i:02d} b")  # default_flag=0 en todo

    with pytest.raises(SnapshotAborted, match="default"):
        await _run(stripped, repo, OCT_8, full=True)

    assert len(repo.snapshots) == 1 and repo.default_changes == []


async def test_perdida_de_defaults_justo_en_el_limite_no_aborta():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(10)
    await _run(catalog, repo, OCT_1)
    stripped = catalog
    for i in range(5):
        stripped = _flip_default(stripped, f"P{i:02d} b")

    report, _ = await _run(stripped, repo, OCT_8, full=True)

    assert len(report.diff.lost_defaults) == 5 and report.persisted


async def test_la_guarda_no_aplica_al_primer_snapshot():
    repo = InMemoryArchiveRepository()

    report, _ = await _run(make_catalog(10), repo, OCT_1, fraction=0.001)

    assert report.persisted and len(report.diff.added) == 20


async def test_la_guarda_tambien_protege_en_incremental():
    repo = InMemoryArchiveRepository()
    await _run(make_catalog(10), repo, OCT_1)
    # Respuesta truncada: solo queda una solucion y todo lo demas "desaparece".
    broken = [make_solution("P00 b", "Smith 2020", default=True, release="2026-09-30")]

    with pytest.raises(SnapshotAborted):
        await _run(broken, repo, OCT_8)

    assert len(repo.snapshots) == 1
    assert all(st.removed_at is None for st in repo.stored.values())


# --------------------------------------------------- fallo de la fuente / dry-run


@pytest.mark.parametrize("fail_on", ["all", "released_since", "default_solutions"])
async def test_fallo_de_la_fuente_no_persiste_nada(fail_on):
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(5)
    if fail_on != "all":
        await _run(catalog, repo, OCT_1)
    snapshots_before = list(repo.snapshots)
    stored_before = {k: (v.last_seen, v.removed_at) for k, v in repo.stored.items()}

    with pytest.raises(ExoplanetArchiveUnavailable):
        await _run(_with_newest(catalog, "P00 b"), repo, OCT_8, fail_on=fail_on)

    assert repo.snapshots == snapshots_before
    assert {k: (v.last_seen, v.removed_at) for k, v in repo.stored.items()} == stored_before


async def test_fallo_en_los_lotes_no_persiste_nada():
    repo = InMemoryArchiveRepository()
    catalog = make_catalog(5)
    await _run(catalog, repo, OCT_1)

    with pytest.raises(ExoplanetArchiveUnavailable):
        await _run(_with_newest(catalog, "P00 b"), repo, OCT_8, fail_on="solutions_for_planets")

    assert len(repo.snapshots) == 1


async def test_archivo_caido_503_con_la_fuente_http_no_persiste_nada():
    repo = InMemoryArchiveRepository()
    client, _ = make_client(lambda r: httpx.Response(503))
    source = ArchiveSnapshotSource(client, planet_batch_size=75)

    with pytest.raises(ExoplanetArchiveUnavailable):
        await _uc(source, repo, FakeClock(OCT_1))(force_full=False, dry_run=False)

    assert repo.snapshots == [] and repo.stored == {}


async def test_relanzar_no_da_altas_bajas_ni_cambios():
    repo = InMemoryArchiveRepository()
    catalog = _with_newest(make_catalog(10), "P00 b")
    await _run(catalog, repo, OCT_1)
    stored = {k: v.is_default_current for k, v in repo.stored.items()}

    for day in (8, 9):
        report, _ = await _run(catalog, repo, datetime(2026, 10, day, 10, tzinfo=MADRID))
        assert report.snapshot.kind is SnapshotKind.INCREMENTAL
        d = report.diff
        assert (d.added, d.removed, d.reactivated, d.default_changes, d.lost_defaults) == ((),) * 5

    assert len(repo.snapshots) == 3 and repo.default_changes == []
    assert {k: v.is_default_current for k, v in repo.stored.items()} == stored


async def test_relanzar_tras_perder_un_default_ya_no_lo_vuelve_a_notificar():
    repo = InMemoryArchiveRepository()
    catalog = _with_newest(make_catalog(5), "P04 b")
    await _run(catalog, repo, OCT_1)
    catalog2 = _flip_default(catalog, "P01 b")
    first, _ = await _run(catalog2, repo, OCT_8)
    second, _ = await _run(catalog2, repo, datetime(2026, 10, 9, 10, tzinfo=MADRID))

    assert first.diff.lost_defaults == ("P01 b",)
    assert second.diff.lost_defaults == ()


class _CountingRepo(InMemoryArchiveRepository):
    def __init__(self) -> None:
        super().__init__()
        self.saves = 0

    def save_snapshot(self, snapshot, solutions, diff) -> None:
        self.saves += 1
        super().save_snapshot(snapshot, solutions, diff)


async def test_dry_run_no_llama_a_save_snapshot_pero_calcula_el_diff():
    repo = _CountingRepo()

    report, source = await _run(make_catalog(5), repo, OCT_1, dry=True)

    assert repo.saves == 0 and repo.snapshots == [] and repo.stored == {}
    assert not report.persisted and report.snapshot.kind is SnapshotKind.FULL
    assert len(report.diff.added) == 10 and source.calls == [("all",)]


async def test_dry_run_incremental_no_escribe_y_el_siguiente_real_ve_lo_mismo():
    repo = _CountingRepo()
    catalog = make_catalog(5)
    await _run(catalog, repo, OCT_1)
    catalog2 = _with_newest(catalog, "P00 b")

    dry, _ = await _run(catalog2, repo, OCT_8, dry=True)
    real, _ = await _run(catalog2, repo, OCT_8)

    assert repo.saves == 2  # primera + la real; la dry-run no cuenta
    assert dry.diff == real.diff and not dry.persisted and real.persisted


# ------------------------------------------------- max_releasedate y huella


async def test_max_releasedate_es_la_mayor_visto_y_nunca_retrocede():
    repo = InMemoryArchiveRepository()
    catalog = _with_newest(make_catalog(10), "P00 b")
    first, _ = await _run(catalog, repo, OCT_1)
    assert first.snapshot.max_releasedate == date(2026, 9, 25)

    # Se retira lo mas reciente y el nuevo maximo visto seria 2026-08-01.
    older = [s for s in catalog if s.pl_refname != "Newest 2026"]
    second, _ = await _run(older, repo, OCT_8, full=True)
    assert second.snapshot.max_releasedate == date(2026, 9, 25)

    # Algo mas nuevo si lo adelanta.
    newer = _with_newest(older, "P01 b", release="2026-10-02")
    third, _ = await _run(newer, repo, datetime(2026, 10, 9, 10, tzinfo=MADRID))
    assert third.snapshot.max_releasedate == date(2026, 10, 2)


async def test_incremental_sin_filas_nuevas_conserva_max_releasedate():
    repo = InMemoryArchiveRepository()
    catalog = _base()
    first, _ = await _run(catalog, repo, OCT_1)
    retired = [s for s in catalog if s.pl_refname != "Newest 2026"]

    second, source = await _run(retired, repo, OCT_8)

    assert second.snapshot.kind is SnapshotKind.INCREMENTAL
    assert source.calls[0] == ("released_since", date(2026, 9, 25))
    assert second.snapshot.max_releasedate == first.snapshot.max_releasedate == date(2026, 9, 25)


async def test_payload_sha256_no_depende_del_orden():
    catalog = _with_newest(make_catalog(10), "P00 b")
    shuffled = list(catalog)
    random.Random(7).shuffle(shuffled)

    a, _ = await _run(catalog, InMemoryArchiveRepository(), OCT_1)
    b, _ = await _run(shuffled, InMemoryArchiveRepository(), OCT_1)
    c, _ = await _run(list(reversed(catalog)), InMemoryArchiveRepository(), OCT_1)

    assert a.snapshot.payload_sha256 == b.snapshot.payload_sha256 == c.snapshot.payload_sha256
    assert len(a.snapshot.payload_sha256) == 64


async def test_payload_sha256_cambia_si_cambia_el_contenido():
    catalog = make_catalog(5)
    changed = [*catalog[:-1], make_solution("P04 b", "Jones 2021", release="2026-07-15", mass=5.0)]

    a, _ = await _run(catalog, InMemoryArchiveRepository(), OCT_1)
    b, _ = await _run(changed, InMemoryArchiveRepository(), OCT_1)

    assert a.snapshot.payload_sha256 != b.snapshot.payload_sha256


async def test_filas_duplicadas_se_fusionan_y_no_cambian_la_huella():
    catalog = make_catalog(5)
    dup = [*catalog, catalog[0]]

    a, _ = await _run(catalog, InMemoryArchiveRepository(), OCT_1)
    b, _ = await _run(dup, InMemoryArchiveRepository(), OCT_1)

    assert b.snapshot.duplicate_rows == 1 and a.snapshot.duplicate_rows == 0
    assert b.snapshot.rows_total == a.snapshot.rows_total
    assert a.snapshot.payload_sha256 == b.snapshot.payload_sha256
