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

#: Versión a mano del prompt del Reader con medidas estructuradas (T71.c),
#: a partir de `reader-measures-exp1.md` (T71.b). Fichero propio
#: (`reader-v3.md`), no una edición de `reader.md`: el Reader base
#: (`READER_PROMPT_VERSION`) sigue existiendo para los ítems fuera de
#: `[reader] measurement_categories` (`config/pipeline.toml`). Mismo
#: criterio de subida a mano que las constantes vecinas.
READER_V3_PROMPT_VERSION: str = "reader-v3"

#: Versión a mano del prompt del Popularizer, mismo criterio que
#: `READER_PROMPT_VERSION`: se sube a mano al editar `popularizer.md`.
#: Subida a "popularizer-v2" en T42: se reforzó la regla de no usar saltos
#: de línea literales dentro de cadenas JSON (medida en producción, causó
#: un reintento que dobló el coste de la llamada). T60 compara
#: calibraciones por esta etiqueta.
POPULARIZER_PROMPT_VERSION: str = "popularizer-v2"

#: Versión a mano del prompt del redactor de tensiones (T76, `writer-v1.md`):
#: escribe título y tres niveles de un `catalog_tension` a partir de los
#: números ya calculados. Mismo criterio de subida a mano.
WRITER_PROMPT_VERSION: str = "writer-v1"

#: Versión a mano del prompt del Editor, mismo criterio que
#: `READER_PROMPT_VERSION`/`POPULARIZER_PROMPT_VERSION`: se sube a mano al
#: editar el prompt. `editor-v1` (T43, `editor.md`, que se conserva) decidía
#: por `item_id`; `editor-v2` (T89, `editor-v2.md`) describe los tres tipos
#: de candidato (`paper_explained`, `primera_medida`,
#: `confirmacion_independiente`) y decide por `candidate_id`; `editor-v3`
#: (T76, `editor-v3.md`) añade `catalog_tension`. Conserva las
#: lecciones de T42 (nada de fences, nunca un salto de línea real dentro de
#: una cadena JSON).
EDITOR_PROMPT_VERSION: str = "editor-v3"


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
