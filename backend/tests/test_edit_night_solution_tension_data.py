"""Línea `data` del Editor para `solution_tension` (T85.e, D4)."""

from __future__ import annotations

from uuid import uuid4

from helpers.solution_tension_writer import make_solution_tension

from nocturna.application.use_cases.edit_night import _data_line
from nocturna.domain.entities import (
    Finding,
    FindingType,
    MeasuredParameter,
    SolutionComparison,
    SolutionTensionLabel,
)
from nocturna.domain.solution_tension import solution_tension_data_from


def _finding(**kw) -> Finding:
    tension = make_solution_tension(uuid4(), **kw)
    data = solution_tension_data_from(tension, threshold_sigma=3.0, archive_url="https://x/y")
    return Finding(
        item_id=uuid4(),
        run_id=uuid4(),
        type=FindingType.SOLUTION_TENSION,
        title="t",
        level_curious="c",
        level_amateur="a",
        level_technical="t",
        solution_tension=data,
        solution_tension_id=tension.id,
    )


def test_data_line_periodo_con_sigma_extrema() -> None:
    line = _data_line(_finding(), extreme_sigma=50)
    assert line is not None and "\n" not in line
    assert line.startswith("planet=HD 114082 b; parameter=period; new=del Burgo et al. 2026")
    assert "[default]; reference=Zakhozhay et al. 2022 109.75 (+0.4/-0.4) day [most_recent]" in line
    assert "sigma=289.50; threshold=3.0; others=0; labels=extreme_sigma" in line
    assert line.endswith("rel_diff=1.0551; ratio=2.0551")


def test_data_line_sin_umbral_extremo_no_marca_y_sin_etiquetas_dice_none() -> None:
    line = _data_line(_finding())
    assert line is not None and "labels=none" in line


def test_data_line_masa_con_otras_y_etiquetas_persistidas() -> None:
    base = make_solution_tension(uuid4(), parameter=MeasuredParameter.MASS, sigma=40.0)
    others = (SolutionComparison(base.reference, 2.0), SolutionComparison(base.reference, 7.5))
    line = _data_line(
        _finding(
            parameter=MeasuredParameter.MASS,
            sigma=40.0,
            others=others,
            labels=(SolutionTensionLabel.MODEL_DEPENDENT,),
        ),
        extreme_sigma=50,
    )
    assert line is not None
    assert "others=2 (sigma 2.00-7.50); labels=model_dependent" in line
    assert "; rel_diff=" in line and "; ratio=" in line
