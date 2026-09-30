"""Tests del caso de uso `ComputeTensions` (T73) con `FakeExoplanetCatalog`.

Sin red, sin Claude, sin base de datos. No se aplica umbral aqui: el caso de
uso solo produce comparaciones y omisiones.
"""

import pytest
from fakes.catalog import FakeExoplanetCatalog
from helpers.exoplanet import (
    MASS,
    RADIUS,
    make_item,
    make_measurement,
    make_reading,
    make_solution,
)

from nocturna.application.use_cases.compute_tensions import (
    ComputeTensions,
    SkippedMeasurement,
    SkipReason,
    TensionReport,
)
from nocturna.domain.entities import MeasurementLimit, MeasurementOrigin, MeasurementUnit
from nocturna.domain.errors import DomainError

pytestmark = pytest.mark.anyio

V1298 = "V1298 Tau b"


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


async def test_v1298_b_produce_un_resultado_con_4_previas_por_2_medidas():
    measurements = (make_measurement(0.52, 0.12, 0.14), make_measurement(0.67, 0.16, 0.16))
    item, reading = _pair(measurements)
    catalog = _catalog({(V1298, MASS): _v1298_priors()})

    report = await ComputeTensions(catalog)([(item, reading)])

    assert isinstance(report, TensionReport)
    assert report.skipped == ()
    assert len(report.results) == 1
    result = report.results[0]
    assert result.item_id == item.id
    assert result.planet_name == V1298
    assert result.parameter == MASS
    assert len(result.comparisons) == 8
    assert result.is_candidate(3.0) is True


async def test_v1298_b_incluye_default_livingston_y_suarez_mascareno():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),))
    catalog = _catalog({(V1298, MASS): _v1298_priors()})

    report = await ComputeTensions(catalog)([(item, reading)])

    by_ref = {c.prior.reference: c for c in report.results[0].comparisons}
    assert by_ref["Livingston et al. 2026"].sigma == pytest.approx(3.396480, abs=1e-4)
    assert by_ref["Suarez Mascareño et al."].sigma == pytest.approx(
        abs(0.52 - 0.64) / (0.12**2 + 0.19**2) ** 0.5, abs=1e-4
    )


async def test_toi_2109_b_sin_tension_da_sigma_cero():
    paper = make_measurement(5.02, 0.75, 0.75, planet_name="TOI-2109 b")
    prior = make_solution(5.02, 0.75, 0.75, planet_name="TOI-2109 b", is_default=True)
    item, reading = _pair((paper,))
    catalog = _catalog({("TOI-2109 b", MASS): [prior]})

    report = await ComputeTensions(catalog)([(item, reading)])

    assert [c.sigma for c in report.results[0].comparisons] == [0.0]
    assert report.results[0].is_candidate(3.0) is False


async def test_catalogo_sin_previas_es_no_priors():
    measurement = make_measurement(0.52, 0.12, 0.14)
    item, reading = _pair((measurement,))
    catalog = _catalog({(V1298, MASS): []})

    report = await ComputeTensions(catalog)([(item, reading)])

    assert report.results == ()
    assert report.skipped == (SkippedMeasurement(item.id, measurement, SkipReason.NO_PRIORS),)


async def test_unica_previa_es_la_propia_se_excluye_y_da_no_priors():
    measurement = make_measurement(0.52, 0.12, 0.14)
    item, reading = _pair((measurement,), external_id="2601.00001")
    own = make_solution(0.52, 0.12, 0.14, is_default=True, arxiv_id="2601.00001")
    catalog = _catalog({(V1298, MASS): [own]})

    report = await ComputeTensions(catalog)([(item, reading)])

    assert report.results == ()
    assert [s.reason for s in report.skipped] == [SkipReason.NO_PRIORS]


async def test_propia_excluida_con_otras_presentes():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),), external_id="2601.00001")
    own = make_solution(0.52, 0.12, 0.14, reference="Este paper", arxiv_id="2601.00001")
    catalog = _catalog({(V1298, MASS): [own, _livingston(), _suarez()]})

    report = await ComputeTensions(catalog)([(item, reading)])

    refs = {c.prior.reference for c in report.results[0].comparisons}
    assert refs == {"Livingston et al. 2026", "Suarez Mascareño et al."}


async def test_propia_es_la_default_y_hay_otras_previas_da_resultado_sin_candidato():
    """Decisión T73 (2026-09-30): si la previa por defecto es la del propio
    paper, se excluye; queda resultado con los σ frente a las demás previas,
    pero sin referencia por defecto no hay candidato."""
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),), external_id="2601.00001")
    own_default = make_solution(
        0.52, 0.12, 0.14, reference="Este paper", arxiv_id="2601.00001", is_default=True
    )
    catalog = _catalog({(V1298, MASS): [own_default, _suarez()]})

    report = await ComputeTensions(catalog)([(item, reading)])

    assert len(report.results) == 1
    result = report.results[0]
    assert {c.prior.reference for c in result.comparisons} == {"Suarez Mascareño et al."}
    assert result.is_candidate(0.0) is False


