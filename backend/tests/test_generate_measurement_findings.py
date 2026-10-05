"""T89: `GenerateMeasurementFindings` (en memoria, sin red, sin catálogo, sin Claude)."""

from __future__ import annotations

import random
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from fakes.clock import FakeClock
from fakes.tension_evaluations import InMemoryTensionEvaluationRepository
from fakes.work import (
    InMemoryFindingRepository,
    InMemoryItemRepository,
    make_measurement_findings_work_factory,
)
from helpers.measurement_findings import (
    chakraborty_b,
    hip67522_b,
    hip67522_c,
    make_arxiv_item,
    present_without_reference,
    toi_210_b_mass,
    toi_6981_b,
)

from nocturna.application.use_cases import generate_measurement_findings as module
from nocturna.application.use_cases.generate_measurement_findings import (
    GenerateMeasurementFindings,
    MeasurementFindingsWork,
)
from nocturna.domain.entities import FindingType, ItemStatus
from nocturna.domain.errors import InvariantViolation

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)


class _Env:
    def __init__(self) -> None:
        self.items = InMemoryItemRepository()
        self.findings = InMemoryFindingRepository()
        self.evaluations = InMemoryTensionEvaluationRepository()
        self.run_id = uuid4()

    def add(self, item, *evaluations) -> None:
        self.items.save(item)
        for evaluation in evaluations:
            self.evaluations.add(evaluation)

    def generator(
        self,
        *,
        max_candidates: int = 5,
        confirmation_enabled: bool = True,
        window_days: int = 30,
        now: datetime = NOW,
    ) -> GenerateMeasurementFindings:
        return GenerateMeasurementFindings(
            work=make_measurement_findings_work_factory(
                items=self.items, findings=self.findings, evaluations=self.evaluations
            ),
            clock=FakeClock(now),
            planet_overview_url=lambda name: f"https://archive.test/{name.replace(' ', '_')}",
            max_candidates=max_candidates,
            max_sigma=2.0,
            window_days=window_days,
            confirmation_enabled=confirmation_enabled,
        )


def _toi_item(external_id="2609.37597", **kw):
    return make_arxiv_item(external_id, status=ItemStatus.READ, **kw)


def _stored(env: _Env) -> list:
    return env.findings.unpublished_for_run(env.run_id)


# --- generación ---------------------------------------------------------------


def test_un_finding_por_evaluacion_elegible_con_su_payload_y_sin_publicar():
    env = _Env()
    toi = _toi_item()
    env.add(toi, toi_6981_b(toi.id))
    foo = _toi_item("2610.00001")
    env.add(foo, present_without_reference(foo.id))

    report = env.generator(confirmation_enabled=False)(run_id=env.run_id, dry_run=False)

    assert report.primera_medida == 2 and report.confirmacion_independiente == 0
    stored = _stored(env)
    assert len(stored) == 2
    by_item = {f.item_id: f for f in stored}
    toi_finding = by_item[toi.id]
    assert toi_finding.type is FindingType.PRIMERA_MEDIDA
    assert toi_finding.run_id == env.run_id
    assert not toi_finding.is_published and toi_finding.confidence is None
    assert toi_finding.first_measurement is not None
    assert toi_finding.first_measurement.archive_url is None, "ausente: sin enlace (D14)"
    assert toi_finding.title.startswith("Primera medida del radio de TOI-6981 b")
    assert "arXiv:2609.37597" in toi_finding.level_amateur
    foo_finding = by_item[foo.id]
    assert foo_finding.first_measurement.archive_url == "https://archive.test/Foo-1_b"
    assert all(f.tension_evaluation_id is not None for f in stored)


def test_solo_las_elegibles_se_generan():
    env = _Env()
    item = _toi_item()
    env.add(item, toi_6981_b(item.id), toi_210_b_mass(item.id), hip67522_c(item.id))

    report = env.generator(confirmation_enabled=False)(run_id=env.run_id, dry_run=False)

    names = sorted(f.first_measurement.paper_planet_name for f in report.created)
    assert names == ["TOI-210 b", "TOI-6981 b"], (
        "HIP 67522 c (consistente con la cota) no se publica"
    )


def test_item_status_no_cambia():
    env = _Env()
    item = _toi_item()
    env.add(item, toi_6981_b(item.id))

    env.generator()(run_id=env.run_id, dry_run=False)

    assert env.items.get(item.id).status is ItemStatus.READ


