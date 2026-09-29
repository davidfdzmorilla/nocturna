"""Tests de `application/agents/prompt_loader.py`.

Puros: leen un fichero `.md` versionado del propio repositorio, sin red ni
base de datos ni LLM.
"""

import hashlib

import pytest

from nocturna.application.agents.prompt_loader import (
    READER_PROMPT_VERSION,
    READER_V3_PROMPT_VERSION,
    load_prompt,
)

#: Hash congelado de `prompts/reader.md` en el momento en que
#: `READER_PROMPT_VERSION` se fijó a `"reader-v2"`. Nada más en el proyecto
#: ata la una al otro: editar el prompt sin subir la versión no lo detecta
#: nadie, y T60 compara calibraciones por esa etiqueta -- de hecho, en el
#: ciclo de esta misma tarea hubo que subirla a mano sin que ningún test lo
#: exigiera. Si `test_reader_prompt_hash_congelado...` falla, es la señal:
#: sube `READER_PROMPT_VERSION` en `prompt_loader.py` y recalcula este hash.
_READER_PROMPT_SHA256 = "f409966337bfcce756a4c4ce741148c823f1427c0f40c08ee1145467ff635889"


def test_load_prompt_reader_existe_y_no_esta_vacio():
    content = load_prompt("reader")

    assert content.strip() != ""


def test_load_prompt_nombre_inexistente_lanza_filenotfounderror():
    """Un prompt ausente no debe degradar a una cadena vacía: un
    `system_prompt` en blanco gastaría una llamada real en silencio.
    """
    with pytest.raises(FileNotFoundError):
        load_prompt("no-existe-este-prompt")


def test_reader_prompt_version_esta_definida_y_no_vacia():
    assert isinstance(READER_PROMPT_VERSION, str)
    assert READER_PROMPT_VERSION.strip() != ""


def test_reader_prompt_hash_congelado_detecta_cambios_sin_subir_la_version():
    """Nada más en el proyecto ata `READER_PROMPT_VERSION` al contenido de
    `reader.md`: este hash congelado es esa atadura. Editar el prompt sin
    subir la versión no lo detecta nadie -- T60 compara calibraciones por
    esa etiqueta -- así que este test debe fallar con un mensaje que diga
    explícitamente qué hacer, no solo "el hash no coincide".
    """
    content = load_prompt("reader")
    actual = hashlib.sha256(content.encode("utf-8")).hexdigest()

    assert actual == _READER_PROMPT_SHA256, (
        "has cambiado el contenido de prompts/reader.md: sube "
        "READER_PROMPT_VERSION en application/agents/prompt_loader.py (nunca "
        "reutilices una versión ya publicada) y actualiza "
        f"_READER_PROMPT_SHA256 en este test al nuevo hash ({actual})"
    )


# --- reader-v3.md: neutralidad frente a los abstracts de T71.b (T71.c) -----

#: Nombres y cifras de `tests/fixtures/t71b/abstracts.json` que NO deben
#: aparecer en `reader-v3.md`: el prompt de producción se escribe a partir
#: del experimento de T71.b, y un ejemplo copiado sin darse cuenta de un
#: nombre o cifra real de esos abstracts sesgaría al modelo hacia lo que ya
#: ha visto en el propio prompt, en vez de extraerlo genuinamente del
#: abstract de cada noche.
_T71B_NAMES_AND_FIGURES = (
    "V1298",
    "TOI-2109",
    "RX J0534",
    "WASP-12",
    "TWA 7",
    "2.8",
    "0.52",
    "5.02",
    "1.347",
    "0.67247479",
    "54",
    "258",
)


def test_reader_v3_prompt_version_esta_definida_y_no_vacia():
    assert isinstance(READER_V3_PROMPT_VERSION, str)
    assert READER_V3_PROMPT_VERSION.strip() != ""


@pytest.mark.parametrize("needle", _T71B_NAMES_AND_FIGURES)
def test_reader_v3_prompt_no_contiene_nombres_ni_cifras_de_los_abstracts_de_t71b(needle):
    content = load_prompt("reader-v3")

    assert needle not in content, (
        f"'{needle}' (de tests/fixtures/t71b/abstracts.json) aparece en "
        "reader-v3.md: el prompt de producción no debe filtrar ningún nombre "
        "ni cifra real de los abstracts usados para diseñarlo"
    )
