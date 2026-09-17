"""Carga de los prompts de rol (`system_prompt`) de los agentes del pipeline.

Los prompts viven como ficheros `.md` versionados en `prompts/`, no inline
en Python (`CLAUDE.md`, "Agentes"). No se llama `prompts.py` para no
colisionar con el paquete `prompts/` que vive junto a este módulo.
"""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent / "prompts"

#: Versión a mano del prompt del Reader, subida al editar `reader.md`.
#: Se eligió una constante legible frente a un hash de contenido porque el
#: informe de calibración de T60 la muestra tal cual, no un hash.
READER_PROMPT_VERSION: str = "reader-v2"


def load_prompt(name: str) -> str:
    """Lee `prompts/<name>.md` y devuelve su contenido.

    Falla ruidosamente (`FileNotFoundError`) si el prompt no existe: un
    prompt ausente no debe degradar a una cadena vacía y gastar una llamada
    real con un `system_prompt` en blanco.
    """
    path = _PROMPTS_DIR / f"{name}.md"
    if not path.is_file():
        raise FileNotFoundError(f"no existe el prompt '{name}' en {_PROMPTS_DIR}")
    return path.read_text(encoding="utf-8")
