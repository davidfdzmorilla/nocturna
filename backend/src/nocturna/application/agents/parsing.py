"""Extracción del objeto JSON de la salida cruda de un agente.

Puro, sin IO: solo manipulación de texto. La salida de un agente LLM no
siempre es un JSON limpio -- puede venir envuelta en un fence de markdown o
con prosa alrededor pese a la instrucción del prompt de responder solo con
JSON-- y este módulo absorbe esas variantes antes de que `reader_output.py`
(u otro esquema de agente) valide la forma.
"""

import json
import re

_MAX_SNIPPET_CHARS = 200

_OPENING_FENCE_RE = re.compile(r"^```[ \t]*(?:json|JSON)?[ \t]*\r?\n")


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
    5. Si nada de lo anterior produce un `dict`, se lanza
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


def _snippet(text: str) -> str:
    if len(text) <= _MAX_SNIPPET_CHARS:
        return text
    return text[:_MAX_SNIPPET_CHARS] + "…"
