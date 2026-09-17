"""Tests de `application/agents/parsing.py::_repair_control_chars` (T42).

Este reparador es un caso especial dentro de `parsing.py`: nace de un
incidente real en producción (ver el docstring del módulo) y su peligro no
es que falle en detectar basura -- es que sea **demasiado generoso** y
convierta basura en algo que `json.loads` acepta sin protestar, dato que
después se publica en la web. Por eso este fichero prueba tanto el "sí
repara lo que debe" como el "no toca lo que no debe", y en la sección de
mutación fuerza deliberadamente las dos formas de fallo peligrosas
(infra-arreglo y sobre-arreglo) para comprobar que cada una muere con un
test.

Puro, sin IO ni base de datos, sin `FakeLLMProvider`: el reparador no llama
a ningún LLM, solo manipula texto. Ningún test llama a Claude -- ver
`.claude/skills/testing-without-claude`.

Sobre el guardián de loggers: `alembic/env.py::fileConfig` deshabilita
cualquier logger de `nocturna.*` no declarado en `alembic.ini` cuando la
suite completa ejecuta primero `tests/db/` (orden alfabético). Los tests
que dependen de `caplog` reactivan `parsing_module._logger.disabled` a
mano, mismo patrón que `test_agent_runner.py` y `test_read_item.py`.
"""

import logging

import pytest

from nocturna.application.agents import parsing as parsing_module
from nocturna.application.agents.parsing import (
    InvalidAgentOutput,
    _repair_control_chars,
    extract_json_object,
)

_LOGGER_NAME = "nocturna.application.agents.parsing"


@pytest.fixture(autouse=True)
def _reactivate_parsing_logger(monkeypatch: pytest.MonkeyPatch):
    """Ver docstring del módulo: neutraliza el `disabled` que deja
    `alembic/env.py` cuando la suite completa corre `tests/db/` antes que
    este fichero. Autouse porque *cualquier* test de aquí puede acabar
    ejerciendo el camino de reparación y, con él, el `logging.debug`.
    """
    monkeypatch.setattr(parsing_module._logger, "disabled", False)


# --- 1. El caso real de producción ------------------------------------------


def test_salto_crudo_y_escapado_conviven_en_el_mismo_objeto():
    """El incidente medido: un campo con salto de línea crudo (el bug) junto
    a otro campo del mismo objeto con un `\\n` ya escapado correctamente (lo
    que el prompt pedía). Ambos deben acabar con el mismo carácter de salto
    de línea real tras el parseo -- el reparador no debe alterar el que ya
    estaba bien.
    """
    text = '{"already_escaped": "line one\\nline two", "level_technical": "line three\nline four"}'

    result = extract_json_object(text)

    assert result["already_escaped"] == "line one\nline two"
    assert result["level_technical"] == "line three\nline four"


# --- 2. \r y \t crudos, cada uno por su lado --------------------------------


def test_retorno_de_carro_crudo_se_repara():
    text = '{"field": "before\rafter"}'

    result = extract_json_object(text)

    assert result["field"] == "before\rafter"


def test_tabulador_crudo_se_repara():
    text = '{"field": "before\tafter"}'

    result = extract_json_object(text)

    assert result["field"] == "before\tafter"


# --- 3. Comilla escapada seguida de un salto crudo más adelante ------------


def test_comilla_escapada_no_cierra_la_cadena_antes_de_tiempo():
    """`\\"` en mitad de la cadena no debe interpretarse como el cierre: el
    reparador debe seguir viendo el resto de la cadena como parte del mismo
    literal, incluido el salto crudo que viene después.
    """
    text = '{"field": "abc \\" def\nghi"}'

    result = extract_json_object(text)

    assert result["field"] == 'abc " def\nghi'


# --- 4. Barra invertida escapada justo antes de la comilla de cierre -------


def test_barra_invertida_escapada_no_contamina_la_comilla_de_cierre_real():
    """La cadena contiene un salto crudo (dispara la reparación) y termina
    en una barra invertida ya escapada (`\\\\`) pegada a la comilla de
    cierre real. Si `escape_next` se colara hasta esa comilla, la cadena
    parecería no cerrarse nunca y el reparador comería el resto del
    documento.
    """
    text = '{"field": "line one\nline two ends with backslash \\\\"}'

    result = extract_json_object(text)

    assert result["field"] == "line one\nline two ends with backslash \\"


