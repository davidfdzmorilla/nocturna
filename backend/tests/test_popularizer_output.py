"""Tests de `application/agents/popularizer_output.py`.

Puros, sin IO ni base de datos: la entrada es texto fijo que simula la
salida cruda del Popularizer. Ningún test llama a Claude. Mismo patrón que
`test_reader_output_parsing.py` (T41), adaptado a los cuatro campos del
Popularizer (`title`, `level_curious`, `level_amateur`, `level_technical`)
en vez de los del Reader.
"""

import json
from uuid import uuid4

import pytest
from pydantic import BaseModel

from nocturna.application.agents.parsing import InvalidAgentOutput
from nocturna.application.agents.popularizer_output import (
    PopularizerOutput,
    parse_popularizer_output,
)
from nocturna.domain.entities import Finding, FindingType
from nocturna.domain.errors import InvariantViolation

# --- PopularizerOutput: forma válida ----------------------------------------


def _valid_payload(**overrides) -> dict:
    payload = {
        "title": "Un titular",
        "level_curious": "Explicación para curiosos.",
        "level_amateur": "Explicación para aficionados.",
        "level_technical": "Explicación técnica.",
    }
    payload.update(overrides)
    return payload


def test_popularizer_output_valido_se_parsea():
    text = json.dumps(_valid_payload())

    output = parse_popularizer_output(text)

    assert isinstance(output, PopularizerOutput)
    assert output.title == "Un titular"
    assert output.level_curious == "Explicación para curiosos."
    assert output.level_amateur == "Explicación para aficionados."
    assert output.level_technical == "Explicación técnica."


# --- PopularizerOutput: bloqueante de la revisión de T41, regresión --------
#
# `PopularizerOutput` debe ser exactamente tan estricto como los cuatro
# campos de texto de `Finding.__post_init__` (`_require_non_empty`,
# `domain/entities.py`). Es la misma regresión que T41 dejó documentada para
# `ReaderOutput`: si el esquema fuese más permisivo que la entidad, una
# llamada ya cobrada por la suscripción reventaría al construir `Finding(...)`
# fuera del camino de reintento -- cero contabilizado, la llamada perdida.
# Los cuatro campos, vacíos y en blanco, parametrizados: los ocho casos
# deben rechazarse igual.

_FIELD_NAMES = ["title", "level_curious", "level_amateur", "level_technical"]


@pytest.mark.parametrize("field_name", _FIELD_NAMES)
@pytest.mark.parametrize("blank_value", ["", "   "], ids=["vacio", "en_blanco"])
def test_popularizer_output_campo_vacio_o_en_blanco_falla(field_name, blank_value):
    payload = _valid_payload(**{field_name: blank_value})

    with pytest.raises(InvalidAgentOutput):
        parse_popularizer_output(json.dumps(payload))


# --- PopularizerOutput: claves extra se aceptan e ignoran, a propósito -----


def test_popularizer_output_con_claves_extra_se_acepta_y_las_ignora():
    """`extra="ignore"`: un campo de más no justifica gastar un reintento que
    cuesta una llamada real.
    """
    payload = _valid_payload()
    payload["confidence"] = 0.9
    payload["unexpected_field"] = "sorpresa"

    output = parse_popularizer_output(json.dumps(payload))

    assert not hasattr(output, "confidence")
    assert not hasattr(output, "unexpected_field")
    assert output.title == "Un titular"