@pytest.mark.parametrize("status", [ItemStatus.DISCARDED, ItemStatus.PUBLISHED, ItemStatus.NEW])
def test_se_genera_con_independencia_del_estado_del_item(status):
    env = _Env()
    item = make_arxiv_item("2609.37597", status=status)
    env.add(item, toi_6981_b(item.id))

    report = env.generator()(run_id=env.run_id, dry_run=False)

    assert len(report.created) == 1
    assert env.items.get(item.id).status is status


def test_idempotente_aunque_el_editor_rechazara_el_primero():
    env = _Env()
    item = _toi_item()
    env.add(item, toi_6981_b(item.id))
    generator = env.generator()

    first = generator(run_id=env.run_id, dry_run=False)
    # El Editor no lo publicó: sigue en la base sin publicar. Otra noche, otro run.
    second = generator(run_id=uuid4(), dry_run=False)

    assert len(first.created) == 1
    assert second.created == () and second.already_generated == 1
    assert len(_stored(env)) == 1


def test_quitar_la_comprobacion_de_ya_generado_se_detecta_con_un_finding_previo():
    env = _Env()
    item = _toi_item()
    evaluation = toi_6981_b(item.id)
    env.add(item, evaluation)
    env.generator()(run_id=env.run_id, dry_run=False)

    again = env.generator()(run_id=env.run_id, dry_run=False)

    assert again.already_generated == 1 and not again.created
    assert len(env.findings.unpublished_for_run(env.run_id)) == 1


def test_respeta_el_tope_k_con_orden_determinista_y_el_resto_se_aplaza():
    env = _Env()
    evaluations = []
    for n in range(6):
        item = _toi_item(f"2609.0000{n}")
        evaluation = toi_6981_b(item.id)
        env.items.save(item)
        evaluations.append(evaluation)
    random.Random(7).shuffle(evaluations)
    for evaluation in evaluations:
        env.evaluations.add(evaluation)

    first = env.generator(max_candidates=2)(run_id=env.run_id, dry_run=False)
    assert len(first.created) == 2 and first.deferred == 4
    externals = [env.items.get(f.item_id).external_id for f in first.created]
    assert externals == ["2609.00000", "2609.00001"], (
        "orden por paper, independiente del repositorio"
    )

    second = env.generator(max_candidates=2)(run_id=uuid4(), dry_run=False)
    assert [env.items.get(f.item_id).external_id for f in second.created] == [
        "2609.00002",
        "2609.00003",
    ], "la cola se vacía de noche en noche"
    assert len(env.findings.unpublished_for_run(env.run_id)) == 2


def test_el_tope_k_es_por_run_y_una_reanudacion_del_mismo_run_no_lo_supera():
    env = _Env()
    for n in range(2):
        item = _toi_item(f"2609.1000{n}")
        env.add(item, toi_6981_b(item.id))
    first = env.generator(max_candidates=3)(run_id=env.run_id, dry_run=False)
    assert len(first.created) == 2

    for n in range(2, 6):
        item = _toi_item(f"2609.1000{n}")
        env.add(item, toi_6981_b(item.id))
    second = env.generator(max_candidates=3)(run_id=env.run_id, dry_run=False)
    third = env.generator(max_candidates=3)(run_id=env.run_id, dry_run=False)

    assert len(second.created) == 1 and second.deferred == 3
    assert third.created == ()
    assert len(_stored(env)) == 3, "como mucho K en total para el run"


def test_tope_cero_no_genera_nada():
    env = _Env()
    item = _toi_item()
    env.add(item, toi_6981_b(item.id))

    report = env.generator(max_candidates=0)(run_id=env.run_id, dry_run=False)

    assert report.created == () and report.deferred == 1


def test_primera_medida_va_antes_que_confirmacion_cuando_hay_tope():
    env = _Env()
    hip_item = make_arxiv_item("2609.35979", status=ItemStatus.READ)
    toi = _toi_item("2609.99999")
    env.add(hip_item, hip67522_b(hip_item.id))
    env.add(toi, toi_6981_b(toi.id))

    report = env.generator(max_candidates=1)(run_id=env.run_id, dry_run=False)

    assert [f.type for f in report.created] == [FindingType.PRIMERA_MEDIDA]
    assert report.deferred == 1


# --- confirmación bloqueada por configuración (D6) --------------------------


