"""Tests de `application/agents/parsing.py` y `application/agents/reader_output.py`.

Puros, sin IO ni base de datos: no hay LLM que llamar aquí, la entrada es
texto fijo que simula la salida cruda de un agente. Ningún test llama a
Claude.
"""

import json

import pytest

from nocturna.application.agents.parsing import InvalidAgentOutput, extract_json_object
from nocturna.application.agents.reader_output import ReaderOutput, parse_reader_output

# --- extract_json_object: variantes de envoltura ---------------------------


def test_fence_json_con_lenguaje_minuscula():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_fence_json_con_lenguaje_mayuscula():
    assert extract_json_object('```JSON\n{"a": 1}\n```') == {"a": 1}


def test_fence_desnudo_sin_lenguaje():
    assert extract_json_object('```\n{"a": 1}\n```') == {"a": 1}


def test_fence_sin_cierre_se_recupera_igual():
    """Un fence de apertura sin el de cierre (el modelo truncó la respuesta
    justo en el borde) no debe impedir extraer el JSON que sí llegó completo.
    """
    assert extract_json_object('```json\n{"a": 1}') == {"a": 1}


def test_json_crudo_sin_fence():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_json_con_prosa_antes_y_despues():
    text = 'Aquí tienes el resultado:\n{"a": 1}\nEspero que ayude.'

    assert extract_json_object(text) == {"a": 1}


def test_json_anidado_con_llaves_internas_no_se_rompe_al_recortar():
    """El caso que más fácil se rompe al tocar `parsing.py`: el recorte
    "primer `{` ... último `}`" tiene que conservar objetos anidados legítimos,
    incluida una cadena que contiene literalmente el carácter `}`.
    """
    text = (
        'Resultado:\n{"summary": "resumen", "objects": ["a"], '
        '"claims": ["x } y"], "nested": {"inner": {"deep": 1}}, '
        '"interest_score": 3}\nFin.'
    )

    result = extract_json_object(text)

    assert result["nested"] == {"inner": {"deep": 1}}
    assert result["claims"] == ["x } y"]


# --- extract_json_object: fallos --------------------------------------


def test_json_truncado_lanza_invalid_agent_output():
    with pytest.raises(InvalidAgentOutput):
        extract_json_object('{"a": 1, "b": ')


@pytest.mark.parametrize(
    "text",
    ["[1, 2, 3]", "42", '"una cadena"', "null"],
    ids=["array", "numero", "cadena", "null"],
)
def test_json_valido_pero_no_objeto_como_raiz_lanza_invalid_agent_output(text):
    with pytest.raises(InvalidAgentOutput):
        extract_json_object(text)


def test_texto_sin_nada_de_json_lanza_invalid_agent_output():
    with pytest.raises(InvalidAgentOutput):
        extract_json_object("esto no tiene nada de json en absoluto")


def test_mensaje_de_error_esta_acotado_y_no_arrastra_todo_el_texto():
    """Un abstract entero (miles de caracteres) no debe colarse íntegro en un
    mensaje de excepción que puede acabar en logs.
    """
    long_text = "prosa sin json " * 100
    assert len(long_text) > 200

    with pytest.raises(InvalidAgentOutput) as exc_info:
        extract_json_object(long_text)

    assert len(str(exc_info.value)) < len(long_text)


# --- ReaderOutput: forma válida --------------------------------------------


def _valid_payload(**overrides) -> dict:
    payload = {
        "summary": "resumen",
        "objects": ["Betelgeuse"],
        "claims": ["una afirmación"],
        "interest_score": 3,
    }
    payload.update(overrides)
    return payload


def test_reader_output_valido_se_parsea():
    text = json.dumps(_valid_payload())

    output = parse_reader_output(text)

    assert isinstance(output, ReaderOutput)
    assert output.summary == "resumen"
    assert output.objects == ["Betelgeuse"]
    assert output.claims == ["una afirmación"]
    assert output.interest_score == 3


# --- ReaderOutput: fallos de forma ------------------------------------------


def test_reader_output_con_campo_ausente_falla():
    payload = _valid_payload()
    del payload["summary"]

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


@pytest.mark.parametrize("score", [0, 6], ids=["por_debajo", "por_encima"])
def test_reader_output_interest_score_fuera_de_rango_falla(score):
    payload = _valid_payload(interest_score=score)

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_interest_score_como_cadena_se_rechaza_por_strict():
    """`strict=True`: `"4"` no se coacciona a `4`. Un modelo que hoy devuelve
    tipos laxos mañana devuelve basura, y conviene verlo en el primer intento.
    """
    payload = _valid_payload(interest_score="4")

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_interest_score_como_float_se_rechaza_por_strict():
    payload = _valid_payload(interest_score=4.0)

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_interest_score_como_bool_se_rechaza_por_strict():
    """`bool` es subclase de `int` en Python; `strict=True` lo rechaza igual
    que cualquier otro tipo que no sea un entero de verdad.
    """
    payload = _valid_payload(interest_score=True)

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_objects_no_lista_de_cadenas_falla():
    payload = _valid_payload(objects=["Betelgeuse", 1])

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


# --- ReaderOutput: bloqueante de la revisión de T41 -------------------------
#
# `ReaderOutput` debe ser exactamente tan estricto como `Reading.__post_init__`
# (`_require_non_empty`, `domain/entities.py`): antes de la corrección aceptaba
# cadenas vacías o en blanco que luego reventaban `Reading(...)` con
# `InvariantViolation` FUERA del camino de reintento (ver el docstring de
# `reader_output.py`). Los tests de abajo son el espejo exacto de esa
# invariante: `summary` y cada elemento de `objects`/`claims` en blanco o
# vacío se rechazan; las listas vacías en sí, no -- la entidad tampoco las
# prohíbe.


def test_reader_output_summary_en_blanco_falla():
    payload = _valid_payload(summary="   ")

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_summary_vacio_falla():
    payload = _valid_payload(summary="")

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_elemento_en_blanco_dentro_de_objects_falla():
    payload = _valid_payload(objects=["Betelgeuse", "   "])

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_elemento_vacio_dentro_de_objects_falla():
    payload = _valid_payload(objects=["Betelgeuse", ""])

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_elemento_en_blanco_dentro_de_claims_falla():
    payload = _valid_payload(claims=["una afirmación válida", "   "])

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_elemento_vacio_dentro_de_claims_falla():
    payload = _valid_payload(claims=["una afirmación válida", ""])

    with pytest.raises(InvalidAgentOutput):
        parse_reader_output(json.dumps(payload))


def test_reader_output_objects_vacio_se_acepta():
    """Una lista vacía sigue siendo válida: la entidad tampoco la prohíbe,
    solo sus elementos si los hay."""
    payload = _valid_payload(objects=[])

    output = parse_reader_output(json.dumps(payload))

    assert output.objects == []


def test_reader_output_claims_vacio_se_acepta():
    payload = _valid_payload(claims=[])

    output = parse_reader_output(json.dumps(payload))

    assert output.claims == []


# --- ReaderOutput: claves extra se aceptan e ignoran, a propósito ----------


def test_reader_output_con_claves_extra_se_acepta_y_las_ignora():
    """`extra="ignore"`: un campo de más no justifica gastar un reintento que
    cuesta una llamada real.
    """
    payload = _valid_payload()
    payload["confidence"] = 0.9
    payload["unexpected_field"] = "sorpresa"

    output = parse_reader_output(json.dumps(payload))

    assert not hasattr(output, "confidence")
    assert not hasattr(output, "unexpected_field")
    assert output.summary == "resumen"
