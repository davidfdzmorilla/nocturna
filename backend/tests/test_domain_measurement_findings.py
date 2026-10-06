"""T89: findings `primera_medida` y `confirmacion_independiente` (dominio puro).

Idioma: español. Sin BD, sin red, sin Claude.
"""

import json
import math
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from helpers.archive import fixture_rows
from helpers.exoplanet import make_measurement, make_own_solution_rule, make_solution

from nocturna.domain.archive import catalog_solution_from_archive
from nocturna.domain.entities import (
    ArchiveStatus,
    ConfirmationReference,
    Finding,
    FindingType,
    FirstMeasurement,
    IndependentConfirmation,
    MeasuredParameter,
    MeasurementLimit,
    MeasurementUnit,
    PaperMeasurement,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.measurement_findings import (
    confirmation_eligible,
    first_measurement_eligible,
    first_measurement_from,
    independent_confirmation_from,
)
from nocturna.domain.tension import (
    EvaluationStatus,
    TensionEvaluation,
    TensionResult,
    compare,
    compare_with_limit,
)
from nocturna.infrastructure.exoplanet_archive.mappers import archive_solution_from_ps_row

MASS = MeasuredParameter.MASS
RADIUS = MeasuredParameter.RADIUS
PERIOD = MeasuredParameter.PERIOD
M_EARTH = MeasurementUnit.M_EARTH
R_EARTH = MeasurementUnit.R_EARTH
NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
OLD_PAPER = datetime(2026, 1, 2, 3, 0, tzinfo=UTC)
RELEASE = date(2026, 10, 1)
MAX_SIGMA = 2.0
WINDOW = 30
EXTERNAL_ID = "2609.35979"
OWN_RULE = make_own_solution_rule()
URL = "https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20b"


def _awaiting(measurements, *, archive_planet_name=None, parameter=RADIUS, **extra):
    return TensionEvaluation(
        reading_id=uuid4(),
        item_id=uuid4(),
        planet_name=measurements[0].planet_name,
        parameter=parameter,
        measurements=tuple(measurements),
        status=EvaluationStatus.AWAITING_REFERENCE,
        evaluated_at=NOW,
        archive_planet_name=archive_planet_name,
        **extra,
    )


def _evaluated(papers, prior):
    planet = prior.planet_name
    result = TensionResult(
        item_id=uuid4(),
        planet_name=planet,
        parameter=prior.parameter,
        comparisons=tuple(compare(m, prior) for m in papers),
    )
    return TensionEvaluation(
        reading_id=uuid4(),
        item_id=result.item_id,
        planet_name=papers[0].planet_name,
        parameter=prior.parameter,
        measurements=tuple(papers),
        status=EvaluationStatus.EVALUATED,
        evaluated_at=NOW,
        archive_planet_name=planet,
        result=result,
    )


def _chakraborty(**overrides):
    """Solución real del archivo para HIP 67522 b: Chakraborty et al. 2026
    (masa 13,8 ± 1,0 M⊕, `releasedate` 2026-10-01), leída de la fila del
    fixture `ps_hip67522_chakraborty2026.csv` y no `is_default`."""
    row = next(
        r
        for r in fixture_rows("ps_hip67522_chakraborty2026.csv")
        if r["pl_name"] == "HIP 67522 b" and r["default_flag"] == "0"
    )
    solution = catalog_solution_from_archive(
        archive_solution_from_ps_row(row), MASS, is_default=False
    )
    assert solution is not None
    return replace(solution, **overrides) if overrides else solution


def _paper(value, err_plus=7.6, err_minus=7.8, *, planet="HIP 67522 b"):
    return make_measurement(value, err_plus, err_minus, planet_name=planet, unit=M_EARTH)


def _hip67522b():
    """Las dos medidas de masa de HIP 67522 b del paper 2609.35979 frente a
    Chakraborty et al. 2026: σ = 1,4242 y 0,7476."""
    return _evaluated([_paper(25.0, 7.6, 7.8), _paper(23.1, 15.4, 12.4)], _chakraborty())


# --- primera_medida -----------------------------------------------------------


def test_toi_6981_b_radio_ausente_es_primera_medida():
    ev = _awaiting(
        [make_measurement(2.4, 0.1, 0.1, planet_name="TOI-6981 b", parameter=RADIUS, unit=R_EARTH)]
    )
    assert first_measurement_eligible(ev)
    fm = first_measurement_from(ev, archive_url=None)
    assert fm.archive_status == ArchiveStatus.ABSENT
    assert fm.archive_planet_name is None and fm.archive_url is None
    assert fm.measurements == (PaperMeasurement(2.4, 0.1, 0.1, R_EARTH),)


def test_toi_210_b_masa_y_radio_son_primeras_medidas():
    mass = _awaiting(
        [make_measurement(6.75, 1.25, 1.25, planet_name="TOI-210 b", unit=M_EARTH)],
        parameter=MASS,
    )
    radius = _awaiting(
        [
            make_measurement(
                2.234, 0.074, 0.074, planet_name="TOI-210 b", parameter=RADIUS, unit=R_EARTH
            )
        ]
    )
    assert first_measurement_eligible(mass) and first_measurement_eligible(radius)
    assert first_measurement_from(mass, archive_url=None).parameter == MASS


def test_planeta_presente_sin_solucion_comparable_es_primera_medida_con_subtipo():
    ev = _awaiting(
        [make_measurement(2.4, 0.1, 0.1, planet_name="X b", parameter=RADIUS, unit=R_EARTH)],
        archive_planet_name="X b",
    )
    fm = first_measurement_from(ev, archive_url="https://a/x")
    assert fm.archive_status == ArchiveStatus.NO_COMPARABLE_SOLUTION
    assert fm.archive_planet_name == "X b" and fm.archive_url == "https://a/x"


def test_primera_medida_ausente_con_url_se_rechaza():
    ev = _awaiting([make_measurement(2.4, 0.1, 0.1, parameter=RADIUS, unit=R_EARTH)])
    with pytest.raises(InvariantViolation):
        first_measurement_from(ev, archive_url="https://a/x")


def test_periodo_no_es_primera_medida():
    ev = _awaiting(
        [make_measurement(3.5, 0.01, 0.01, parameter=PERIOD, unit=MeasurementUnit.DAY)],
        parameter=PERIOD,
    )
    assert not first_measurement_eligible(ev)
    with pytest.raises(InvariantViolation):
        first_measurement_from(ev, archive_url=None)


def test_hip_67522_c_consistent_with_limit_no_es_elegible():
    paper = make_measurement(10.0, 5.0, 5.0, planet_name="HIP 67522 c", unit=M_EARTH)
    limit = make_solution(
        22.0, None, None, planet_name="HIP 67522 c", unit=M_EARTH, limit=MeasurementLimit.UPPER
    )
    comparison = compare_with_limit([paper], limit, threshold_sigma=2.0)
    ev = TensionEvaluation(
        reading_id=uuid4(),
        item_id=uuid4(),
        planet_name="HIP 67522 c",
        parameter=MASS,
        measurements=(paper,),
        status=EvaluationStatus.CONSISTENT_WITH_LIMIT,
        evaluated_at=NOW,
        archive_planet_name="HIP 67522 c",
        limit=comparison,
    )
    assert not first_measurement_eligible(ev)
    assert not confirmation_eligible(
        ev,
        item_published_at=NOW,
        now=NOW,
        max_sigma=MAX_SIGMA,
        window_days=WINDOW,
        item_external_id=EXTERNAL_ID,
        own_rule=OWN_RULE,
    )


def test_closed_loop_y_awaiting_con_solucion_propia_no_son_primera_medida():
    ev = _awaiting(
        [make_measurement(2.4, 0.1, 0.1, parameter=RADIUS, unit=R_EARTH)],
        archive_planet_name="V1298 Tau b",
        own_solution_key="k",
    )
    assert not first_measurement_eligible(ev)
    assert not first_measurement_eligible(_hip67522b())


# --- confirmacion_independiente ----------------------------------------------


def _eligible(ev, *, published=OLD_PAPER, now=NOW, max_sigma=MAX_SIGMA, window=WINDOW):
    return confirmation_eligible(
        ev,
        item_published_at=published,
        now=now,
        max_sigma=max_sigma,
        window_days=window,
        item_external_id=EXTERNAL_ID,
        own_rule=OWN_RULE,
    )


def test_hip_67522_b_frente_a_chakraborty_es_confirmacion():
    ev = _hip67522b()
    sigmas = [c.sigma for c in ev.result.comparisons]
    assert sigmas == pytest.approx([1.4242, 0.7476], abs=5e-5)
    assert not ev.result.reference().is_default
    assert _eligible(ev)

    ic = independent_confirmation_from(
        ev,
        item_published_at=OLD_PAPER,
        now=NOW,
        max_sigma=MAX_SIGMA,
        window_days=WINDOW,
        archive_url=URL,
        item_external_id=EXTERNAL_ID,
        own_rule=OWN_RULE,
    )
    assert ic.sigmas == pytest.approx((1.4242, 0.7476), abs=5e-5)
    assert ic.reference.refname == "Chakraborty et al. 2026"
    assert ic.reference.arxiv_id == "2606.18045"
    assert (ic.reference.value, ic.reference.err_plus, ic.reference.err_minus) == (13.8, 1.0, 1.0)
    assert ic.reference_releasedate == RELEASE
    assert (ic.max_sigma, ic.window_days) == (2.0, 30)
    assert ic.paper_published_at == OLD_PAPER


def test_frontera_de_sigma_exacta_si_y_un_epsilon_por_debajo_no():
    """σ <= max_sigma: con el umbral igual al σ de la medida es elegible; un
    epsilon por debajo, no (el `<=` no puede ser `<`)."""
    ev = _evaluated([_paper(25.0)], _chakraborty())
    sigma = ev.result.comparisons[0].sigma
    assert _eligible(ev, max_sigma=sigma)
    assert not _eligible(ev, max_sigma=math.nextafter(sigma, 0.0))
    # Con los umbrales de la configuración (2,0): 1,42 sí, 2,01 no.
    assert _eligible(ev)
    far = _evaluated([_paper(13.8 + 2.01 * math.hypot(7.8, 1.0))], _chakraborty())
    assert not _eligible(far)


def test_si_una_medida_supera_el_umbral_no_es_confirmacion():
    one = 13.8 + 1.0 * math.hypot(7.8, 1.0)
    two_and_half = 13.8 + 2.5 * math.hypot(7.8, 1.0)
    ev = _evaluated([_paper(one), _paper(two_and_half)], _chakraborty())  # σ 1,0 y 2,5
    assert not _eligible(ev)


def test_tension_alta_no_es_confirmacion():
    assert not _eligible(_evaluated([_paper(40.0)], _chakraborty()))


@pytest.mark.parametrize(("days", "expected"), [(30, True), (31, False), (0, True)])
def test_frontera_de_ventana_de_30_y_31_dias(days, expected):
    release = NOW.date() - timedelta(days=days)
    ev = _evaluated([_paper(22.0)], _chakraborty(releasedate=release))
    assert _eligible(ev) is expected


def test_releasedate_en_el_futuro_cuenta_como_dentro_de_la_ventana():
    """Fijado: una fecha futura (reloj desfasado, fecha del archivo adelantada)
    no saca la evaluación de la ventana."""
    ev = _evaluated([_paper(22.0)], _chakraborty(releasedate=NOW.date() + timedelta(days=5)))
    assert _eligible(ev)


def test_published_at_en_el_futuro_cuenta_como_dentro_de_la_ventana():
    ev = _evaluated([_paper(22.0)], _chakraborty(releasedate=date(2026, 1, 1)))
    assert _eligible(ev, published=NOW + timedelta(days=5))


MADRID = ZoneInfo("Europe/Madrid")
NOW_MADRID = datetime(2026, 10, 5, 3, 0, tzinfo=MADRID)


@pytest.mark.parametrize(
    ("published_utc", "expected"),
    [
        # 23:30 UTC del 4-sep son las 01:30 del 5-sep en Madrid (CEST): 30 días, dentro.
        (datetime(2026, 9, 4, 23, 30, tzinfo=UTC), True),
        # 23:30 UTC del 3-sep son las 01:30 del 4-sep en Madrid: 31 días, fuera.
        (datetime(2026, 9, 3, 23, 30, tzinfo=UTC), False),
    ],
)
def test_published_at_cerca_de_medianoche_utc_usa_el_dia_de_europe_madrid(published_utc, expected):
    ev = _evaluated([_paper(22.0)], _chakraborty(releasedate=date(2026, 1, 1)))
    assert _eligible(ev, published=published_utc, now=NOW_MADRID) is expected


def test_la_mas_reciente_de_las_dos_entradas_cuenta():
    ev = _evaluated([_paper(22.0)], _chakraborty(releasedate=date(2026, 1, 1)))
    assert not _eligible(ev)
    assert _eligible(ev, published=NOW - timedelta(days=30))
    assert not _eligible(ev, published=NOW - timedelta(days=31))


def test_referencia_sin_releasedate_no_es_elegible():
    ev = _evaluated([_paper(22.0)], _chakraborty(releasedate=None))
    assert not _eligible(ev)


def test_periodo_evaluado_no_es_confirmacion():
    prior = make_solution(
        3.5,
        0.01,
        0.01,
        parameter=PERIOD,
        unit=MeasurementUnit.DAY,
        releasedate=RELEASE,
        planet_name="HIP 67522 b",
    )
    paper = make_measurement(
        3.5, 0.01, 0.01, parameter=PERIOD, unit=MeasurementUnit.DAY, planet_name="HIP 67522 b"
    )
    result = TensionResult(
        item_id=uuid4(),
        planet_name="HIP 67522 b",
        parameter=PERIOD,
        comparisons=(compare(paper, prior),),
    )
    from nocturna.domain.tension import PeriodCheck

    ev = TensionEvaluation(
        reading_id=uuid4(),
        item_id=result.item_id,
        planet_name="HIP 67522 b",
        parameter=PERIOD,
        measurements=(paper,),
        status=EvaluationStatus.EVALUATED,
        evaluated_at=NOW,
        archive_planet_name="HIP 67522 b",
        result=result,
        period_check=PeriodCheck(min_difference_met=False, alias_suspected=False),
    )
    assert not _eligible(ev)
    with pytest.raises(InvariantViolation):
        independent_confirmation_from(
            ev,
            item_published_at=OLD_PAPER,
            now=NOW,
            max_sigma=2.0,
            window_days=30,
            archive_url=URL,
            item_external_id=EXTERNAL_ID,
            own_rule=OWN_RULE,
        )


def test_confirmacion_no_elegible_lanza_en_la_factoria():
    ev = _evaluated([_paper(40.0)], _chakraborty())
    with pytest.raises(InvariantViolation):
        independent_confirmation_from(
            ev,
            item_published_at=OLD_PAPER,
            now=NOW,
            max_sigma=2.0,
            window_days=30,
            archive_url=URL,
            item_external_id=EXTERNAL_ID,
            own_rule=OWN_RULE,
        )


# --- value objects y JSON ----------------------------------------------------


def _first(**over):
    kwargs = dict(
        paper_planet_name="TOI-6981 b",
        archive_planet_name=None,
        parameter=RADIUS,
        archive_status=ArchiveStatus.ABSENT,
        measurements=(PaperMeasurement(2.4, 0.1, 0.1, R_EARTH),),
    )
    kwargs.update(over)
    return FirstMeasurement(**kwargs)


def _confirmation():
    return independent_confirmation_from(
        _hip67522b(),
        item_published_at=OLD_PAPER,
        now=NOW,
        max_sigma=2.0,
        window_days=30,
        archive_url=URL,
        item_external_id=EXTERNAL_ID,
        own_rule=OWN_RULE,
    )


def test_json_de_first_measurement_y_ida_y_vuelta():
    fm = _first()
    raw = fm.to_json()
    assert raw == {
        "schema_version": 1,
        "paper_planet_name": "TOI-6981 b",
        "archive_planet_name": None,
        "parameter": "radius",
        "archive_status": "absent",
        "archive_url": None,
        "measurements": [{"value": 2.4, "err_plus": 0.1, "err_minus": 0.1, "unit": "R_earth"}],
    }
    assert FirstMeasurement.from_json(json.loads(json.dumps(raw))) == fm


def test_json_de_independent_confirmation_y_ida_y_vuelta():
    ic = _confirmation()
    raw = json.loads(json.dumps(ic.to_json()))
    assert raw["schema_version"] == 1
    assert raw["reference"]["releasedate"] == "2026-10-01"
    assert raw["reference"]["refname"] == "Chakraborty et al. 2026"
    assert raw["paper_published_at"] == OLD_PAPER.isoformat()
    assert IndependentConfirmation.from_json(raw) == ic
    assert "evidence" not in json.dumps(raw)


@pytest.mark.parametrize("cls_raw", [lambda: _first().to_json(), lambda: _confirmation().to_json()])
@pytest.mark.parametrize("version", [None, 0, 2, "1", True])
def test_schema_version_ausente_o_distinta_da_error(cls_raw, version):
    raw = cls_raw()
    if version is None:
        del raw["schema_version"]
    else:
        raw["schema_version"] = version
    cls = FirstMeasurement if "archive_status" in raw else IndependentConfirmation
    with pytest.raises(ValueError, match="schema_version"):
        cls.from_json(raw)


def test_invariantes_de_first_measurement():
    ok = PaperMeasurement(2.4, 0.1, 0.1, R_EARTH)
    with pytest.raises(InvariantViolation):
        _first(archive_planet_name="X b")  # absent con nombre
    with pytest.raises(InvariantViolation):
        _first(archive_status=ArchiveStatus.NO_COMPARABLE_SOLUTION)  # sin nombre
    with pytest.raises(InvariantViolation):
        _first(archive_url="https://x")  # absent con url
    with pytest.raises(InvariantViolation):
        _first(
            parameter=PERIOD, measurements=(PaperMeasurement(3.0, 0.1, 0.1, MeasurementUnit.DAY),)
        )
    with pytest.raises(InvariantViolation):
        _first(measurements=())
    with pytest.raises(InvariantViolation):
        _first(measurements=(PaperMeasurement(2.4, 0.1, 0.1, M_EARTH),))  # unidad incoherente
    with pytest.raises(InvariantViolation):
        _first(paper_planet_name=" ")
    assert _first(measurements=(ok,)).measurements == (ok,)


def test_invariantes_de_paper_measurement_y_confirmacion():
    for bad in (0.0, -1.0, math.nan, math.inf):
        with pytest.raises(InvariantViolation):
            PaperMeasurement(bad, 0.1, 0.1, R_EARTH)
    with pytest.raises(InvariantViolation):
        PaperMeasurement(1.0, -0.1, 0.1, R_EARTH)
    ic = _confirmation()
    with pytest.raises(InvariantViolation):
        replace(ic, sigmas=(1.0,))  # un σ por medida
    with pytest.raises(InvariantViolation):
        replace(ic, sigmas=(1.0, 2.5))  # > max_sigma
    with pytest.raises(InvariantViolation):
        replace(ic, window_days=0)
    with pytest.raises(InvariantViolation):
        replace(ic, paper_published_at=datetime(2026, 1, 1))
    with pytest.raises(InvariantViolation):
        ConfirmationReference("r", None, 1.0, 0.0, 0.1, M_EARTH, RELEASE)


# --- Finding: invariantes "si y solo si" por tipo ----------------------------


def _finding(type_, **extra):
    return Finding(
        item_id=uuid4(),
        run_id=uuid4(),
        type=type_,
        title="t",
        level_curious="c",
        level_amateur="a",
        level_technical="x",
        **extra,
    )


def test_finding_primera_medida_y_confirmacion_validos():
    ev_id = uuid4()
    f1 = _finding(
        FindingType.PRIMERA_MEDIDA, first_measurement=_first(), tension_evaluation_id=ev_id
    )
    f2 = _finding(
        FindingType.CONFIRMACION_INDEPENDIENTE,
        independent_confirmation=_confirmation(),
        tension_evaluation_id=ev_id,
    )
    assert f1.first_measurement is not None and f2.tension_evaluation_id == ev_id
    assert FindingType.PRIMERA_MEDIDA.value == "primera_medida"
    assert FindingType.CONFIRMACION_INDEPENDIENTE.value == "confirmacion_independiente"


def test_finding_invariantes_si_y_solo_si_para_los_cuatro_tipos():
    ev_id = uuid4()
    fm, ic = _first(), _confirmation()
    # sin payload / sin evaluación en los tipos nuevos
    with pytest.raises(InvariantViolation):
        _finding(FindingType.PRIMERA_MEDIDA, tension_evaluation_id=ev_id)
    with pytest.raises(InvariantViolation):
        _finding(FindingType.PRIMERA_MEDIDA, first_measurement=fm)
    with pytest.raises(InvariantViolation):
        _finding(FindingType.CONFIRMACION_INDEPENDIENTE, independent_confirmation=ic)
    with pytest.raises(InvariantViolation):
        _finding(FindingType.CONFIRMACION_INDEPENDIENTE, tension_evaluation_id=ev_id)
    # payload cruzado
    with pytest.raises(InvariantViolation):
        _finding(
            FindingType.PRIMERA_MEDIDA, independent_confirmation=ic, tension_evaluation_id=ev_id
        )
    with pytest.raises(InvariantViolation):
        _finding(
            FindingType.PRIMERA_MEDIDA,
            first_measurement=fm,
            independent_confirmation=ic,
            tension_evaluation_id=ev_id,
        )
    # payloads u evaluación en los tipos antiguos
    with pytest.raises(InvariantViolation):
        _finding(FindingType.PAPER_EXPLAINED, first_measurement=fm)
    with pytest.raises(InvariantViolation):
        _finding(FindingType.PAPER_EXPLAINED, independent_confirmation=ic)
    with pytest.raises(InvariantViolation):
        _finding(FindingType.PAPER_EXPLAINED, tension_evaluation_id=ev_id)
    with pytest.raises(InvariantViolation):
        _finding(FindingType.CATALOG_TENSION, tension_evaluation_id=ev_id)
    with pytest.raises(InvariantViolation):
        _finding(FindingType.CATALOG_TENSION)  # sin catalog_tension
    assert _finding(FindingType.PAPER_EXPLAINED).tension_evaluation_id is None
