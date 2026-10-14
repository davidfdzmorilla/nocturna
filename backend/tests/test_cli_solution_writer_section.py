"""T85.e: subsección del `--dry-run` con las tensiones del archivo del redactor."""

from __future__ import annotations

from helpers.run_night import WITHIN_WINDOW, Environment, make_policy, make_solution_selector
from helpers.solution_tension_writer import make_archive_item, make_solution_tension

from nocturna import cli
from nocturna.domain.solution_tension import SkipReason


def _pending():
    item = make_archive_item()
    env = Environment(items=[item], policy=make_policy(), now=WITHIN_WINDOW)
    env.solution_tensions.add(make_solution_tension(item.id))
    env.solution_tensions.add(
        make_solution_tension(None, new_key="c" * 64, skip_reason=SkipReason.BELOW_THRESHOLD)
    )
    return make_solution_selector(env).pending()


def test_la_seccion_se_lista_aunque_el_redactor_este_apagado_y_lo_dice() -> None:
    text = cli._format_solution_writer_section(
        _pending(), enabled=False, labels={}, estimated_tokens=12000
    )
    assert "Tensiones del archivo pendientes del redactor" in text
    assert "redactor del archivo desactivado (writer_enabled = false)" in text
    assert "elegibles=1 excluidas=1" in text
    assert "elegible · HD 114082 b (period) · σ=289.50 · extreme_sigma" in text
    assert "excluidas · not_eligible: 1" in text


def test_la_seccion_con_el_redactor_encendido_no_lleva_la_marca() -> None:
    text = cli._format_solution_writer_section(
        _pending(), enabled=True, labels={}, estimated_tokens=12000
    )
    assert "desactivado" not in text
