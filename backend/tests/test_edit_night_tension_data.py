"""T76: línea `data` de un `catalog_tension` y versiones de prompt del Editor/redactor."""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

from helpers.tension_writer import v1298_evaluation

from nocturna.application.agents.prompt_loader import (
    EDITOR_PROMPT_VERSION,
    WRITER_PROMPT_VERSION,
    load_prompt,
)
from nocturna.application.use_cases.edit_night import _data_line
from nocturna.domain.entities import Finding, FindingType
from nocturna.domain.tension import catalog_tension_from


def _finding(evidence: str | None = None) -> Finding:
    ev = v1298_evaluation(uuid4(), evidence=evidence)
    assert ev.result is not None
    tension = catalog_tension_from(ev.result, threshold_sigma=3.0, archive_url="https://a.test/x")
    return Finding(
        item_id=ev.item_id,
        run_id=uuid4(),
        type=FindingType.CATALOG_TENSION,
        title="t",
        level_curious="c",
        level_amateur="a",
        level_technical="x",
        catalog_tension=tension,
        tension_evaluation_id=ev.id,
    )


def test_data_line_de_catalog_tension_trae_planeta_sigma_y_referencia_en_una_linea() -> None:
    line = _data_line(_finding())

    assert line is not None
    assert "\n" not in line
    assert "planet=V1298 Tau b" in line
    assert "parameter=mass" in line
    assert "reference=Livingston et al. 2026" in line
    assert "[default]" in line
    assert "sigma=3.37" in line
    assert "threshold=3.0" in line
    assert "other_priors=1 (sigma 0.04-0.53)" in line


def test_data_line_nunca_lleva_evidence() -> None:
    line = _data_line(_finding(evidence="FRASE-LITERAL-DEL-ABSTRACT <x>"))

    assert line is not None
    assert "FRASE-LITERAL" not in line
    assert "evidence" not in line
    assert "<" not in line and ">" not in line


def test_data_line_sigue_acotada_con_muchas_previas_y_texto_largo() -> None:
    base = _finding()
    tension = base.catalog_tension
    assert tension is not None
    baseline = _data_line(base)
    assert baseline is not None
    extra = tuple(
        replace(
            c, prior=replace(c.prior, reference=f"Otra previa {i} " + "x" * 200, is_default=False)
        )
        for i in range(40)
        for c in tension.comparisons
        if not c.prior.is_default
    )
    many = replace(tension, comparisons=tension.comparisons + extra)
    crowded = replace(base, catalog_tension=many)

    line = _data_line(crowded)

    assert line is not None
    assert "\n" not in line
    assert "Otra previa" not in line, "las previas no referencia se resumen, no se listan"
    assert len(line) <= len(baseline) + 40
    assert "other_priors=41" in line


def test_versiones_y_prompts_cargan() -> None:
    assert EDITOR_PROMPT_VERSION == "editor-v3"
    assert WRITER_PROMPT_VERSION == "writer-v1"
    editor = load_prompt(EDITOR_PROMPT_VERSION)
    assert "catalog_tension" in editor and "no verificada" in editor
    assert "<tension>" in load_prompt(WRITER_PROMPT_VERSION)
    assert "catalog_tension" not in load_prompt("editor-v2")
