"""Espejo, paramétrico, entre `MeasurementOut` (validación de JSON crudo,
`application/agents/reader_output.py`) y `Measurement` (invariantes de
dominio, `domain/entities.py`) para T71.c.

`MeasurementOut` existe precisamente para replicar, sobre JSON no confiable,
cada invariante que `Measurement.__post_init__` exige sobre datos ya
tipados (mismo criterio que `ReaderOutput` frente a `Reading`, ver el
docstring de `reader_output.py`). Este fichero no repite los tests que ya
cubren cada clase por separado (`tests/test_domain_measurement.py`,
`tests/test_reader_output_parsing.py` -- si tuviera casos de `MeasurementOut`,
que hoy no tenía ninguno): aquí el punto es que, para el MISMO caso, las dos
capas acepten o rechacen exactamente lo mismo. Si un día divergen -- por
ejemplo, alguien relaja `MeasurementOut` sin tocar `Measurement`, o al
revés -- este fichero se pone en rojo aunque cada test individual de las
otras dos suites siga en verde.

Puro: sin IO, sin red, sin base de datos. Ningún test llama a Claude.
"""

import pytest

from nocturna.application.agents.reader_output import MalformedMeasurement, parse_measurement
from nocturna.domain.entities import (
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
)
from nocturna.domain.errors import InvariantViolation


def _base_raw(**overrides: object) -> dict:
    payload = {
        "planet_name": "Kepler-0000 b",
        "parameter": "mass",
        "value": 2.8,
        "err_plus": 0.5,
        "err_minus": 0.5,
        "unit": "M_jup",
        "limit": "none",
        "origin": "this_work",
        "evidence": "a mass of 2.8 M_jup",
    }
    payload.update(overrides)
    return payload


def _base_entity_kwargs(**overrides: object) -> dict:
    kwargs = {
        "planet_name": "Kepler-0000 b",
        "parameter": MeasuredParameter.MASS,
        "value": 2.8,
        "err_plus": 0.5,
        "err_minus": 0.5,
        "unit": MeasurementUnit.M_JUP,
        "limit": MeasurementLimit.NONE,
        "origin": MeasurementOrigin.THIS_WORK,
        "evidence": "a mass of 2.8 M_jup",
    }
    kwargs.update(overrides)
    return kwargs


# Cada caso: (id, override para el raw de MeasurementOut, override para los
# kwargs de Measurement -- la MISMA regla expresada en el vocabulario de cada
# capa --, si el caso debe ser válido en AMBAS capas).
_CASES = [
    ("value_nan_invalido", {"value": float("nan")}, {"value": float("nan")}, False),
    ("value_inf_invalido", {"value": float("inf")}, {"value": float("inf")}, False),
    ("value_neg_inf_invalido", {"value": float("-inf")}, {"value": float("-inf")}, False),
    ("value_finito_valido", {"value": 10.0}, {"value": 10.0}, True),
    ("value_cero_invalido", {"value": 0.0}, {"value": 0.0}, False),
    ("value_negativo_invalido", {"value": -2.8}, {"value": -2.8}, False),
    ("err_plus_negativo_invalido", {"err_plus": -0.1}, {"err_plus": -0.1}, False),
    ("err_minus_negativo_invalido", {"err_minus": -0.1}, {"err_minus": -0.1}, False),
    (
        "err_plus_nan_invalido",
        {"err_plus": float("nan")},
        {"err_plus": float("nan")},
        False,
    ),
    (
        "err_minus_inf_invalido",
        {"err_minus": float("inf")},
        {"err_minus": float("inf")},
        False,
    ),
    ("err_plus_none_valido", {"err_plus": None}, {"err_plus": None}, True),
    ("err_minus_none_valido", {"err_minus": None}, {"err_minus": None}, True),
    ("err_plus_cero_valido", {"err_plus": 0.0}, {"err_plus": 0.0}, True),
    (
        "unit_incoherente_con_mass_invalido",
        {"unit": "R_jup"},
        {"unit": MeasurementUnit.R_JUP},
        False,
    ),
    (
        "unit_incoherente_con_mass_day_invalido",
        {"unit": "day"},
        {"unit": MeasurementUnit.DAY},
        False,
    ),
    (
        "unit_coherente_con_mass_m_earth_valido",
        {"unit": "M_earth"},
        {"unit": MeasurementUnit.M_EARTH},
        True,
    ),
    (
        "parameter_radius_con_unit_r_earth_valido",
        {"parameter": "radius", "unit": "R_earth"},
        {"parameter": MeasuredParameter.RADIUS, "unit": MeasurementUnit.R_EARTH},
        True,
    ),
    (
        "parameter_radius_con_unit_incoherente_invalido",
        {"parameter": "radius", "unit": "M_jup"},
        {"parameter": MeasuredParameter.RADIUS, "unit": MeasurementUnit.M_JUP},
        False,
    ),
    (
        "parameter_period_con_unit_day_valido",
        {"parameter": "period", "unit": "day"},
        {"parameter": MeasuredParameter.PERIOD, "unit": MeasurementUnit.DAY},
        True,
    ),
    (
        "parameter_period_con_unit_incoherente_invalido",
        {"parameter": "period", "unit": "R_jup"},
        {"parameter": MeasuredParameter.PERIOD, "unit": MeasurementUnit.R_JUP},
        False,
    ),
    ("planet_name_en_blanco_invalido", {"planet_name": "   "}, {"planet_name": "   "}, False),
    ("planet_name_vacio_invalido", {"planet_name": ""}, {"planet_name": ""}, False),
    ("evidence_en_blanco_invalido", {"evidence": "   "}, {"evidence": "   "}, False),
    ("evidence_vacio_invalido", {"evidence": ""}, {"evidence": ""}, False),
]


@pytest.mark.parametrize(
    "raw_override,entity_override,valid", [c[1:] for c in _CASES], ids=[c[0] for c in _CASES]
)
def test_measurementout_y_measurement_aceptan_o_rechazan_el_mismo_caso(
    raw_override, entity_override, valid
):
    raw = _base_raw(**raw_override)
    entity_kwargs = _base_entity_kwargs(**entity_override)

    if valid:
        out = parse_measurement(raw)
        assert out.evidence == raw["evidence"]
        measurement = Measurement(**entity_kwargs)
        assert measurement.evidence == entity_kwargs["evidence"]
    else:
        with pytest.raises(MalformedMeasurement):
            parse_measurement(raw)
        with pytest.raises(InvariantViolation):
            Measurement(**entity_kwargs)
