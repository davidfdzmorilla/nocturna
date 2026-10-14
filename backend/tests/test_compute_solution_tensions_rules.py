"""`ComputeSolutionTensions`: since, avalancha, Items del archivo, idempotencia (T85).

En memoria, sin LLM ni red. Complementa `test_compute_solution_tensions.py`.
"""

from __future__ import annotations

from contextlib import nullcontext
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from fakes.archive import InMemoryArchiveRepository
from fakes.clock import FakeClock
from fakes.solution_tensions import InMemoryArchiveItemRepository, InMemorySolutionTensionRepository
from test_domain_solution_tension import NEW, NOW, OLD, RULE, sol

from nocturna.application.use_cases.compute_solution_tensions import (
    ComputeSolutionTensions,
    SolutionTensionWork,
)
from nocturna.domain.archive import ArchiveParameterValue as PV
from nocturna.domain.entities import (
    ITEM_SOURCE_EXOPLANET_ARCHIVE,
    Item,
    ItemStatus,
    MeasuredParameter,
)
from nocturna.domain.solution_tension import SkipReason

MASS = MeasuredParameter.MASS
RADIUS = MeasuredParameter.RADIUS
PERIOD = MeasuredParameter.PERIOD


def make(*solutions, now=NOW, preloaded_items=()):
    archive = InMemoryArchiveRepository()
    archive.seed(solutions)
    tensions = InMemorySolutionTensionRepository()
    items = InMemoryArchiveItemRepository()
    for item in preloaded_items:
        items.add_many([item])
    work = SolutionTensionWork(archive=archive, solution_tensions=tensions, items=items)
    return ComputeSolutionTensions(lambda: nullcontext(work), RULE, FakeClock(now)), tensions, items


def planet(name: str, *, release: date, default_alta: bool = True):
    """Alta (por defecto o no) + una referencia antigua utilizable, para `name`."""
    alta = sol(
        pl_name=name,
        ref_key=f"ALTA-{name}",
        releasedate=release,
        pl_pubdate=f"{release.year}-{release.month:02d}",
        is_default=default_alta,
    )
    ref = sol(
        pl_name=name,
        ref_key=f"REF-{name}",
        releasedate=date(2017, 2, 1),
        pl_pubdate="2017-03",
        is_default=not default_alta,
        mass=PV(5689.0, 922.0, -572.0, 0),
        period=PV(1260.0, 11.0, -11.0, 0),
    )
    return alta, ref


# --- since: frontera exacta --------------------------------------------------------


def test_since_incluye_la_frontera_exacta_y_excluye_el_dia_anterior():
    en_frontera, ref_a = planet("A b", release=RULE.since)
    antes, ref_b = planet("B b", release=date(2026, 7, 31))
    use_case, tensions, _ = make(en_frontera, ref_a, antes, ref_b)

    report = use_case(dry_run=False)

    assert report.altas == 1
    assert {t.planet_name for t in tensions.all()} == {"A b"}


def test_solo_cuentan_las_altas_published_confirmed_activas():
    cand = sol(
        ref_key="CAND",
        soltype="Published Candidate",
        releasedate=date(2026, 9, 1),
        is_default=False,
    )
    use_case, tensions, _ = make(NEW, OLD, cand)
    report = use_case(dry_run=False)
    assert report.altas == 1  # la Candidate no es alta PC
    assert all(t.new_solution_key == NEW.solution_key for t in tensions.all())


# --- avalancha ---------------------------------------------------------------------


def test_avalancha_de_soluciones_antiguas_no_genera_filas_ni_items():
    solutions = []
    for n in range(400):
        alta, ref = planet(f"P{n:03d} b", release=date(2019, 1 + n % 12, 1))
        solutions += [alta, ref]
    use_case, tensions, items = make(*solutions)

    report = use_case(dry_run=False)

    assert report.altas == 0
    assert report.computed == () and report.no_reference == ()
    assert tensions.all() == [] and items.items == {}