# --- 5. Varias cadenas en el mismo objeto: solo se tocan las que hace falta -


def test_solo_se_repara_la_cadena_que_lo_necesita_el_resto_byte_a_byte_igual():
    text = (
        '{"first": "no problem here", '
        '"second": "bad\nnewline", '
        '"third": "unicode: café, no tab here, fine"}'
    )

    repaired = _repair_control_chars(text)

    assert '"first": "no problem here"' in repaired
    assert '"third": "unicode: café, no tab here, fine"' in repaired
    assert '"second": "bad\\nnewline"' in repaired

    # Reconstrucción exacta: deshacer solo el trozo reparado debe devolver
    # el texto original byte a byte -- ninguna otra parte se tocó.
    reconstructed = repaired.replace('"second": "bad\\nnewline"', '"second": "bad\nnewline"')
    assert reconstructed == text


# --- 6. Salto crudo + error real: debe seguir fallando ----------------------


def test_salto_crudo_mas_coma_colgante_sigue_fallando_tras_reparar():
    """El test que impide que el reparador se convierta en un colador: si el
    JSON tiene, además del salto crudo, un error real (coma colgante), la
    reparación no debe disfrazarlo de JSON válido.
    """
    text = '{"a": "line one\nline two", "b": 1,}'

    with pytest.raises(InvalidAgentOutput):
        extract_json_object(text)


# --- 7. Un JSON ya válido nunca dispara el reparador ------------------------


def test_json_valido_no_dispara_el_reparador(caplog: pytest.LogCaptureFixture):
    text = '{"a": 1, "b": "sin problemas"}'

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        result = extract_json_object(text)

    assert result == {"a": 1, "b": "sin problemas"}
    assert not any("repar" in record.getMessage() for record in caplog.records), (
        "un JSON que json.loads acepta a la primera no debe pasar por "
        "_repair_control_chars -- el reparador solo corre tras un fallo"
    )


# --- 8. Recorte por llaves y reparación en el mismo texto -------------------


def test_recorte_por_llaves_y_reparacion_combinados():
    text = 'Aquí tienes el resultado:\n{"field": "linea uno\nlinea dos"}\nEspero que ayude.'

    result = extract_json_object(text)

    assert result == {"field": "linea uno\nlinea dos"}


# --- 9. El logging.debug se emite cuando la reparación funciona ------------


def test_logging_debug_se_emite_cuando_la_reparacion_tiene_exito(
    caplog: pytest.LogCaptureFixture,
):
    text = '{"field": "linea uno\nlinea dos"}'

    with caplog.at_level(logging.DEBUG, logger=_LOGGER_NAME):
        extract_json_object(text)

    assert any(
        "repar" in record.getMessage() and record.levelno == logging.DEBUG
        for record in caplog.records
    ), "T60 necesita esta señal para medir con qué frecuencia se activa el reparador"


# --- 10. Salto crudo dentro de una clave, no solo de un valor --------------


def test_salto_crudo_dentro_de_una_clave_se_repara():
    text = '{"field with\nraw newline": "value"}'

    result = extract_json_object(text)

    assert result == {"field with\nraw newline": "value"}


# --- 11. Bordes: justo tras la comilla de apertura y justo antes del cierre -


def test_salto_crudo_pegado_a_la_comilla_de_apertura_y_a_la_de_cierre():
    text = '{"field": "\nvalue\n"}'

    result = extract_json_object(text)

    assert result["field"] == "\nvalue\n"


# --- 12. Fuera de las cadenas, el formato se conserva intacto --------------


def test_formato_fuera_de_las_cadenas_se_conserva_intacto():
    """La preocupación explícita: un JSON indentado con saltos de línea
    legales entre claves y valores no debe verse tocado por el reparador --
    solo la cadena que contiene el salto crudo. Se compara el texto
    reparado byte a byte contra lo esperado, no solo el resultado
    parseado, precisamente porque aquí lo que importa es lo que *no*
    cambia.
    """
    text = '{\n  "a": "before' + "\n" + 'after",\n  "b": 2\n}'
    expected = '{\n  "a": "before\\nafter",\n  "b": 2\n}'

    repaired = _repair_control_chars(text)

    assert repaired == expected

    # Y de punta a punta: el objeto final se parsea con la indentación
    # original intacta a su alrededor.
    result = extract_json_object(text)
    assert result == {"a": "before\nafter", "b": 2}
