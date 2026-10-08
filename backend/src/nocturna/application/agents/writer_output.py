"""Esquema de validación de la salida cruda del redactor de tensiones (T76).

Mismo patrón y misma estrictez que `popularizer_output.py`: los cuatro campos
de texto de `Finding` no pueden estar en blanco (espejo de
`_require_non_empty`), los campos extra se ignoran y no hay límite de
longitud (los topes de palabras viven solo en `prompts/writer-v1.md`).
El resto del `Finding` (tipo, `catalog_tension`, `tension_evaluation_id`,
`run_id`) lo aporta el caso de uso, nunca el modelo.
"""

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

from nocturna.application.agents.parsing import InvalidAgentOutput, extract_json_object


class WriterOutput(BaseModel):
    """Forma validada del JSON que debe devolver el redactor."""

    model_config = ConfigDict(extra="ignore", strict=True)

    title: str
    level_curious: str
    level_amateur: str
    level_technical: str

    @field_validator("title", "level_curious", "level_amateur", "level_technical")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Espejo exacto de `_require_non_empty` para estos campos en `Finding`."""
        if not value.strip():
            raise ValueError("no puede estar vacío ni en blanco")
        return value


def parse_writer_output(text: str) -> WriterOutput:
    """Extrae y valida la salida cruda del redactor; todo fallo es `InvalidAgentOutput`."""
    payload = extract_json_object(text)
    try:
        return WriterOutput.model_validate(payload)
    except ValidationError as exc:
        raise InvalidAgentOutput(f"salida del redactor con forma inválida: {exc}") from exc