def test_avalancha_con_una_sola_alta_reciente_solo_toca_esa():
    solutions = []
    for n in range(200):
        alta, ref = planet(f"P{n:03d} b", release=date(2020, 5, 1))
        solutions += [alta, ref]
    nueva, ref_nueva = planet("Nueva b", release=date(2026, 9, 1))
    use_case, tensions, items = make(*solutions, nueva, ref_nueva)

    report = use_case(dry_run=False)

    assert report.altas == 1
    assert {t.planet_name for t in tensions.all()} == {"Nueva b"}
    assert len(items.items) == 1


# --- Items del archivo -------------------------------------------------------------


def test_un_item_por_alta_elegible_y_ninguno_para_las_que_no_pasan():
    bien, ref_bien = planet("Bien b", release=date(2026, 9, 1))
    # misma σ pero papel antiguo: old_paper
    antigua = sol(
        pl_name="Antigua b",
        ref_key="ALTA-Antigua",
        releasedate=date(2026, 9, 1),
        pl_pubdate="2020-01",
    )
    ref_antigua = sol(
        pl_name="Antigua b",
        ref_key="REF-Antigua",
        is_default=False,
        releasedate=date(2017, 2, 1),
        pl_pubdate="2017-03",
        mass=PV(5689.0, 922.0, -572.0, 0),
        period=PV(1260.0, 11.0, -11.0, 0),
    )
    # todas las filas del planeta son del mismo paper: sin referencia, sin fila
    mismo = sol(pl_name="Mismo b", ref_key="K", releasedate=date(2026, 9, 1))
    mismo_otro = sol(
        pl_name="Mismo b",
        ref_key="K",
        is_default=False,
        releasedate=date(2017, 2, 1),
        pl_pubdate="2017-03",
        mass=PV(5689.0, 922.0, -572.0, 0),
        period=PV(1260.0, 11.0, -11.0, 0),
    )
    use_case, tensions, items = make(bien, ref_bien, antigua, ref_antigua, mismo, mismo_otro)

    report = use_case(dry_run=False)

    assert report.altas == 3
    assert {i.external_id for i in items.items.values()} == {bien.solution_key}
    by_planet = {}
    for t in tensions.all():
        by_planet.setdefault(t.planet_name, set()).add(t.skip_reason)
    assert by_planet["Antigua b"] == {SkipReason.OLD_PAPER}
    assert "Mismo b" not in by_planet
    assert {n.planet_name for n in report.no_reference} == {"Mismo b"}
    # solo las del planeta elegible quedan enlazadas
    linked = {t.planet_name for t in tensions.all() if t.item_id is not None}
    assert linked == {"Bien b"}
    # las excluidas se guardan igualmente, con su motivo y su σ
    assert all(t.sigma is not None for t in tensions.all())


def test_masa_y_radio_de_la_misma_alta_comparten_item():
    alta = sol(radius=PV(12.0, 0.5, -0.5, 0))
    ref = sol(
        ref_key="REF",
        is_default=False,
        releasedate=date(2017, 2, 1),
        pl_pubdate="2017-03",
        mass=PV(5689.0, 922.0, -572.0, 0),
        radius=PV(20.0, 0.5, -0.5, 0),
        period=PV(1260.0, 11.0, -11.0, 0),
    )
    use_case, tensions, items = make(alta, ref)

    report = use_case(dry_run=False)

    assert {t.parameter for t in report.eligible} == {MASS, RADIUS, PERIOD}
    assert report.items_created == 1 and len(items.items) == 1
    (item,) = items.items.values()
    assert {t.item_id for t in tensions.all()} == {item.id}


def test_get_or_create_reutiliza_el_item_existente():
    previo = Item(
        source=ITEM_SOURCE_EXOPLANET_ARCHIVE,
        external_id=NEW.solution_key,
        title="previo",
        abstract="previo",
        categories=["exoplanet_archive"],
        published_at=NOW,
        fetched_at=NOW,
    )
    use_case, tensions, items = make(NEW, OLD, preloaded_items=[previo])

    report = use_case(dry_run=False)

    assert report.items_created == 0 and report.items_reused == 1
    assert len(items.items) == 1
    assert {t.item_id for t in tensions.all()} == {previo.id}
    assert items.items[(ITEM_SOURCE_EXOPLANET_ARCHIVE, NEW.solution_key)].title == "previo"


