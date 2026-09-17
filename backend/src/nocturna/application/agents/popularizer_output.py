"""Esquema de validación de la salida cruda del Popularizer.

Mismo patrón que `reader_output.py` (T41): `domain/entities.py` define
`Finding` como entidad de dominio pura (sin Pydantic, ver
`ddd-conventions`); este módulo valida el JSON que devuelve el agente
*antes* de que un caso de uso lo traduzca a los cuatro campos de texto que
el Popularizer aporta a un `Finding` (`title`, `level_curious`,
`level_amateur`, `level_technical` -- el resto de campos de `Finding`,
`item_id`/`run_id`/`type`/`confidence`/`published_at`, los rellena el
propio caso de uso o el Editor, no el Popularizer).

**`PopularizerOutput` debe ser exactamente tan estricto como esos cuatro
campos de `Finding`, ni más ni menos (misma lección que T41, ver el
docstring de `reader_output.py` para el bloqueante que la motivó).**
`Finding.__post_init__` rechaza `title`/`level_curious`/`level_amateur`/
`level_technical` vacíos o en blanco con `_require_non_empty` (que hace
`value.strip()`); los validadores de abajo replican eso literalmente para
los cuatro campos. No hay comprobación de longitud aquí a propósito: los
topes de palabras de cada nivel viven solo en el prompt
(`prompts/popularizer.md`), porque validar longitud en el esquema
convertiría un texto largo -- un problema estético, no una violación de
invariante -- en un reintento real, una llamada de pago completa. `Finding`
tampoco exige ninguna longitud máxima, así que un esquema que la impusiera
sería más estricto que la entidad que valida, exactamente el error que
causó el bloqueante de T41 en la dirección contraria.
"""

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from nocturna.application.agents.parsing import InvalidAgentOutput, extract_json_object


class PopularizerOutput(BaseModel):
    """Forma validada del JSON que debe devolver el Popularizer.

    `extra="ignore"`: un campo de más que el modelo añada por su cuenta no
    justifica un reintento, que cuesta una llamada real. `strict=True`: no
    se coacciona ningún tipo; los cuatro campos son `str` y deben llegar
    como tal.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    title: str
    level_curious: str
    level_amateur: str
    level_technical: str

    @field_validator("title", "level_curious", "level_amateur", "level_technical")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Espejo exacto de `_require_non_empty` para cada uno de estos campos en `Finding`."""
        if not value.strip():
            raise ValueError("no puede estar vacío ni en blanco")
        return value


def parse_popularizer_output(text: str) -> PopularizerOutput:
    """Extrae y valida la salida cruda del Popularizer.

    Compone `extract_json_object` con la validación de `PopularizerOutput`
    y traduce cualquier `ValidationError` de Pydantic a `InvalidAgentOutput`,
    para que quien llame tenga un único tipo de fallo que capturar, sea cual
    sea el motivo (igual que `parse_reader_output`).
    """
    payload = extract_json_object(text)
    try:
        return PopularizerOutput.model_validate(payload)
    except ValidationError as exc:
        raise InvalidAgentOutput(f"salida del Popularizer con forma inválida: {exc}") from exc
