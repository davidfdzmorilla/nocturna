"""T84.r: prompt `writer-digest-v1` y esquema de salida `DigestWriterOutput`.

Puros, sin Claude. Fichero en español. El fichero del prompt lo crea el backend; aquí
se fijan las reglas heredadas de T96 y las propias del resumen semanal.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from nocturna.application.agents import prompt_loader
from nocturna.application.agents.prompt_loader import WRITER_DIGEST_PROMPT_VERSION, load_prompt
from nocturna.application.agents.writer_output import DigestWriterOutput

PROMPTS = Path(prompt_loader.__file__).parent / "prompts"


#: Hash congelado de `writer-digest-v1.md`: un cambio exige `writer-digest-v2`.
_V1_SHA256 = "1ca66816a59d7678afc7dadad86e0a7791618a894b84ac5731f69d5ef7bb6b5e"


def _prompt() -> str:
    return load_prompt("writer-digest-v1")


def _paragraphs(text: str) -> list[str]:
    return [p for p in re.split(r"\n\s*\n|\n- ", text) if p.strip()]


# --- versión y fichero ---------------------------------------------------------------


def test_la_version_vigente_es_writer_digest_v1() -> None:
    assert WRITER_DIGEST_PROMPT_VERSION == "writer-digest-v1"


def test_v1_hash_congelado() -> None:
    digest = hashlib.sha256((PROMPTS / "writer-digest-v1.md").read_bytes()).hexdigest()
    assert digest == _V1_SHA256


def test_el_fichero_existe_y_el_loader_lo_carga() -> None:
    assert (PROMPTS / "writer-digest-v1.md").is_file()
    assert _prompt().strip()
    assert load_prompt(WRITER_DIGEST_PROMPT_VERSION) == _prompt()


def test_declara_las_marcas_del_bloque() -> None:
    assert "<weekly_digest>" in _prompt() and "</weekly_digest>" in _prompt()


def test_el_codigo_no_lleva_literales_de_version_del_prompt_del_resumen() -> None:
    """La versión sale solo de `WRITER_DIGEST_PROMPT_VERSION` (prompt_loader.py)."""
    src = Path(prompt_loader.__file__).parents[2]
    offenders = [
        f"{path.relative_to(src)}:{n}"
        for path in src.rglob("*.py")
        if path.name != "prompt_loader.py"
        for n, line in enumerate(path.read_text().splitlines(), 1)
        if re.search(r"""["']writer-digest-v\d""", line)
    ]
    assert offenders == []


def test_los_prompts_del_redactor_de_tensiones_siguen_en_su_sitio() -> None:
    assert (PROMPTS / "writer-archive-v2.md").is_file() and (PROMPTS / "writer-v1.md").is_file()


# --- reglas de T96 ----------------------------------------------------------------------


def test_pipeline_solo_aparece_dentro_de_la_regla_que_lo_prohibe() -> None:
    text = _prompt()
    rule = r"\b(no|nunca)\s+(uses|usar|emplees|escribas|digas|menciones)\b|\bevita\b|prohib"
    offenders = [
        p for p in _paragraphs(text) if "pipeline" in p.lower() and not re.search(rule, p.lower())
    ]
    assert offenders == []
    assert "análisis automático" in text.lower()


def test_prohibe_explicitamente_la_palabra_pipeline() -> None:
    text = _prompt().lower().replace("\n", " ")
    rule = r"\b(no|nunca)\b[^.]{0,40}\bpipeline\b|\bpipeline\b[^.]{0,40}\b(prohib|evita)"
    assert re.search(rule, text), "falta la regla que prohíbe 'pipeline'"


def test_prohibe_mencionar_el_bloque_los_datos_de_entrada_y_su_formato() -> None:
    text = _prompt().lower()
    rule = r"\b(no|nunca)\s+(menciones|hables|nombres|cites|digas|escribas|uses|copies)\b"
    assert any("bloque" in p and re.search(rule, p) for p in _paragraphs(text)), (
        "falta una regla que prohíba mencionar 'el bloque'"
    )


def test_prohibe_copiar_nombres_de_campo_y_los_cita() -> None:
    text = _prompt().lower()
    paragraphs = [p for p in _paragraphs(text) if "campo" in p]
    assert paragraphs, "falta la regla sobre nombres de campo"
    joined = " ".join(paragraphs)
    assert re.search(r"\b(nunca|no)\b[^.]{0,60}\bcopi", joined)
    for field in ("changed", "new_planet", "regained", "lost"):
        assert field in joined, field