def test_la_tension_excluida_de_un_alta_con_otra_elegible_no_se_enlaza_al_item():
    # masa elegible, periodo con ttv_flag: fila de periodo guardada sin item.
    alta = sol(ttv_flag=True)
    use_case, tensions, items = make(alta, OLD)

    use_case(dry_run=False)

    by_param = {t.parameter: t for t in tensions.all()}
    assert by_param[MASS].skip_reason is None and by_param[MASS].item_id is not None
    assert by_param[PERIOD].skip_reason is SkipReason.PERIOD_TTV
    assert by_param[PERIOD].item_id is None
    assert len(items.items) == 1


def test_el_item_del_archivo_es_determinista_y_nace_new():
    use_case, _, items = make(NEW, OLD)
    use_case(dry_run=False)
    (item,) = items.items.values()
    assert item.status is ItemStatus.NEW
    assert item.categories == ["exoplanet_archive"]
    assert item.source == ITEM_SOURCE_EXOPLANET_ARCHIVE
    assert item.title == "HD 202206 c: Cao et al. 2026"
    assert "2026-08-20" in item.abstract and "Benedict & Harrison 2017" in item.abstract

    use_case2, _, items2 = make(NEW, OLD)
    use_case2(dry_run=False)
    (item2,) = items2.items.values()
    assert item2.abstract == item.abstract and item2.title == item.title


def test_published_at_es_la_medianoche_en_la_zona_del_reloj():
    madrid = datetime(2026, 10, 14, 5, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    use_case, _, items = make(NEW, OLD, now=madrid)
    use_case(dry_run=False)
    (item,) = items.items.values()
    assert item.published_at == datetime(2026, 8, 20, 0, 0, tzinfo=ZoneInfo("Europe/Madrid"))
    assert item.published_at != datetime(2026, 8, 20, tzinfo=UTC)


# --- idempotencia y dry-run --------------------------------------------------------


def test_idempotente_aunque_cambie_el_reloj_y_con_nuevas_altas_solo_calcula_las_nuevas():
    nueva, ref_nueva = planet("Nueva b", release=date(2026, 9, 1))
    archive = InMemoryArchiveRepository()
    archive.seed([NEW, OLD])
    tensions = InMemorySolutionTensionRepository()
    items = InMemoryArchiveItemRepository()
    work = SolutionTensionWork(archive=archive, solution_tensions=tensions, items=items)
    clock = FakeClock(NOW)
    use_case = ComputeSolutionTensions(lambda: nullcontext(work), RULE, clock)

    first = use_case(dry_run=False)
    snapshot = {t.id: t for t in tensions.all()}
    archive.seed([nueva, ref_nueva])
    clock.set(datetime(2026, 10, 21, 5, 0, tzinfo=UTC))
    second = use_case(dry_run=False)

    assert len(first.computed) == 2
    assert second.already_computed == 2
    assert {t.planet_name for t in second.computed} == {"Nueva b"}
    for tension_id, before in snapshot.items():
        assert tensions.get(tension_id) == before  # foto fija: no se recalcula
    assert len(items.items) == 2


def test_dry_run_calcula_lo_mismo_que_la_ejecucion_real():
    dry, _, _ = make(NEW, OLD)
    real, _, _ = make(NEW, OLD)
    a, b = dry(dry_run=True), real(dry_run=False)
    key = lambda t: (t.new_solution_key, t.parameter, t.sigma, t.skip_reason)  # noqa: E731
    assert sorted(map(key, a.computed), key=str) == sorted(map(key, b.computed), key=str)
    assert (a.items_created, a.items_reused) == (b.items_created, b.items_reused)
    assert a.dry_run and not b.dry_run


def test_dry_run_dos_veces_da_el_mismo_informe():
    use_case, tensions, items = make(NEW, OLD)
    assert use_case(dry_run=True).already_computed == 0
    assert use_case(dry_run=True).already_computed == 0
    assert tensions.all() == [] and items.items == {}
