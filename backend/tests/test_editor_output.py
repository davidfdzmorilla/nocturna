"""Tests de `application/agents/editor_output.py`.

Puros, sin IO ni base de datos: la entrada es texto fijo que simula la
salida cruda del Editor. Ningún test llama a Claude. Mismo patrón que
`test_popularizer_output.py` (T42), adaptado a la forma del Editor
(`publish`: lista de `{item_id, confidence, reason}`).
"""

import json
from uuid import uuid4

import pytest

from nocturna.application.agents.editor_output import (
    EditorDecision,
    EditorOutput,
    parse_editor_output,
)
from nocturna.application.agents.parsing import InvalidAgentOutput

# --- EditorOutput: forma válida ---------------------------------------------


def _decision(**overrides) -> dict:
    decision = {
        "item_id": str(uuid4()),
        "confidence": 0.8,
        "reason": "Hallazgo con potencial de interés general.",
    }
    decision.update(overrides)
    return decision


def _payload(*decisions: dict) -> dict:
    return {"publish": list(decisions)}


def test_editor_output_valido_se_parsea():
    item_id = uuid4()
    text = json.dumps(_payload(_decision(item_id=str(item_id))))

    output = parse_editor_output(text)

    assert isinstance(output, EditorOutput)
    assert len(output.publish) == 1
    decision = output.publish[0]
    assert isinstance(decision, EditorDecision)
    assert decision.item_id == item_id
    assert decision.confidence == 0.8
    assert decision.reason == "Hallazgo con potencial de interés general."


# --- EditorOutput: lista vacía es una respuesta válida y legítima ---------


def test_editor_output_publish_vacia_es_valida():
    text = json.dumps(_payload())

    output = parse_editor_output(text)

    assert output.publish == []


# --- EditorOutput: campos obligatorios -------------------------------------


@pytest.mark.parametrize("field_name", ["item_id", "confidence", "reason"])
def test_editor_output_decision_con_campo_ausente_falla(field_name):
    decision = _decision()
    del decision[field_name]
    text = json.dumps(_payload(decision))

    with pytest.raises(InvalidAgentOutput):
        parse_editor_output(text)


def test_editor_output_sin_publish_falla():
    text = json.dumps({})

    with pytest.raises(InvalidAgentOutput):
        parse_editor_output(text)


# --- EditorOutput: claves extra se aceptan e ignoran, a propósito ---------


def test_editor_output_con_claves_extra_se_acepta_y_las_ignora():
    """`extra="ignore"`: un campo de más no justifica gastar un reintento que
    cuesta una llamada real.
    """
    payload = _payload(_decision())
    payload["unexpected_field"] = "sorpresa"
    payload["publish"][0]["unexpected_nested"] = "sorpresa"

    output = parse_editor_output(json.dumps(payload))

    assert not hasattr(output, "unexpected_field")
    assert not hasattr(output.publish[0], "unexpected_nested")


# --- EditorOutput: confidence fuera de rango, espejo de Finding ------------
#
# `Finding._validate_confidence` rechaza cualquier valor fuera de
# [0.0, 1.0]; `EditorDecision.confidence` replica ese rango exacto.


@pytest.mark.parametrize("confidence", [-0.01, 1.01, -1.0, 2.0])
def test_editor_output_confidence_fuera_de_rango_falla(confidence):
    text = json.dumps(_payload(_decision(confidence=confidence)))

    with pytest.raises(InvalidAgentOutput):
        parse_editor_output(text)


@pytest.mark.parametrize("confidence", [0.0, 1.0, 0.5])
def test_editor_output_confidence_en_los_limites_es_valida(confidence):
    text = json.dumps(_payload(_decision(confidence=confidence)))

    output = parse_editor_output(text)

    assert output.publish[0].confidence == confidence


# --- EditorOutput: reason vacío o en blanco, regla defensiva de T41 -------


@pytest.mark.parametrize("blank_value", ["", "   "], ids=["vacio", "en_blanco"])
def test_editor_output_reason_vacio_o_en_blanco_falla(blank_value):
    text = json.dumps(_payload(_decision(reason=blank_value)))

    with pytest.raises(InvalidAgentOutput):
        parse_editor_output(text)


# --- EditorOutput: item_id mal formado -------------------------------------


def test_editor_output_item_id_mal_formado_falla():
    text = json.dumps(_payload(_decision(item_id="no-es-un-uuid")))

    with pytest.raises(InvalidAgentOutput):
        parse_editor_output(text)


# --- EditorOutput: tipos incorrectos rechazados (strict=True) -------------


def test_editor_output_reason_como_entero_se_rechaza():
    text = json.dumps(_payload(_decision(reason=123)))

    with pytest.raises(InvalidAgentOutput):
        parse_editor_output(text)


def test_editor_output_confidence_como_cadena_se_rechaza():
    # strict=True: "0.8" no se coacciona a 0.8, a diferencia del modo laxo.
    text = json.dumps(_payload(_decision(confidence="0.8")))

    with pytest.raises(InvalidAgentOutput):
        parse_editor_output(text)


# --- extract_json_object: envoltura, reutilizando lo que ya resuelve ------


def test_editor_output_en_fence_json_se_parsea():
    text = "```json\n" + json.dumps(_payload(_decision())) + "\n```"

    output = parse_editor_output(text)

    assert len(output.publish) == 1


def test_editor_output_con_prosa_alrededor_se_parsea():
    text = f"Aquí tienes el resultado:\n{json.dumps(_payload(_decision()))}\nEspero que ayude."

    output = parse_editor_output(text)

    assert len(output.publish) == 1


def test_editor_output_con_caracteres_de_control_crudos_se_repara():
    # Mismo caso de T42 (ver docstring de `parsing.py`): un salto de línea
    # literal dentro de una cadena JSON, aquí en `reason`, se repara antes
    # de fallar el parseo.
    raw = (
        '{"publish": [{"item_id": "'
        + str(uuid4())
        + '", "confidence": 0.7, "reason": "Primera frase.\nSegunda frase."}]}'
    )

    output = parse_editor_output(raw)

    assert len(output.publish) == 1
    assert "\n" in output.publish[0].reason