async def test_unmatched_no_llama_a_solutions():
    measurement = make_measurement(1.0, 0.1, 0.1, planet_name="Planeta Desconocido b")
    item, reading = _pair((measurement,))
    catalog = _catalog()

    report = await ComputeTensions(catalog)([(item, reading)])

    assert report.results == ()
    assert report.skipped == (SkippedMeasurement(item.id, measurement, SkipReason.UNMATCHED),)
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

    report = await ComputeTensions(catalog)([(item, reading)])

    assert report.results == ()
    assert report.skipped == (SkippedMeasurement(item.id, measurement, SkipReason.NOT_USABLE),)
    assert catalog.total_calls == 0


@pytest.mark.parametrize("measurements", [None, ()], ids=["none", "vacia"])
async def test_sin_measurements_no_hay_nada_ni_llamadas(measurements):
    item, reading = _pair(measurements)
    catalog = _catalog({(V1298, MASS): _v1298_priors()})

    report = await ComputeTensions(catalog)([(item, reading)])

    assert report.results == ()
    assert report.skipped == ()
    assert catalog.total_calls == 0


async def test_lista_de_pares_vacia():
    catalog = _catalog()

    report = await ComputeTensions(catalog)([])

    assert report == TensionReport(results=(), skipped=())
    assert catalog.total_calls == 0


async def test_dos_alias_del_mismo_canonico_forman_un_solo_grupo():
    m1 = make_measurement(0.52, 0.12, 0.14, planet_name="V1298 Tau b")
    m2 = make_measurement(0.67, 0.16, 0.16, planet_name="K2-52 b")
    item, reading = _pair((m1, m2))
    catalog = _catalog(
        {(V1298, MASS): [_livingston(), _suarez()]},
        aliases={"V1298 Tau b": V1298, "K2-52 b": V1298},
    )

    report = await ComputeTensions(catalog)([(item, reading)])

    assert len(report.results) == 1
    assert report.results[0].planet_name == V1298
    assert len(report.results[0].comparisons) == 4
    assert catalog.solutions_calls == [(V1298, MASS)]


async def test_masa_y_radio_del_mismo_planeta_dan_dos_resultados():
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

    report = await ComputeTensions(catalog)([(item, reading)])

    assert {r.parameter for r in report.results} == {MASS, RADIUS}
    assert len(report.results) == 2
    assert all(len(r.comparisons) == 1 for r in report.results)


async def test_reading_que_no_casa_con_el_item_es_error():
    item = make_item()
    other = make_item("2601.00002")
    reading = make_reading(other.id, (make_measurement(0.52, 0.12, 0.14),))

    with pytest.raises((DomainError, ValueError)):
        await ComputeTensions(_catalog())([(item, reading)])


async def test_previas_no_utilizables_se_filtran():
    item, reading = _pair((make_measurement(0.52, 0.12, 0.14),))
    priors = [
        _livingston(),
        make_solution(0.5, 0.1, 0.1, limit=MeasurementLimit.UPPER, reference="Cota"),
        make_solution(0.5, None, 0.1, reference="Sin error"),
        make_solution(0.5, 0.1, 0.0, reference="Error cero"),
    ]
    catalog = _catalog({(V1298, MASS): priors})

    report = await ComputeTensions(catalog)([(item, reading)])

    assert [c.prior.reference for c in report.results[0].comparisons] == ["Livingston et al. 2026"]


async def test_solo_previas_no_utilizables_da_no_priors():
    measurement = make_measurement(0.52, 0.12, 0.14)
    item, reading = _pair((measurement,))
    priors = [make_solution(0.5, 0.1, 0.1, limit=MeasurementLimit.LOWER)]
    catalog = _catalog({(V1298, MASS): priors})

    report = await ComputeTensions(catalog)([(item, reading)])

    assert report.results == ()
    assert [s.reason for s in report.skipped] == [SkipReason.NO_PRIORS]


async def test_varios_items_se_procesan_por_separado():
    a_item, a_reading = _pair((make_measurement(0.52, 0.12, 0.14),), "2601.00001")
    b_item, b_reading = _pair((make_measurement(0.67, 0.16, 0.16),), "2601.00002")
    catalog = _catalog({(V1298, MASS): [_livingston()]})

    report = await ComputeTensions(catalog)([(a_item, a_reading), (b_item, b_reading)])

    assert {r.item_id for r in report.results} == {a_item.id, b_item.id}
