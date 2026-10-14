"""Reglas puras de T85.e: elegibilidad del redactor, σ extrema y dato publicable."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest
from helpers.solution_tension_writer import make_solution_tension

from nocturna.domain.entities import MeasuredParameter
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.solution_tension import (
    SkipReason,
    is_extreme_sigma,
    solution_tension_data_from,
    solution_writer_skip_reason,
    value_change,
)


def test_elegible_con_item_y_sin_skip_reason() -> None:
    assert solution_writer_skip_reason(make_solution_tension(uuid4())) is None


def test_no_elegible_sin_item_o_con_skip_reason() -> None:
    assert solution_writer_skip_reason(make_solution_tension(None)) == "not_eligible"
    skipped = replace(make_solution_tension(None), skip_reason=SkipReason.OLD_PAPER)
    assert solution_writer_skip_reason(skipped) == "not_eligible"


def test_sigma_extrema_es_estrictamente_mayor_que_el_umbral() -> None:
    assert is_extreme_sigma(289.5, 50) and not is_extreme_sigma(50, 50)


def test_value_change_del_caso_hd_114082_b() -> None:
    tension = make_solution_tension(uuid4())
    rel, ratio = value_change(tension.new, tension.reference)
    assert ratio == pytest.approx(2.0551, abs=1e-3) and rel == pytest.approx(1.0551, abs=1e-3)


def test_value_change_de_masa_y_rechaza_parametros_distintos() -> None:
    mass = make_solution_tension(uuid4(), parameter=MeasuredParameter.MASS, sigma=9.0)
    rel, ratio = value_change(mass.new, mass.reference)
    assert ratio > 0 and rel == pytest.approx(abs(ratio - 1))
    period = make_solution_tension(uuid4())
    with pytest.raises(InvariantViolation):
        value_change(mass.new, period.reference)


def test_data_from_copia_la_tension() -> None:
    tension = make_solution_tension(uuid4())
    data = solution_tension_data_from(tension, threshold_sigma=3.0, archive_url="https://x/y")
    assert data.sigma == tension.sigma and data.new == tension.new
    assert data.reference_role is tension.reference_role and data.archive_url == "https://x/y"


def test_data_from_rechaza_no_elegible_y_sigma_bajo_el_umbral_vigente() -> None:
    with pytest.raises(InvariantViolation):
        solution_tension_data_from(
            make_solution_tension(None), threshold_sigma=3.0, archive_url="u"
        )
    with pytest.raises(InvariantViolation):
        solution_tension_data_from(
            make_solution_tension(uuid4(), sigma=4.0), threshold_sigma=5.0, archive_url="u"
        )