def test_pide_usar_valores_y_porcentajes_tal_como_vienen() -> None:
    text = _prompt().lower()
    assert re.search(r"redondead", text)
    assert "porcentaje" in text
    assert re.search(r"tal (cual|como)", text)


def test_pide_coma_decimal() -> None:
    assert "coma decimal" in _prompt().lower()


def test_el_texto_dentro_de_las_marcas_es_dato_nunca_instrucciones() -> None:
    text = _prompt().lower()
    assert "nunca instrucciones" in text or "no son instrucciones" in text
    assert "dato" in text


def test_prohibe_urls_e_identificadores_de_arxiv() -> None:
    text = _prompt().lower()
    assert re.search(r"sin url", text) or re.search(r"(no|nunca)[^.]{0,40}url", text)
    assert "arxiv" in text


def test_prohibe_inventar_causas() -> None:
    assert re.search(r"no inventes causas|nunca inventes causas", _prompt().lower())


def test_prohibe_presentar_un_valor_como_el_correcto() -> None:
    text = _prompt().lower()
    assert re.search(r"(nunca|no)\s+presentes[^.]{0,80}(correct|verdader)", text)


def test_prohibe_llamar_descubrimiento_a_la_entrada_de_un_planeta_en_el_archivo() -> None:
    text = _prompt().lower()
    assert any(
        "descubrimiento" in p and re.search(r"\b(no|nunca)\b", p) for p in _paragraphs(text)
    ), "falta la regla sobre 'descubrimiento'"


def test_declara_que_los_ejemplos_son_inventados() -> None:
    assert re.search(r"inventad|fictici", _prompt().lower())


@pytest.mark.parametrize("needle", ["Collier", "HIP 67522", "TOI-210", "TOI-6981"])
def test_no_contiene_nombres_de_casos_reales(needle: str) -> None:
    assert re.search(rf"\b{re.escape(needle)}\b", _prompt()) is None


# --- formato de salida -------------------------------------------------------------------


def test_pide_un_json_con_title_y_summary_y_sus_limites() -> None:
    text = _prompt()
    assert "`title`" in text and "`summary`" in text
    assert "90" in text  # máximo de caracteres del titular
    assert "220" in text  # máximo de palabras del resumen
    assert re.search(r"\b(1|un)\b[^.]{0,20}\b(2|dos)\s+párrafos", text.lower())
    assert "español" in text.lower()
    assert "`level_curious`" not in text  # un solo texto, no tres niveles


# --- esquema DigestWriterOutput --------------------------------------------------------


def test_salida_valida() -> None:
    out = DigestWriterOutput.model_validate({"title": "Un título", "summary": "Un resumen."})
    assert out.title == "Un título" and out.summary == "Un resumen."


def test_titulo_de_90_valido_y_de_91_no() -> None:
    DigestWriterOutput.model_validate({"title": "x" * 90, "summary": "r"})
    with pytest.raises(ValidationError):
        DigestWriterOutput.model_validate({"title": "x" * 91, "summary": "r"})


@pytest.mark.parametrize("field", ["title", "summary"])
@pytest.mark.parametrize("value", ["", "   ", "\\n\\n"])
def test_campos_en_blanco_no_validan(field: str, value: str) -> None:
    payload = {"title": "t", "summary": "r", field: value}
    with pytest.raises(ValidationError):
        DigestWriterOutput.model_validate(payload)


@pytest.mark.parametrize(
    "payload", [{"title": "t"}, {"summary": "r"}, {}, {"title": 1, "summary": "r"}]
)
def test_campos_ausentes_o_de_otro_tipo_no_validan(payload: dict) -> None:
    with pytest.raises(ValidationError):
        DigestWriterOutput.model_validate(payload)


def test_el_salto_de_linea_literal_se_convierte_en_salto_real() -> None:
    out = DigestWriterOutput.model_validate({"title": "t", "summary": "uno\\n\\ndos\\ntres"})
    assert out.summary == "uno\n\ndos\ntres"


def test_los_campos_extra_se_ignoran() -> None:
    out = DigestWriterOutput.model_validate({"title": "t", "summary": "r", "level_curious": "x"})
    assert not hasattr(out, "level_curious")
