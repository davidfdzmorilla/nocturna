"""T100: `GenerateMeasurementFindings` ignora `absent`, descarta la masa mínima y exige
autores. En memoria; el generador no llama a ningún agente."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

from helpers.measurement_findings import (
    absent_toi_6981_b,
    hip67522_b,
    make_arxiv_item,
    present_without_reference,
    toi_210_b_mass,
)
from test_generate_measurement_findings import _Env, _stored

from nocturna.domain.entities import FindingType, ItemStatus

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
AUTHORS = "A. Otro, B. Distinto"


def _item(external_id: str = "2609.35979", **kw):
    return make_arxiv_item(external_id, status=ItemStatus.READ, **kw)


def test_absent_se_ignora_y_la_presente_sin_solucion_se_genera() -> None:
    env = _Env()
    absent_item = _item("2609.37597")
    present_item = _item("2610.00001")
    env.add(absent_item, absent_toi_6981_b(absent_item.id))
    env.add(present_item, present_without_reference(present_item.id))

    report = env.generator(confirmation_enabled=False)(run_id=env.run_id, dry_run=False)

    assert [f.item_id for f in report.created] == [present_item.id]
    assert report.already_generated == 0 and report.skipped_minimum_mass == 0
    assert len(_stored(env)) == 1


def test_masa_minima_en_primera_medida_se_cuenta_y_no_se_genera() -> None:
    env = _Env()
    abstract = "We detect TOI-210 b with Msin(i) = 6.75 +- 1.25 M_earth."
    item = _item("2610.00002", abstract=abstract)
    ev = toi_210_b_mass(item.id)
    ev = replace(
        ev,
        measurements=tuple(
            replace(m, evidence="Msin(i) = 6.75 +- 1.25 M_earth") for m in ev.measurements
        ),
    )
    env.add(item, ev)

    report = env.generator(confirmation_enabled=False)(run_id=env.run_id, dry_run=False)

    assert report.created == ()
    assert report.skipped_minimum_mass == 1
    assert _stored(env) == []


def test_masa_minima_en_confirmacion_se_cuenta_y_no_se_genera() -> None:
    """Solo cuenta una confirmación que cumplía el resto (equipo distinto)."""
    env = _Env()
    probe = hip67522_b(uuid4())
    first, second = (m.evidence for m in probe.measurements)
    abstract = f"Planet HIP 67522 b has Msin(i) = {first} and also {second}."
    item = _item(abstract=abstract, authors=AUTHORS)
    env.add(item, hip67522_b(item.id))

    report = env.generator()(run_id=env.run_id, dry_run=False)

    assert report.created == ()
    assert report.skipped_minimum_mass == 1


def test_masa_minima_con_el_mismo_equipo_cuenta_como_equipo_no_como_masa_minima() -> None:
    env = _Env()
    probe = hip67522_b(uuid4())
    first, second = (m.evidence for m in probe.measurements)
    abstract = f"Planet HIP 67522 b has Msin(i) = {first} and also {second}."
    item = _item(abstract=abstract, authors="J. Doe, C. Chakraborty")
    env.add(item, hip67522_b(item.id))

    report = env.generator()(run_id=env.run_id, dry_run=False)

    assert report.skipped_same_team == 1
    assert report.skipped_minimum_mass == 0


def test_confirmacion_sin_autores_no_se_genera_y_con_autores_distintos_si() -> None:
    env = _Env()
    no_authors = _item("2609.35979", authors=None)
    env.add(no_authors, hip67522_b(no_authors.id))
    ok = _item("2609.35980", authors=AUTHORS)
    env.add(ok, hip67522_b(ok.id))
    same_team = _item("2609.35981", authors="J. Doe, C. Chakraborty")
    env.add(same_team, hip67522_b(same_team.id))

    report = env.generator()(run_id=env.run_id, dry_run=False)

    assert [(f.type, f.item_id) for f in report.created] == [
        (FindingType.CONFIRMACION_INDEPENDIENTE, ok.id)
    ]
    assert report.skipped_same_team == 1
    assert report.skipped_unknown_team == 1


def test_autores_none_cuenta_como_equipo_desconocido() -> None:
    env = _Env()
    item = _item("2609.35979", authors=None)
    env.add(item, hip67522_b(item.id))

    report = env.generator()(run_id=env.run_id, dry_run=False)

    assert report.created == () and report.blocked_confirmations == ()
    assert report.skipped_unknown_team == 1 and report.skipped_same_team == 0


def test_el_texto_tecnico_de_la_confirmacion_cita_al_primer_autor() -> None:
    env = _Env()
    item = _item(authors=AUTHORS)
    env.add(item, hip67522_b(item.id))

    (finding,) = env.generator()(run_id=env.run_id, dry_run=False).created

    assert "el primer autor de la referencia (Chakraborty) no figura entre los autores" in (
        finding.level_technical
    )
    assert "no se comprueba el resto del equipo ni el método" in finding.level_technical
    assert "no se comprueba si los métodos" not in finding.level_technical


def test_un_dry_run_cuenta_pero_no_escribe() -> None:
    env = _Env()
    item = _item("2610.00003")
    env.add(item, present_without_reference(item.id))

    report = env.generator()(run_id=uuid4(), dry_run=True)

    assert len(report.created) == 1 and report.dry_run
    assert _stored(env) == []


def test_la_seccion_del_dry_run_muestra_los_contadores_de_equipo() -> None:
    from nocturna.application.use_cases.generate_measurement_findings import (
        MeasurementFindingsReport,
    )
    from nocturna.cli import _format_measurement_findings_section

    report = MeasurementFindingsReport(
        created=(),
        blocked_confirmations=(),
        already_generated=0,
        deferred=0,
        dry_run=True,
        skipped_same_team=2,
        skipped_unknown_team=3,
    )

    text = _format_measurement_findings_section(report, external_ids={})

    assert "omitidas por mismo equipo=2" in text
    assert "omitidas por equipo desconocido=3" in text
