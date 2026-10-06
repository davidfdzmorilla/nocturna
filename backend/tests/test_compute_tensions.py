"""Tests del caso de uso `ComputeTensions` (T73, T88) con `FakeExoplanetCatalog`.

Sin red, sin Claude, sin base de datos. El caso de uso produce una
`TensionEvaluation` por (lectura, planeta del Reader, parámetro) y omisiones;
el umbral solo se usa para las cotas superiores.
"""

from datetime import UTC, datetime

import pytest
from fakes.catalog import FakeExoplanetCatalog
from fakes.clock import FakeClock
from helpers.exoplanet import (
    MASS,
    RADIUS,
    make_item,
    make_measurement,
    make_own_solution_rule,
    make_period_rule,
    make_reading,
    make_solution,
)

from nocturna.application.use_cases.compute_tensions import (
    ComputeTensions,
    SkippedMeasurement,
    SkipReason,
    TensionReport,
)
from nocturna.domain.entities import (
    MeasuredParameter,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
)
from nocturna.domain.errors import DomainError
from nocturna.domain.tension import EvaluationStatus

pytestmark = pytest.mark.anyio

V1298 = "V1298 Tau b"
PERIOD = MeasuredParameter.PERIOD
NOW = datetime(2026, 10, 2, 3, 0, tzinfo=UTC)


def compute(catalog) -> ComputeTensions:
    return ComputeTensions(
        catalog,
        threshold_sigma=3.0,
        period_rule=make_period_rule(),
        own_solution_rule=make_own_solution_rule(),
        clock=FakeClock(NOW),
    )


def _livingston():
    return make_solution(0.041, 0.017, 0.017, reference="Livingston et al. 2026", is_default=True)


def _suarez():
    return make_solution(0.64, 0.19, 0.19, reference="Suarez Mascareño et al.")


def _v1298_priors():
    return [
        _livingston(),
        _suarez(),
        make_solution(0.30, 0.10, 0.10, reference="Otro A"),
        make_solution(0.90, 0.30, 0.20, reference="Otro B"),
    ]


def _pair(measurements, external_id="2601.00001"):
    item = make_item(external_id)
    return item, make_reading(item.id, measurements)


def _catalog(solutions=None, aliases=None):
    return FakeExoplanetCatalog(
        aliases=aliases if aliases is not None else {V1298: V1298, "TOI-2109 b": "TOI-2109 b"},
        solutions=solutions or {},
    )


async def test_v1298_b_produce_una_evaluacion_con_4_previas_por_2_medidas():
    measurements = (make_measurement(0.52, 0.12, 0.14), make_measurement(0.67, 0.16, 0.16))
    item, reading = _pair(measurements)
    catalog = _catalog({(V1298, MASS): _v1298_priors()})

    report = await compute(catalog)([(item, reading)])

    assert isinstance(report, TensionReport)
    assert report.skipped == ()
    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.EVALUATED
    assert evaluation.item_id == item.id
    assert evaluation.reading_id == reading.id
    assert evaluation.evaluated_at == NOW
    assert evaluation.planet_name == V1298
    assert evaluation.archive_planet_name == V1298
    assert evaluation.measurements == measurements
    assert evaluation.parameter == MASS
    assert evaluation.result is not None
    assert evaluation.result.reading_id == reading.id
    assert len(evaluation.result.comparisons) == 8
    assert evaluation.is_candidate(3.0) is True


async def test_v1298_b_incluye_default_livingston_y_suarez_mascareno():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),))
    catalog = _catalog({(V1298, MASS): _v1298_priors()})

    report = await compute(catalog)([(item, reading)])

    by_ref = {c.prior.reference: c for c in report.evaluations[0].result.comparisons}
    assert by_ref["Livingston et al. 2026"].sigma == pytest.approx(3.396480, abs=1e-4)
    assert by_ref["Suarez Mascareño et al."].sigma == pytest.approx(
        abs(0.52 - 0.64) / (0.12**2 + 0.19**2) ** 0.5, abs=1e-4
    )


