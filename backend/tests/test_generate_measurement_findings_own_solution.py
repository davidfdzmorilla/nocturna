"""T83: `GenerateMeasurementFindings` nunca genera `confirmacion_independiente`
con una referencia que es (o puede ser) el propio paper. En memoria, sin red,
sin catálogo, sin Claude. Se activa `confirmation_enabled=True` solo en el test."""

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fakes.clock import FakeClock
from fakes.tension_evaluations import InMemoryTensionEvaluationRepository
from fakes.work import (
    InMemoryFindingRepository,
    InMemoryItemRepository,
    make_measurement_findings_work_factory,
)
from helpers.exoplanet import make_own_solution_rule
from helpers.measurement_findings import chakraborty_b, hip67522_b, make_arxiv_item

from nocturna.application.use_cases.generate_measurement_findings import (
    GenerateMeasurementFindings,
)
from nocturna.domain.entities import FindingType, ItemStatus

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
HIP_PAPER = "2609.35979"


class _Env:
    def __init__(self) -> None:
        self.items = InMemoryItemRepository()
        self.findings = InMemoryFindingRepository()
        self.evaluations = InMemoryTensionEvaluationRepository()
        self.run_id = uuid4()

    def add(self, item, evaluation) -> None:
        self.items.save(item)
        self.evaluations.add(evaluation)

    def generator(self, *, confirmation_enabled: bool = True) -> GenerateMeasurementFindings:
        return GenerateMeasurementFindings(
            work=make_measurement_findings_work_factory(
                items=self.items, findings=self.findings, evaluations=self.evaluations
            ),
            clock=FakeClock(NOW),
            planet_overview_url=lambda name: f"https://archive.test/{name.replace(' ', '_')}",
            max_candidates=5,
            max_sigma=2.0,
            window_days=30,
            confirmation_enabled=confirmation_enabled,
            own_solution_rule=make_own_solution_rule(),
        )


def _journal_reference(value):
    return replace(
        chakraborty_b(),
        arxiv_id=None,
        value=value,
        err_plus=7.6,
        err_minus=7.8,
        pl_pubdate="2026-10",
        releasedate=date(2026, 10, 3),
    )


def _env_with(reference=None) -> _Env:
    env = _Env()
    item = make_arxiv_item(HIP_PAPER, status=ItemStatus.READ)
    env.add(item, hip67522_b(item.id, reference=reference))
    return env


def test_hip_67522_b_con_chakraborty_produce_confirmacion_independiente():
    env = _env_with()

    report = env.generator(confirmation_enabled=True)(run_id=env.run_id, dry_run=False)

    (finding,) = report.created
    assert finding.type is FindingType.CONFIRMACION_INDEPENDIENTE
    assert finding.independent_confirmation.reference.arxiv_id == "2606.18045"
    assert report.blocked_confirmations == ()


@pytest.mark.parametrize("enabled", [True, False])
def test_la_version_de_revista_del_propio_paper_nunca_produce_confirmacion(enabled):
    env = _env_with(_journal_reference(25.04))

    report = env.generator(confirmation_enabled=enabled)(run_id=env.run_id, dry_run=False)

    assert report.created == ()
    assert report.blocked_confirmations == ()
    assert env.findings.unpublished_for_run(env.run_id) == []


@pytest.mark.parametrize("enabled", [True, False])
def test_una_referencia_ambigua_nunca_produce_confirmacion(enabled):
    env = _env_with(_journal_reference(40.0))

    report = env.generator(confirmation_enabled=enabled)(run_id=env.run_id, dry_run=False)

    assert report.created == ()
    assert report.blocked_confirmations == ()


def test_con_confirmation_enabled_false_la_independiente_sigue_bloqueada_y_la_propia_no_cuenta():
    independent = _env_with()
    own = _env_with(_journal_reference(25.04))

    blocked = independent.generator(confirmation_enabled=False)(
        run_id=independent.run_id, dry_run=False
    )
    not_blocked = own.generator(confirmation_enabled=False)(run_id=own.run_id, dry_run=False)

    assert len(blocked.blocked_confirmations) == 1
    assert not_blocked.blocked_confirmations == ()


def test_dry_run_tampoco_cuenta_como_bloqueada_la_referencia_propia():
    env = _env_with(_journal_reference(25.04))

    report = env.generator(confirmation_enabled=False)(run_id=env.run_id, dry_run=True)

    assert report.created == () and report.blocked_confirmations == ()


def test_el_constructor_exige_own_solution_rule():
    env = _Env()

    with pytest.raises(TypeError):
        GenerateMeasurementFindings(  # type: ignore[call-arg]
            work=make_measurement_findings_work_factory(
                items=env.items, findings=env.findings, evaluations=env.evaluations
            ),
            clock=FakeClock(NOW),
            planet_overview_url=lambda name: name,
            max_candidates=5,
            max_sigma=2.0,
            window_days=30,
            confirmation_enabled=True,
        )
