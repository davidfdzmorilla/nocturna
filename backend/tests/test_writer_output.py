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
