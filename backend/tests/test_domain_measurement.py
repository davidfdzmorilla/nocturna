"""Tests del value object `Measurement` y de `Reading.measurements` (T71.c).

Cubren cada invariante de `Measurement` por separado, la tabla de verdad de
`usable_for_tension`, la distinción `None`/`()` de `Reading.measurements` y
sus rechazos (no-tupla, elemento no `Measurement`). Ningún test llama a
Claude, a la red ni a la base de datos.
"""

from uuid import uuid4

import pytest

from nocturna.domain.entities import (
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
    Reading,
)
from nocturna.domain.errors import InvariantViolation


def _base_measurement_kwargs() -> dict:
    return dict(
        planet_name="TOI-2109 b",
        parameter=MeasuredParameter.MASS,
        value=2.8,
        err_plus=0.5,
        err_minus=0.5,
        unit=MeasurementUnit.M_JUP,
        limit=MeasurementLimit.NONE,
        origin=MeasurementOrigin.THIS_WORK,
        evidence="2.8 (+0.5/-0.5) M_J",
    )


def _base_reading_kwargs() -> dict:
    return dict(
        item_id=uuid4(),
        summary="Resumen",
        objects=["TOI-2109"],
        claims=["Una afirmación"],
        interest_score=3,
        tokens_in=10,
        tokens_out=5,
        model="sonnet",
    )


# --- construcción válida ---------------------------------------------------


def test_measurement_construccion_valida():
    measurement = Measurement(**_base_measurement_kwargs())

    assert measurement.planet_name == "TOI-2109 b"
    assert measurement.parameter == MeasuredParameter.MASS
    assert measurement.value == 2.8
    assert measurement.unit == MeasurementUnit.M_JUP


def test_measurement_errores_none_es_valido():
    kwargs = _base_measurement_kwargs()
    kwargs["err_plus"] = None
    kwargs["err_minus"] = None

    measurement = Measurement(**kwargs)

    assert measurement.err_plus is None
    assert measurement.err_minus is None


# --- invariante: value finito -----------------------------------------------


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_measurement_value_no_finito_falla(bad_value):
    kwargs = _base_measurement_kwargs()
    kwargs["value"] = bad_value

    with pytest.raises(InvariantViolation):
        Measurement(**kwargs)


# --- invariante: value > 0 (T71.c, decisión del autor, revisión 2) ---------


def test_measurement_value_cero_falla():
    """Decisión del autor (revisión 2 de T71.c): masa, radio y periodo son
    magnitudes físicas estrictamente positivas; `value == 0` no es una
    medida válida de ninguno de los tres parámetros del esquema."""
    kwargs = _base_measurement_kwargs()
    kwargs["value"] = 0.0

    with pytest.raises(InvariantViolation):
        Measurement(**kwargs)


def test_measurement_value_negativo_falla():
    kwargs = _base_measurement_kwargs()
    kwargs["value"] = -2.8

    with pytest.raises(InvariantViolation):
        Measurement(**kwargs)


# --- invariante: err_plus / err_minus ---------------------------------------


@pytest.mark.parametrize("err_field_name", ["err_plus", "err_minus"])
@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), float("-inf")])
def test_measurement_error_no_finito_falla(err_field_name, bad_value):
    kwargs = _base_measurement_kwargs()
    kwargs[err_field_name] = bad_value

    with pytest.raises(InvariantViolation):
        Measurement(**kwargs)


@pytest.mark.parametrize("err_field_name", ["err_plus", "err_minus"])
def test_measurement_error_negativo_falla(err_field_name):
    kwargs = _base_measurement_kwargs()
    kwargs[err_field_name] = -0.1

    with pytest.raises(InvariantViolation):
        Measurement(**kwargs)


@pytest.mark.parametrize("err_field_name", ["err_plus", "err_minus"])
def test_measurement_error_cero_es_valido(err_field_name):
    kwargs = _base_measurement_kwargs()
    kwargs[err_field_name] = 0.0

    measurement = Measurement(**kwargs)

    assert getattr(measurement, err_field_name) == 0.0


# --- invariante: unidad coherente con el parámetro --------------------------


@pytest.mark.parametrize(
    "parameter,valid_units,invalid_unit",
    [
        (
            MeasuredParameter.MASS,
            (MeasurementUnit.M_JUP, MeasurementUnit.M_EARTH),
            MeasurementUnit.R_JUP,
        ),
        (
            MeasuredParameter.RADIUS,
            (MeasurementUnit.R_JUP, MeasurementUnit.R_EARTH),
            MeasurementUnit.M_JUP,
        ),
        (MeasuredParameter.PERIOD, (MeasurementUnit.DAY,), MeasurementUnit.M_JUP),
    ],
)
def test_measurement_unidad_coherente_con_el_parametro_es_valida(
    parameter, valid_units, invalid_unit
):
    for unit in valid_units:
        kwargs = _base_measurement_kwargs()
        kwargs["parameter"] = parameter
        kwargs["unit"] = unit

        measurement = Measurement(**kwargs)

        assert measurement.unit == unit


