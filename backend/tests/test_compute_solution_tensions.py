"""`ComputeSolutionTensions` con dobles en memoria (T85). Sin LLM ni red."""

from __future__ import annotations

import inspect
from contextlib import nullcontext
from datetime import UTC, datetime

from fakes.archive import InMemoryArchiveRepository
from fakes.clock import FakeClock
from fakes.solution_tensions import InMemoryArchiveItemRepository, InMemorySolutionTensionRepository
from test_domain_solution_tension import MSINI, NEW, OLD, RULE

from nocturna.application.use_cases.compute_solution_tensions import (
    ComputeSolutionTensions,
    SolutionTensionWork,
)
from nocturna.domain.entities import ITEM_SOURCE_EXOPLANET_ARCHIVE, MeasuredParameter

NOW = datetime(2026, 10, 14, 5, 0, tzinfo=UTC)


def make(*solutions):
    archive = InMemoryArchiveRepository()
    archive.seed(solutions)
    tensions = InMemorySolutionTensionRepository()
    items = InMemoryArchiveItemRepository()
    work = SolutionTensionWork(archive=archive, solution_tensions=tensions, items=items)
    return ComputeSolutionTensions(lambda: nullcontext(work), RULE, FakeClock(NOW)), tensions, items


def test_calcula_guarda_y_crea_un_item_por_alta():
    use_case, tensions, items = make(NEW, OLD, MSINI)
    report = use_case(dry_run=False)

    assert report.altas == 1  # solo NEW tiene releasedate >= since
    assert {(t.parameter) for t in report.computed} == {
        MeasuredParameter.MASS,
        MeasuredParameter.PERIOD,
    }
    assert len(report.eligible) == 2
    assert len(tensions.all()) == 2
    assert report.items_created == 1
    (item,) = items.items.values()
    assert item.source == ITEM_SOURCE_EXOPLANET_ARCHIVE
    assert item.external_id == NEW.solution_key
    assert item.title == "HD 202206 c: Cao et al. 2026"
    assert item.published_at == datetime(2026, 8, 20, tzinfo=UTC)
    assert {t.item_id for t in tensions.all()} == {item.id}


def test_es_idempotente():
    use_case, tensions, items = make(NEW, OLD)
    use_case(dry_run=False)
    again = use_case(dry_run=False)
    assert again.computed == ()
    assert again.already_computed == 2
    assert len(tensions.all()) == 2
    assert len(items.items) == 1


def test_dry_run_no_escribe_nada():
    use_case, tensions, items = make(NEW, OLD)
    report = use_case(dry_run=True)
    assert len(report.computed) == 2
    assert report.items_created == 1
    assert tensions.all() == []
    assert items.items == {}


def test_sin_referencia_no_guarda_fila_y_lo_cuenta():
    use_case, tensions, _ = make(NEW)
    report = use_case(dry_run=False)
    assert report.computed == ()
    assert {n.parameter for n in report.no_reference} == {
        MeasuredParameter.MASS,
        MeasuredParameter.PERIOD,
    }
    assert tensions.all() == []


def test_las_altas_anteriores_a_since_no_generan_filas():
    use_case, _, _ = make(OLD, MSINI)
    assert use_case(dry_run=False).altas == 0


def test_el_constructor_no_acepta_proveedor_ni_fabrica_de_agentes():
    params = set(inspect.signature(ComputeSolutionTensions.__init__).parameters)
    assert params == {"self", "work", "rule", "clock"}
    assert set(SolutionTensionWork.__dataclass_fields__) == {
        "archive",
        "solution_tensions",
        "items",
    }
