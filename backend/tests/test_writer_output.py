"""T76: `parse_writer_output` (esquema de la salida del redactor)."""

from __future__ import annotations

import json

import pytest

from nocturna.application.agents.parsing import InvalidAgentOutput
from nocturna.application.agents.writer_output import parse_writer_output

VALID = {
    "title": "Titular",
    "level_curious": "Curioso",
    "level_amateur": "Aficionado",
    "level_technical": "Técnico",
}


def test_salida_valida() -> None:
    out = parse_writer_output(json.dumps(VALID))

    assert out.title == "Titular" and out.level_technical == "Técnico"


def test_campo_extra_se_ignora() -> None:
    out = parse_writer_output(json.dumps({**VALID, "extra": "x"}))

    assert out.title == "Titular"


@pytest.mark.parametrize("field", list(VALID))
def test_campo_en_blanco_es_invalido(field: str) -> None:
    with pytest.raises(InvalidAgentOutput):
        parse_writer_output(json.dumps({**VALID, field: "  "}))


def test_campo_ausente_es_invalido() -> None:
    payload = dict(VALID)
    del payload["title"]
    with pytest.raises(InvalidAgentOutput):
        parse_writer_output(json.dumps(payload))


def test_tipo_incorrecto_es_invalido() -> None:
    with pytest.raises(InvalidAgentOutput):
        parse_writer_output(json.dumps({**VALID, "title": 3}))


def test_texto_que_no_es_json_es_invalido() -> None:
    with pytest.raises(InvalidAgentOutput):
        parse_writer_output("no es json")


FIELDS = list(VALID)


@pytest.mark.parametrize("field", FIELDS)
def test_t96_barra_n_literal_se_convierte_en_salto_real(field: str) -> None:
    # JSON "\\n" = barra invertida + n en el texto (doble escape del modelo).
    raw = json.dumps({**VALID, field: "Uno.\\nNo hay otras."})
    assert "\\\\n" in raw

    out = parse_writer_output(raw)

    assert getattr(out, field) == "Uno.\nNo hay otras."


def test_t96_dos_literales_dan_dos_saltos() -> None:
    out = parse_writer_output(json.dumps({**VALID, "level_amateur": "A.\\n\\nB."}))

    assert out.level_amateur == "A.\n\nB."


def test_t96_texto_sin_la_secuencia_queda_identico() -> None:
    out = parse_writer_output(json.dumps({**VALID, "level_amateur": "Sin saltos, 3 sigma."}))

    assert out.level_amateur == "Sin saltos, 3 sigma."


def test_t96_salto_real_bien_escapado_queda_igual() -> None:
    raw = '{"title": "T", "level_curious": "C", "level_amateur": "A.\\nB.", "level_technical": "X"}'

    out = parse_writer_output(raw)

    assert out.level_amateur == "A.\nB."


def test_t96_campo_solo_de_literales_sigue_siendo_blanco() -> None:
    with pytest.raises(InvalidAgentOutput):
        parse_writer_output(json.dumps({**VALID, "title": "\\n\\n"}))
