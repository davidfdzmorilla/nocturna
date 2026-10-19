"""T96: `writer-archive-v2` (prompt) y el bloque `<solution_tension>` que lo alimenta.

Puros / en memoria, sin Claude. Fichero en español.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import replace
from pathlib import Path

import pytest
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    make_policy,
    make_solution_selector,
)
from helpers.solution_tension_writer import (
    catalog_solution,
    make_archive_item,
    make_solution_tension,
)

from nocturna.application.agents import prompt_loader
from nocturna.application.agents.prompt_loader import (
    WRITER_ARCHIVE_PROMPT_VERSION,
    load_prompt,
)
from nocturna.application.use_cases.write_solution_tensions import WriteSolutionTensions

PROMPTS = Path(prompt_loader.__file__).parent / "prompts"

#: Hash congelado de `writer-archive-v1.md` (T85.e). Los hallazgos ya guardados
#: citan esa etiqueta: si falla, alguien editó v1 en vez de crear v2.
_V1_SHA256 = "e1078aedab9bac88b8abef57d9d5e02696f8dfdddb3d70bea055f562b5f6aa38"

#: Hash congelado de `writer-archive-v2.md` (T96): un cambio exige `writer-archive-v3`.
_V2_SHA256 = "a4bc625cb15719e7d62c45dcf2d708c60af631cff70899a3a6f6debabd3936d0"


# --- versión y fichero ------------------------------------------------------


def test_la_version_vigente_del_redactor_del_archivo_es_v2() -> None:
    assert WRITER_ARCHIVE_PROMPT_VERSION == "writer-archive-v2"


def test_v2_existe_y_declara_las_marcas_del_bloque() -> None:
    assert "<solution_tension>" in load_prompt("writer-archive-v2")


def test_v1_sigue_existiendo_y_su_hash_esta_congelado() -> None:
    digest = hashlib.sha256((PROMPTS / "writer-archive-v1.md").read_bytes()).hexdigest()
    assert digest == _V1_SHA256


def test_v2_hash_congelado() -> None:
    digest = hashlib.sha256((PROMPTS / "writer-archive-v2.md").read_bytes()).hexdigest()
    assert digest == _V2_SHA256


# --- v2 no contiene datos de los casos reales -------------------------------

#: Cifras y nombres de los casos reales que motivaron v2 (y del ejemplo de v1).
#: Un ejemplo con datos reales sesgaría al modelo hacia ellos.
_NUMERIC = ("982", "5689", "8,2", "225,55", "109,75", "2,055", "1,0551", "1197", "450,2", "10,07")
_NAMES = (
    "HD 114082",
    "HD 202206",
    "HD 65216",
    "TOI-2158",
    "TOI-4504",
    "K2-312",
    "K2-223",
    "Zakhozhay",
    "del Burgo",
    "Cao",
    "Benedict",
    "Almenara",
    "Vítková",
    "Knudstrup",
    "Wallace",
    "Wittenmyer",
)


@pytest.mark.parametrize("needle", _NUMERIC)
def test_v2_no_contiene_cifras_de_casos_reales(needle: str) -> None:
    pattern = rf"(?<![\d,.]){re.escape(needle)}(?!\d)"
    assert re.search(pattern, load_prompt("writer-archive-v2")) is None, needle


@pytest.mark.parametrize("needle", _NAMES)
def test_v2_no_contiene_nombres_de_casos_reales(needle: str) -> None:
    assert re.search(rf"\b{re.escape(needle)}\b", load_prompt("writer-archive-v2")) is None, needle


# --- reglas presentes en v2 ---------------------------------------------------


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n|\n- ", text) if p.strip()]


_PROHIBITION = r"(no|nunca|evita|prohib)\w*\s+\w*"


def test_v2_pide_la_diferencia_relativa_en_porcentaje() -> None:
    text = load_prompt("writer-archive-v2").lower()
    assert "porcentaje" in text
    assert any("porcentaje" in p and "diferencia relativa" in p for p in _paragraphs(text)), (
        "la regla debe unir 'diferencia relativa' y 'porcentaje'"
    )


def test_v2_prohibe_hablar_de_etiquetas_cuando_no_hay() -> None:
    text = load_prompt("writer-archive-v2").lower()
    assert re.search(r"(si no hay|sin|cuando no hay|no hay)\s+(ninguna\s+)?etiquetas?", text)


def test_v2_prohibe_mencionar_el_bloque_y_el_formato_de_entrada() -> None:
    text = load_prompt("writer-archive-v2").lower()
    rule = r"\b(no|nunca)\s+(menciones|hables|nombres|cites|digas|escribas|uses|copies)\b"
    assert any("bloque" in p and re.search(rule, p) for p in _paragraphs(text)), (
        "falta una regla que prohíba mencionar 'el bloque'"
    )


def test_v2_pipeline_solo_aparece_dentro_de_la_regla_que_lo_prohibe() -> None:
    text = load_prompt("writer-archive-v2")
    rule = r"\b(no|nunca)\s+(uses|usar|emplees|escribas|digas|menciones)\b|\bevita\b|prohib"
    offenders = [
        p for p in _paragraphs(text) if "pipeline" in p.lower() and not re.search(rule, p.lower())
    ]
    assert offenders == []
    assert "análisis automático" in text.lower()


def test_v2_pide_usar_los_valores_tal_como_vienen_redondeados() -> None:
    text = load_prompt("writer-archive-v2").lower()
    assert re.search(r"redondead", text)


def test_v2_conserva_coma_decimal() -> None:
    assert "coma decimal" in load_prompt("writer-archive-v2").lower()


# --- bloque ------------------------------------------------------------------


def _block(*, tension_kwargs=None, new=None, reference=None, extreme_sigma: float = 50.0) -> str:
    item = make_archive_item()
    env = Environment(
        items=[item],
        policy=make_policy(writer_reserve_tokens=20_000, max_writer_calls_per_night=5),
        now=WITHIN_WINDOW,
    )
    tension = make_solution_tension(item.id, **(tension_kwargs or {}))
    if new is not None:
        tension = replace(tension, new=new)
    if reference is not None:
        tension = replace(tension, reference=reference)
    env.solution_tensions.add(tension)
    (candidate,) = make_solution_selector(env, extreme_sigma=extreme_sigma).pending().candidates
    return WriteSolutionTensions._build_prompt(candidate)


def _line(block: str, prefix: str) -> str:
    (line,) = [ln for ln in block.splitlines() if ln.startswith(prefix)]
    return line


def test_hd114082b_da_el_porcentaje_y_no_la_fraccion_y_conserva_la_razon() -> None:
    line = _line(_block(), "value_change:")

    assert "105.5" in line
    assert "1.0551" not in line
    assert "ratio=2.0551" in line


def test_periodo_con_diferencia_diminuta_no_da_cero_ni_exponente() -> None:
    new = catalog_solution("Nuevo et al. 2026", 3.2510, 0.0001, is_default=True)
    ref = catalog_solution("Previo et al. 2020", 3.2500, 0.0001)
    line = _line(_block(new=new, reference=ref), "value_change:")

    assert "0.03077" in line
    assert "e-" not in line.lower() and "e+" not in line.lower()
    assert "=0.0 " not in line and "=0.0000" not in line
    assert "ratio=1.0003" in line


def _sol(value: float, plus: float | None, minus: float | None, *, default: bool = False):
    return replace(
        catalog_solution("Ref et al. 2026", value, 0.0, is_default=default),
        err_plus=plus,
        err_minus=minus,
    )


@pytest.mark.parametrize(
    ("value", "plus", "minus", "expected"),
    [
        (1197.32317485, 57.52694167, 57.90833576, "1197 (+58/-58)"),
        (10.07687365, 0.16813471, 0.16813471, "10.08 (+0.17/-0.17)"),
        (225.5504, 0.0004, 0.0004, "225.55040 (+0.00040/-0.00040)"),
        (4.562546, 0.00014, 0.00014, "4.56255 (+0.00014/-0.00014)"),
        (109.75, 0.4, 0.4, "109.75 (+0.40/-0.40)"),
    ],
)
def test_redondeo_segun_la_incertidumbre_en_la_solucion_nueva(
    value: float, plus: float, minus: float, expected: str
) -> None:
    block = _block(new=_sol(value, plus, minus, default=True))

    assert expected in _line(block, "new_solution:")


def test_el_redondeo_usa_el_error_menor_y_vale_para_la_referencia_y_otras() -> None:
    from nocturna.domain.entities import SolutionComparison

    other = SolutionComparison(_sol(1197.32317485, 57.52694167, 57.90833576), 3.0)
    block = _block(
        tension_kwargs={"others": (other,)},
        reference=_sol(10.07687365, 0.16813471, 0.2),
    )

    assert "10.08 (+0.17/-0.20)" in _line(block, "reference_solution:")
    assert any("1197 (+58/-58)" in ln for ln in block.splitlines() if ln.startswith("- Ref"))
    assert "57.52694167" not in block and "10.07687365" not in block


@pytest.mark.parametrize(("plus", "minus"), [(None, None), (0.0, 0.0)])
def test_sin_error_o_error_cero_el_valor_se_deja_como_viene(plus, minus) -> None:
    block = _block(new=_sol(12.3456789, plus, minus, default=True))

    assert "12.3456789" in _line(block, "new_solution:")


# --- T96, endurecimiento tras mutantes ----------------------------------------


def test_v2_ordena_contar_la_diferencia_relativa_como_porcentaje() -> None:
    text = load_prompt("writer-archive-v2").lower()
    assert any(
        "relative_difference_pct" in p and "como porcentaje" in p for p in _paragraphs(text)
    ), "la regla debe ordenar contar la diferencia relativa 'como porcentaje'"


def test_v2_prohibe_explicitamente_la_palabra_pipeline() -> None:
    text = load_prompt("writer-archive-v2").lower()
    rule = r"\b(no|nunca)\b[^.]{0,40}\bpipeline\b|\bpipeline\b[^.]{0,40}\b(prohib|evita)"
    assert re.search(rule, text.replace("\n", " ")), "falta la regla que prohíbe 'pipeline'"


def test_el_codigo_no_lleva_literales_de_version_del_prompt_del_archivo() -> None:
    """La versión sale solo de `WRITER_ARCHIVE_PROMPT_VERSION`: un literal en el
    CLI o en el caso de uso podría divergir de `prompt_version`."""
    src = Path(prompt_loader.__file__).parents[2]
    offenders = [
        f"{path.relative_to(src)}:{n}"
        for path in src.rglob("*.py")
        if path.name != "prompt_loader.py"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"""["']writer-archive-v\d""", line)
    ]
    assert offenders == []