async def test_toi_2109_b_sin_tension_da_sigma_cero_y_no_es_candidato():
    paper = make_measurement(5.02, 0.75, 0.75, planet_name="TOI-2109 b")
    # T83: `pl_pubdate` antiguo, para que un valor idéntico no se tome por el propio paper.
    prior = make_solution(
        5.02, 0.75, 0.75, planet_name="TOI-2109 b", is_default=True, pl_pubdate="2020-01"
    )
    item, reading = _pair((paper,))
    catalog = _catalog({("TOI-2109 b", MASS): [prior]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert [c.sigma for c in evaluation.result.comparisons] == [0.0]
    assert evaluation.is_candidate(3.0) is False


async def test_catalogo_sin_previas_queda_esperando_referencia_con_planeta_de_archivo():
    measurement = make_measurement(0.52, 0.12, 0.14)
    item, reading = _pair((measurement,))
    catalog = _catalog({(V1298, MASS): []})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.AWAITING_REFERENCE
    assert evaluation.archive_planet_name == V1298
    assert evaluation.result is None
    assert report.skipped == ()


async def test_unica_previa_es_la_propia_es_closed_loop_con_su_clave():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),), external_id="2601.00001")
    own = make_solution(0.52, 0.12, 0.14, is_default=True, arxiv_id="2601.00001", solution_key="k1")
    catalog = _catalog({(V1298, MASS): [own]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.CLOSED_LOOP
    assert evaluation.own_solution_key == "k1"
    assert evaluation.result is None


async def test_propia_excluida_con_otras_presentes_y_clave_anotada():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),), external_id="2601.00001")
    own = make_solution(
        0.52, 0.12, 0.14, reference="Este paper", arxiv_id="2601.00001", solution_key="own"
    )
    catalog = _catalog({(V1298, MASS): [own, _livingston(), _suarez()]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    refs = {c.prior.reference for c in evaluation.result.comparisons}
    assert refs == {"Livingston et al. 2026", "Suarez Mascareño et al."}
    assert evaluation.own_solution_key == "own"
    assert evaluation.status == EvaluationStatus.EVALUATED


async def test_propia_es_la_default_y_hay_otras_previas_la_referencia_pasa_a_la_mas_reciente():
    """Decisión T73 (2026-09-30) y T88: si la previa por defecto es la del propio
    paper, se excluye; la referencia pasa a ser la más reciente de las restantes."""
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),), external_id="2601.00001")
    own_default = make_solution(
        0.52, 0.12, 0.14, reference="Este paper", arxiv_id="2601.00001", is_default=True
    )
    catalog = _catalog({(V1298, MASS): [own_default, _suarez()]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert {c.prior.reference for c in evaluation.result.comparisons} == {"Suarez Mascareño et al."}
    assert evaluation.result.reference().reference == "Suarez Mascareño et al."
    assert evaluation.status == EvaluationStatus.EVALUATED


async def test_planeta_sin_resolver_espera_referencia_sin_llamar_a_solutions():
    measurement = make_measurement(1.0, 0.1, 0.1, planet_name="Planeta Desconocido b")
    item, reading = _pair((measurement,))
    catalog = _catalog()

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.AWAITING_REFERENCE
    assert evaluation.archive_planet_name is None
    assert evaluation.planet_name == "Planeta Desconocido b"
    assert report.skipped == ()
    assert catalog.resolve_calls == ["Planeta Desconocido b"]
    assert catalog.solutions_calls == []


@pytest.mark.parametrize(
    "measurement",
    [
        make_measurement(0.52, 0.12, 0.14, origin=MeasurementOrigin.LITERATURE),
        make_measurement(0.52, None, None, limit=MeasurementLimit.UPPER),
        make_measurement(0.52, None, None),
    ],
    ids=["literature", "cota", "sin_error"],
)
async def test_not_usable_no_consulta_el_catalogo(measurement):
    item, reading = _pair((measurement,))
    catalog = _catalog({(V1298, MASS): _v1298_priors()})

    report = await compute(catalog)([(item, reading)])

    assert report.evaluations == ()
    assert report.skipped == (SkippedMeasurement(item.id, measurement, SkipReason.NOT_USABLE),)
    assert catalog.total_calls == 0


@pytest.mark.parametrize("measurements", [None, ()], ids=["none", "vacia"])
async def test_sin_measurements_no_hay_nada_ni_llamadas(measurements):
    item, reading = _pair(measurements)
    catalog = _catalog({(V1298, MASS): _v1298_priors()})

    report = await compute(catalog)([(item, reading)])

    assert report.evaluations == ()
    assert report.skipped == ()
    assert catalog.total_calls == 0


async def test_lista_de_pares_vacia():
    catalog = _catalog()

    report = await compute(catalog)([])

    assert report == TensionReport(evaluations=(), skipped=())
    assert catalog.total_calls == 0


async def test_se_agrupa_por_el_nombre_del_reader_no_por_el_canonico():
    m1 = make_measurement(0.52, 0.12, 0.14, planet_name="V1298 Tau b")
    m2 = make_measurement(0.67, 0.16, 0.16, planet_name="K2-52 b")
    item, reading = _pair((m1, m2))
    catalog = _catalog(
        {(V1298, MASS): [_livingston(), _suarez()]},
        aliases={"V1298 Tau b": V1298, "K2-52 b": V1298},
    )

    report = await compute(catalog)([(item, reading)])

    assert [e.planet_name for e in report.evaluations] == ["V1298 Tau b", "K2-52 b"]
    assert {e.archive_planet_name for e in report.evaluations} == {V1298}
    assert [len(e.result.comparisons) for e in report.evaluations] == [2, 2]


async def test_masa_y_radio_del_mismo_planeta_dan_dos_evaluaciones():
    mass = make_measurement(0.52, 0.12, 0.14)
    radius = make_measurement(0.9, 0.1, 0.1, parameter=RADIUS, unit=MeasurementUnit.R_JUP)
    item, reading = _pair((mass, radius))
    catalog = _catalog(
        {
            (V1298, MASS): [_livingston()],
            (V1298, RADIUS): [
                make_solution(
                    0.85, 0.05, 0.05, parameter=RADIUS, unit=MeasurementUnit.R_JUP, is_default=True
                )
            ],
        }
    )

    report = await compute(catalog)([(item, reading)])

    assert {e.parameter for e in report.evaluations} == {MASS, RADIUS}
    assert all(len(e.result.comparisons) == 1 for e in report.evaluations)


async def test_reading_que_no_casa_con_el_item_es_error():
    item = make_item()
    other = make_item("2601.00002")
    reading = make_reading(other.id, (make_measurement(0.52, 0.12, 0.14),))

    with pytest.raises((DomainError, ValueError)):
        await compute(_catalog())([(item, reading)])


async def test_previas_no_utilizables_se_filtran():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),))
    priors = [
        _livingston(),
        make_solution(0.5, 0.1, 0.1, limit=MeasurementLimit.UPPER, reference="Cota"),
        make_solution(0.5, None, 0.1, reference="Sin error"),
        make_solution(0.5, 0.1, 0.0, reference="Error cero"),
    ]
    catalog = _catalog({(V1298, MASS): priors})

    report = await compute(catalog)([(item, reading)])

    assert [c.prior.reference for c in report.evaluations[0].result.comparisons] == [
        "Livingston et al. 2026"
    ]