@pytest.mark.parametrize(
    "parameter,invalid_unit",
    [
        (MeasuredParameter.MASS, MeasurementUnit.R_JUP),
        (MeasuredParameter.MASS, MeasurementUnit.R_EARTH),
        (MeasuredParameter.MASS, MeasurementUnit.DAY),
        (MeasuredParameter.RADIUS, MeasurementUnit.M_JUP),
        (MeasuredParameter.RADIUS, MeasurementUnit.M_EARTH),
        (MeasuredParameter.RADIUS, MeasurementUnit.DAY),
        (MeasuredParameter.PERIOD, MeasurementUnit.M_JUP),
        (MeasuredParameter.PERIOD, MeasurementUnit.M_EARTH),
        (MeasuredParameter.PERIOD, MeasurementUnit.R_JUP),
        (MeasuredParameter.PERIOD, MeasurementUnit.R_EARTH),
    ],
)
def test_measurement_unidad_incoherente_con_el_parametro_falla(parameter, invalid_unit):
    kwargs = _base_measurement_kwargs()
    kwargs["parameter"] = parameter
    kwargs["unit"] = invalid_unit

    with pytest.raises(InvariantViolation):
        Measurement(**kwargs)


# --- invariante: planet_name / evidence en blanco ---------------------------


@pytest.mark.parametrize("field_name", ["planet_name", "evidence"])
def test_measurement_campo_de_texto_en_blanco_falla(field_name):
    kwargs = _base_measurement_kwargs()
    kwargs[field_name] = "   "

    with pytest.raises(InvariantViolation):
        Measurement(**kwargs)


# --- tabla de verdad de usable_for_tension ----------------------------------


@pytest.mark.parametrize(
    "origin,limit,err_plus,err_minus,expected",
    [
        (MeasurementOrigin.THIS_WORK, MeasurementLimit.NONE, 0.5, 0.5, True),
        (MeasurementOrigin.THIS_WORK, MeasurementLimit.NONE, 0.0, 0.0, True),
        (MeasurementOrigin.LITERATURE, MeasurementLimit.NONE, 0.5, 0.5, False),
        (MeasurementOrigin.THIS_WORK, MeasurementLimit.UPPER, 0.5, 0.5, False),
        (MeasurementOrigin.THIS_WORK, MeasurementLimit.LOWER, 0.5, 0.5, False),
        (MeasurementOrigin.THIS_WORK, MeasurementLimit.NONE, None, 0.5, False),
        (MeasurementOrigin.THIS_WORK, MeasurementLimit.NONE, 0.5, None, False),
        (MeasurementOrigin.THIS_WORK, MeasurementLimit.NONE, None, None, False),
        (MeasurementOrigin.LITERATURE, MeasurementLimit.UPPER, None, None, False),
    ],
    ids=[
        "this_work-none-con_errores",
        "this_work-none-errores_cero",
        "literature-none-con_errores",
        "this_work-upper-con_errores",
        "this_work-lower-con_errores",
        "this_work-none-sin_err_plus",
        "this_work-none-sin_err_minus",
        "this_work-none-sin_errores",
        "literature-upper-sin_errores",
    ],
)
def test_usable_for_tension_tabla_de_verdad(origin, limit, err_plus, err_minus, expected):
    kwargs = _base_measurement_kwargs()
    kwargs["origin"] = origin
    kwargs["limit"] = limit
    kwargs["err_plus"] = err_plus
    kwargs["err_minus"] = err_minus

    measurement = Measurement(**kwargs)

    assert measurement.usable_for_tension is expected


# --- Reading.measurements: None / () / tupla válida -------------------------


def test_reading_measurements_none_es_valido():
    reading = Reading(**_base_reading_kwargs())

    assert reading.measurements is None


def test_reading_measurements_tupla_vacia_es_valida():
    kwargs = _base_reading_kwargs()
    kwargs["measurements"] = ()

    reading = Reading(**kwargs)

    assert reading.measurements == ()


def test_reading_measurements_tupla_valida_con_measurement():
    measurement = Measurement(**_base_measurement_kwargs())
    kwargs = _base_reading_kwargs()
    kwargs["measurements"] = (measurement,)

    reading = Reading(**kwargs)

    assert reading.measurements == (measurement,)


# --- Reading.measurements: rechazos ------------------------------------------


def test_reading_measurements_lista_en_vez_de_tupla_falla():
    measurement = Measurement(**_base_measurement_kwargs())
    kwargs = _base_reading_kwargs()
    kwargs["measurements"] = [measurement]

    with pytest.raises(InvariantViolation):
        Reading(**kwargs)


def test_reading_measurements_elemento_no_measurement_falla():
    kwargs = _base_reading_kwargs()
    kwargs["measurements"] = ("no soy un Measurement",)

    with pytest.raises(InvariantViolation):
        Reading(**kwargs)


# --- enums congelados --------------------------------------------------------


def test_measured_parameter_valores_congelados():
    assert {member.value for member in MeasuredParameter} == {"mass", "radius", "period"}


def test_measurement_unit_valores_congelados():
    assert {member.value for member in MeasurementUnit} == {
        "M_jup",
        "M_earth",
        "R_jup",
        "R_earth",
        "day",
    }


def test_measurement_limit_valores_congelados():
    assert {member.value for member in MeasurementLimit} == {"none", "upper", "lower"}


def test_measurement_origin_valores_congelados():
    assert {member.value for member in MeasurementOrigin} == {"this_work", "literature"}
