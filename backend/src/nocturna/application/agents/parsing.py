"""Extracción del objeto JSON de la salida cruda de un agente.

Puro, sin IO salvo el `logging.debug` de la reparación (ver más abajo): solo
manipulación de texto. La salida de un agente LLM no siempre es un JSON
limpio -- puede venir envuelta en un fence de markdown o con prosa alrededor
pese a la instrucción del prompt de responder solo con JSON-- y este módulo
absorbe esas variantes antes de que `reader_output.py` (u otro esquema de
agente) valide la forma.

## Reparación de caracteres de control crudos (T42)

Medido en producción: el Popularizer metió saltos de línea **literales**
(bytes `\\n` crudos, no la secuencia escapada `\\\\n`) dentro del valor de
`level_technical`. `json.loads` lo rechaza con `Invalid control character`,
y el reintento que eso provoca **dobla el coste en tokens de esa llamada**
(medido: 9.120 tokens en vez de ~4.560). `_repair_control_chars` evita ese
reintento a coste cero reescribiendo, solo dentro de literales de cadena,
los tres caracteres de control que el prompt prohíbe explícitamente y que
aun así aparecen: `\\n`, `\\r`, `\\t` sin escapar, por su versión escapada.
Se limita a esos tres a propósito -- no a "cualquier carácter de control"
-- porque cada reparación añadida es una forma nueva de aceptar basura como
si fuera un JSON válido; si aparece un caso distinto, se añade cuando se
mida, no por anticipación.
"""

import json
import logging
import re

_MAX_SNIPPET_CHARS = 200

_OPENING_FENCE_RE = re.compile(r"^```[ \t]*(?:json|JSON)?[ \t]*\r?\n")

_CONTROL_CHAR_ESCAPES = {"\n": "\\n", "\r": "\\r", "\t": "\\t"}

_logger = logging.getLogger(__name__)


class InvalidAgentOutput(Exception):
    """La salida cruda de un agente no contiene un objeto JSON válido."""


def extract_json_object(text: str) -> dict:
    """Extrae el (único) objeto JSON de `text`.

    Orden de intentos:

    1. `text.strip()`.
    2. Si el resultado empieza por un fence de markdown (```` ``` ````, con
       o sin `json`/`JSON` tras los backticks), se retira el fence de
       apertura y todo lo que siga al de cierre.
    3. Se intenta `json.loads` directo sobre lo que quede.
    4. Si falla, se recorta desde el primer `{` hasta el último `}` y se
       reintenta -- para absorber prosa alrededor del JSON pese a la
       instrucción del prompt.
    5. Si **eso también falla**, y solo entonces, se intenta reparar
       caracteres de control crudos (`\n`, `\r`, `\t` sin escapar) dentro de
       literales de cadena -- ver el docstring del módulo -- y se reintenta
       una última vez. Un JSON ya válido nunca pasa por este paso: el
       reparador solo corre sobre texto que `json.loads` ya rechazó.
    6. Si nada de lo anterior produce un `dict`, se lanza
       `InvalidAgentOutput`. Un JSON válido que no sea un objeto (lista,
       número, cadena, `null`) cuenta como fallo.
    """
    stripped = text.strip()
    candidate = _strip_fence(stripped)

    parsed = _load_object(candidate)
    if parsed is not None:
        return parsed

    braces = _slice_between_braces(candidate)
    if braces is not None:
        parsed = _load_object(braces)
        if parsed is not None:
            return parsed

    repair_source = braces if braces is not None else candidate
    repaired = _repair_control_chars(repair_source)
    if repaired != repair_source:
        parsed = _load_object(repaired)
        if parsed is not None:
            _logger.debug(
                "extract_json_object reparó caracteres de control crudos dentro de "
                "una cadena JSON tras fallar el parseo normal (snippet=%r)",
                _snippet(text),
            )
            return parsed

    raise InvalidAgentOutput(
        f"la salida del agente no contiene un objeto JSON válido: {_snippet(text)}"
    )


def _strip_fence(text: str) -> str:
    match = _OPENING_FENCE_RE.match(text)
    if match is None:
        return text
    rest = text[match.end() :]
    closing = rest.rfind("```")
    if closing == -1:
        return rest
    return rest[:closing].strip()


def _load_object(text: str) -> dict | None:
    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _slice_between_braces(text: str) -> str | None:
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        return None
    return text[start : end + 1]


def _repair_control_chars(text: str) -> str:
    """Escapa `\\n`, `\\r` y `\\t` crudos encontrados dentro de literales de cadena.

    No toca nada fuera de comillas: el formateo entre claves y valores
    (indentación, saltos de línea del propio documento JSON) es legal y se
    deja intacto. Lleva un estado mínimo -- `in_string` y `escape_next` --
    para distinguir una comilla que cierra la cadena de una comilla
    escapada (`\\"`) y para que una barra invertida escapada (`\\\\`)
    seguida de comilla no se confunda con un escape de comilla: tras
    `escape_next`, el carácter siguiente se copia tal cual y el flag se
    limpia, sin volver a interpretarlo.

    No repara nada más (comillas sin escapar, comas colgantes, claves sin
    comillas...): cada reparación adicional es una forma nueva de aceptar
    como bueno un JSON que en realidad está mal formado por otra razón, y
    el prompt ya pide explícitamente no meter estos saltos de línea -- este
    reparador es la red para cuando, aun así, ocurre.
    """
    out: list[str] = []
    in_string = False
    escape_next = False
    for char in text:
        if in_string:
            if escape_next:
                out.append(char)
                escape_next = False
            elif char == "\\":
                out.append(char)
                escape_next = True
            elif char == '"':
                in_string = False
                out.append(char)
            elif char in _CONTROL_CHAR_ESCAPES:
                out.append(_CONTROL_CHAR_ESCAPES[char])
            else:
                out.append(char)
        else:
            if char == '"':
                in_string = True
            out.append(char)
    return "".join(out)


def _snippet(text: str) -> str:
    if len(text) <= _MAX_SNIPPET_CHARS:
        return text
    return text[:_MAX_SNIPPET_CHARS] + "…"