async def test_solo_previas_no_published_confirmed_espera_referencia_con_comparaciones():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),))
    catalog = _catalog({(V1298, MASS): [make_solution(0.5, 0.1, 0.1, soltype="Controversial")]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.AWAITING_REFERENCE
    assert evaluation.archive_planet_name == V1298
    assert evaluation.result is not None and evaluation.result.reference() is None


async def test_varios_items_se_procesan_por_separado():
    a_item, a_reading = _pair((make_measurement(0.52, 0.12, 0.14),), "2601.00001")
    b_item, b_reading = _pair((make_measurement(0.67, 0.16, 0.16),), "2601.00002")
    catalog = _catalog({(V1298, MASS): [_livingston()]})

    report = await compute(catalog)([(a_item, a_reading), (b_item, b_reading)])

    assert {e.item_id for e in report.evaluations} == {a_item.id, b_item.id}
    assert {e.reading_id for e in report.evaluations} == {a_reading.id, b_reading.id}


# ---------------------------------------------------------------- cotas (T88)


def _upper(value, **kw):
    return make_solution(value, None, None, limit=MeasurementLimit.UPPER, **kw)


async def test_sin_referencia_y_con_cota_consistente():
    item, reading = _pair((make_measurement(0.5, 0.1, 0.1),))
    catalog = _catalog({(V1298, MASS): [_upper(0.45, reference="Cota")]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.CONSISTENT_WITH_LIMIT
    assert evaluation.limit.limit.reference == "Cota"
    assert evaluation.is_candidate(3.0) is False


async def test_sin_referencia_y_con_cota_incompatible_es_candidato():
    item, reading = _pair((make_measurement(0.9, 0.1, 0.1),))
    catalog = _catalog({(V1298, MASS): [_upper(0.45)]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.INCOMPATIBLE_WITH_LIMIT
    assert evaluation.is_candidate(3.0) is True


async def test_con_referencia_y_cota_gana_la_referencia():
    item, reading = _pair((make_measurement(0.9, 0.1, 0.1),))
    catalog = _catalog({(V1298, MASS): [_livingston(), _upper(0.45)]})

    report = await compute(catalog)([(item, reading)])

    assert report.evaluations[0].status == EvaluationStatus.EVALUATED
    assert report.evaluations[0].limit is None


async def test_cota_propia_no_cuenta_y_sin_nada_mas_es_closed_loop():
    item, reading = _pair((make_measurement(0.9, 0.1, 0.1),), external_id="2601.00001")
    own = _upper(0.45, arxiv_id="2601.00001", solution_key="own-limit")
    catalog = _catalog({(V1298, MASS): [own]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.CLOSED_LOOP
    assert evaluation.own_solution_key == "own-limit"


# ---------------------------------------------------------------- periodo (T88)


def _period(value, ep=0.0001, em=0.0001, **kw):
    return make_measurement(value, ep, em, parameter=PERIOD, unit=MeasurementUnit.DAY, **kw)


def _period_prior(value, ep=0.0001, em=0.0001, **kw):
    # T83: con `pl_pubdate` nulo la fecha cuenta como plausible y un valor casi igual
    # sería la solución propia; las previas ajenas de estos tests llevan fecha antigua.
    kw.setdefault("pl_pubdate", "2020-01")
    return make_solution(
        value, ep, em, parameter=PERIOD, unit=MeasurementUnit.DAY, is_default=True, **kw
    )


async def test_periodo_evaluado_lleva_period_check():
    item, reading = _pair((_period(24.1386),))
    catalog = _catalog({(V1298, PERIOD): [_period_prior(24.13861)]})

    report = await compute(catalog)([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.EVALUATED
    assert evaluation.period_check is not None
    assert evaluation.period_check.alias_suspected is False


async def test_periodo_con_ttv_en_alguna_solucion_se_omite():
    item, reading = _pair((_period(24.1386),))
    catalog = _catalog(
        {
            (V1298, PERIOD): [
                _period_prior(24.13861),
                make_solution(
                    24.2, 0.1, 0.1, parameter=PERIOD, unit=MeasurementUnit.DAY, ttv_flag=True
                ),
            ]
        }
    )

    report = await compute(catalog)([(item, reading)])

    assert report.evaluations == ()
    assert [s.reason for s in report.skipped] == [SkipReason.PERIOD_TTV]


async def test_ttv_no_afecta_a_la_masa():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),))
    catalog = _catalog(
        {(V1298, MASS): [_livingston(), make_solution(0.3, 0.1, 0.1, ttv_flag=True)]}
    )

    report = await compute(catalog)([(item, reading)])

    assert len(report.evaluations) == 1 and report.skipped == ()


async def test_periodo_sospechoso_de_alias_se_marca():
    item, reading = _pair((_period(48.2772),))
    catalog = _catalog({(V1298, PERIOD): [_period_prior(24.1386)]})

    report = await compute(catalog)([(item, reading)])

    assert report.evaluations[0].period_check.alias_suspected is True