# --- PopularizerOutput: tipo incorrecto rechazado -------------------------
#
# A diferencia de `ReaderOutput` (que tiene un campo `int`, donde `strict=True`
# sí cambia el resultado observable: `"4"`/`4.0`/`True` se coaccionarían a
# `4` en modo laxo), los cuatro campos de `PopularizerOutput` son `str`, y
# Pydantic v2 **nunca** coacciona `int`/`float`/`bool`/`None`/`list`/`dict` a
# `str`, ni siquiera en modo laxo (`strict=False`) -- solo `bytes` se
# coaccionaría, y `json.loads` no produce `bytes`. Verificado por mutación:
# cambiar `strict=True` por `strict=False` en el esquema no hace fallar
# ningún test de este fichero (mutante equivalente para este esquema en
# concreto). El test de abajo sigue siendo una regresión real -- un `title`
# entero debe rechazarse--, pero no se debe leer como una prueba de que
# `strict=True` está en efecto; `strict=True` se mantiene por paridad de
# intención con `ReaderOutput` y como defensa si algún campo cambiara a un
# tipo no-`str` en el futuro.


def test_popularizer_output_title_como_entero_se_rechaza():
    payload = _valid_payload(title=123)

    with pytest.raises(InvalidAgentOutput):
        parse_popularizer_output(json.dumps(payload))


# --- PopularizerOutput: campo ausente -----------------------------------


def test_popularizer_output_con_campo_ausente_falla():
    payload = _valid_payload()
    del payload["level_technical"]

    with pytest.raises(InvalidAgentOutput):
        parse_popularizer_output(json.dumps(payload))


# --- extract_json_object: envoltura, reutilizando lo que ya resuelve -------


def test_popularizer_output_en_fence_json_se_parsea():
    payload = _valid_payload()
    text = "```json\n" + json.dumps(payload) + "\n```"

    output = parse_popularizer_output(text)

    assert output.title == "Un titular"


def test_popularizer_output_con_prosa_alrededor_se_parsea():
    payload = _valid_payload()
    text = f"Aquí tienes el resultado:\n{json.dumps(payload)}\nEspero que ayude."

    output = parse_popularizer_output(text)

    assert output.level_curious == "Explicación para curiosos."


# --- PopularizerOutput y las invariantes de Finding no han divergido ------
#
# La guarda que habría evitado el bloqueante de T41 en la dirección
# contraria: en vez de escribir a mano una segunda lista literal de los
# cuatro campos (que se desincronizaría exactamente igual que el esquema que
# causó el bloqueante), este test deriva los campos a comprobar de
# `PopularizerOutput.model_fields` -- la fuente de verdad ya existente, no
# una copia -- y por cada uno comprueba que tanto el esquema como `Finding`
# rechazan la cadena en blanco. Si mañana se añade o se quita un campo de
# `PopularizerOutput` sin ajustar `Finding` (o al revés, un campo de más en
# `Finding` que el esquema no cubre), este test seguiría comparando solo los
# campos que declara `PopularizerOutput` -- no detectaría una divergencia por
# *ausencia* de campo en el esquema. Comprobar eso exigiría enumerar a mano
# los campos de texto de `Finding`, la misma lista duplicada que se quiere
# evitar; se documenta la limitación en vez de fingir cobertura completa.


def _minimal_finding_kwargs(**overrides) -> dict:
    kwargs = {
        "item_id": uuid4(),
        "run_id": uuid4(),
        "type": FindingType.PAPER_EXPLAINED,
        "title": "Un titular",
        "level_curious": "Explicación para curiosos.",
        "level_amateur": "Explicación para aficionados.",
        "level_technical": "Explicación técnica.",
    }
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize("field_name", list(PopularizerOutput.model_fields))
def test_popularizer_output_y_finding_coinciden_en_rechazar_el_campo_en_blanco(field_name):
    # PopularizerOutput.model_fields es la lista real de campos del esquema,
    # no una copia escrita a mano: si el esquema gana o pierde un campo, este
    # test lo recorre igual, sin tocarlo.
    assert issubclass(PopularizerOutput, BaseModel)

    with pytest.raises(InvalidAgentOutput):
        parse_popularizer_output(json.dumps(_valid_payload(**{field_name: "   "})))

    with pytest.raises(InvariantViolation):
        Finding(**_minimal_finding_kwargs(**{field_name: "   "}))
