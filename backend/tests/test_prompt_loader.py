"""Tests de `application/agents/prompt_loader.py`.

Puros: leen un fichero `.md` versionado del propio repositorio, sin red ni
base de datos ni LLM.
"""

import hashlib

import pytest

from nocturna.application.agents.prompt_loader import READER_PROMPT_VERSION, load_prompt

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