def test_con_confirmation_enabled_false_no_se_crea_y_se_cuenta_como_bloqueada():
    env = _Env()
    hip_item = make_arxiv_item("2609.35979", status=ItemStatus.READ)
    env.add(hip_item, hip67522_b(hip_item.id))

    report = env.generator(confirmation_enabled=False)(run_id=env.run_id, dry_run=False)

    assert report.created == ()
    assert len(report.blocked_confirmations) == 1
    blocked = report.blocked_confirmations[0]
    assert (blocked.external_id, blocked.planet_name, blocked.parameter) == (
        "2609.35979",
        "HIP 67522 b",
        "mass",
    )
    assert _stored(env) == []


def test_con_confirmation_enabled_true_se_crea_la_confirmacion():
    env = _Env()
    hip_item = make_arxiv_item("2609.35979", status=ItemStatus.READ)
    evaluation = hip67522_b(hip_item.id)
    env.add(hip_item, evaluation)

    report = env.generator(confirmation_enabled=True)(run_id=env.run_id, dry_run=False)

    assert report.blocked_confirmations == ()
    (finding,) = report.created
    assert finding.type is FindingType.CONFIRMACION_INDEPENDIENTE
    assert finding.tension_evaluation_id == evaluation.id
    confirmation = finding.independent_confirmation
    assert confirmation.archive_url == "https://archive.test/HIP_67522_b"
    assert confirmation.reference.refname == "Chakraborty et al. 2026"
    assert finding.title == "Confirmación independiente de la masa de HIP 67522 b"
    assert "1,42 σ" in finding.level_amateur


def test_confirmacion_fuera_de_ventana_no_se_genera_ni_se_bloquea():
    env = _Env()
    hip_item = make_arxiv_item("2609.35979", status=ItemStatus.READ)
    env.add(hip_item, hip67522_b(hip_item.id, reference=chakraborty_b(releasedate=NOW.date())))

    old_now = datetime(2027, 1, 1, tzinfo=UTC)
    report = env.generator(confirmation_enabled=True, now=old_now)(run_id=env.run_id, dry_run=False)

    assert report.created == () and report.blocked_confirmations == ()


def test_confirmacion_ya_generada_no_se_repite_ni_cuenta_como_bloqueada():
    env = _Env()
    hip_item = make_arxiv_item("2609.35979", status=ItemStatus.READ)
    env.add(hip_item, hip67522_b(hip_item.id))
    env.generator(confirmation_enabled=True)(run_id=env.run_id, dry_run=False)

    report = env.generator(confirmation_enabled=False)(run_id=uuid4(), dry_run=False)

    assert report.created == () and report.blocked_confirmations == ()
    assert report.already_generated == 1


# --- dry-run ------------------------------------------------------------------


def test_dry_run_informa_pero_no_escribe():
    env = _Env()
    toi = _toi_item()
    hip_item = make_arxiv_item("2609.35979", status=ItemStatus.READ)
    env.add(toi, toi_6981_b(toi.id))
    env.add(hip_item, hip67522_b(hip_item.id))

    report = env.generator(confirmation_enabled=False)(run_id=env.run_id, dry_run=True)

    assert report.dry_run is True
    assert [f.type for f in report.created] == [FindingType.PRIMERA_MEDIDA]
    assert len(report.blocked_confirmations) == 1
    assert _stored(env) == []
    # Tras el ensayo, la ejecución real sigue generándolo.
    real = env.generator(confirmation_enabled=False)(run_id=env.run_id, dry_run=False)
    assert len(real.created) == 1


# --- sin gasto -----------------------------------------------------------------


def test_la_unidad_de_trabajo_no_expone_budget_guard():
    assert set(MeasurementFindingsWork.__dataclass_fields__) == {"items", "findings", "evaluations"}


def test_el_modulo_no_conoce_el_proveedor_ni_el_runner_ni_el_guard():
    source = Path(module.__file__).read_text(encoding="utf-8")
    imports = [ln for ln in source.splitlines() if ln.startswith(("import ", "from "))]
    joined = "\n".join(imports)
    for forbidden in ("AgentRunner", "LLMProvider", "BudgetGuard", "budget", "runner", "claude"):
        assert forbidden not in joined, forbidden
    assert "run_agent" not in source


def test_item_inexistente_es_una_violacion_de_invariante():
    env = _Env()
    env.evaluations.add(toi_6981_b(uuid4()))

    with pytest.raises(InvariantViolation):
        env.generator()(run_id=env.run_id, dry_run=False)