def test_percent_4sf_nunca_usa_exponente_ni_redondea_a_cero() -> None:
    from nocturna.application.agents.prompt_text import percent_4sf

    assert percent_4sf(123.45678) == "12346"
    assert percent_4sf(0.0000001234) == "0.00001234"
    assert percent_4sf(-1.0551289) == "105.5"
    assert percent_4sf(0.0) == "0"
    assert all("e" not in percent_4sf(f) for f in (1e-9, 3.3e-6, 1e6, 12345.6789))


@pytest.mark.parametrize(
    ("value", "plus", "minus", "expected"),
    [
        (1.23456, 0.5, 0.05, ("1.235", "0.500", "0.050")),
        (1.23456, 0.05, 0.5, ("1.235", "0.050", "0.500")),
        (1234.5, 150.0, 120.0, ("1234", "150", "120")),
        (1.5, 0.0042, 0.0042, ("1.5000", "0.0042", "0.0042")),
    ],
)
def test_round_to_error_usa_dos_cifras_del_error_menor(value, plus, minus, expected) -> None:
    from nocturna.application.agents.prompt_text import round_to_error

    assert round_to_error(value, plus, minus) == expected


@pytest.mark.parametrize(
    ("plus", "minus"), [(None, 0.1), (0.1, None), (None, None), (0.0, 0.1), (0.1, 0.0), (0.0, 0.0)]
)
def test_round_to_error_sin_base_de_redondeo_devuelve_none(plus, minus) -> None:
    from nocturna.application.agents.prompt_text import round_to_error

    assert round_to_error(12.3456789, plus, minus) is None


def test_el_redondeo_del_bloque_sigue_al_error_menor_aunque_difieran_en_orden() -> None:
    block = _block(new=_sol(1.23456, 0.5, 0.05, default=True))

    assert "1.235 (+0.500/-0.050)" in _line(block, "new_solution:")


def test_el_porcentaje_del_bloque_no_usa_exponente_con_cambio_enorme() -> None:
    new = catalog_solution("Nuevo et al. 2026", 5000.0, 0.1, is_default=True)
    ref = catalog_solution("Previo et al. 2020", 2.0, 0.1)
    line = _line(_block(new=new, reference=ref), "value_change:")

    assert "relative_difference_pct=249900" in line
    assert "e+" not in line.lower()
