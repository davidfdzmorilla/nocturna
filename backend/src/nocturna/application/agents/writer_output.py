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

    @field_validator("title", "level_curious", "level_amateur", "level_technical", mode="before")
    @classmethod
    def _unescape_literal_newlines(cls, value: object) -> object:
        """Convierte la barra invertida + `n` literal en un salto de línea (T96).

        Los prompts piden "`\\n` escapado entre párrafos" y a veces el modelo lo
        escapa dos veces (JSON `"\\\\n"`), de modo que el texto llegaría con los dos
        caracteres `\\` y `n` en vez de un salto, y la web los mostraría tal cual.
        Se sustituye toda aparición, sea cual sea el carácter siguiente: los
        prompts piden prosa en español sin LaTeX, así que un `\\nu` legítimo no
        existe, y distinguir por mayúscula/minúscula dejaría sin cubrir un párrafo
        que empiece en minúscula. Corre antes de `_not_blank` (modo `before`):
        un campo hecho solo de `\\n` literales queda en blanco y falla. Los
        valores que no son `str` pasan intactos para que la validación estricta
        los rechace.
        """
        if isinstance(value, str):
            return value.replace("\\n", "\n")
        return value

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


#: Máximo de caracteres del titular del resumen semanal (T84.r); el prompt
#: `writer-digest-v1.md` pide lo mismo.
DIGEST_TITLE_MAX_CHARS = 90


class DigestWriterOutput(BaseModel):
    """Forma validada del JSON del redactor del resumen semanal (T84.r):
    un titular (hasta 90 caracteres) y un resumen. Misma normalización del `\\n`
    literal que `WriterOutput` (T96); los campos extra se ignoran."""

    model_config = ConfigDict(extra="ignore", strict=True)

    title: str
    summary: str

    @field_validator("title", "summary", mode="before")
    @classmethod
    def _unescape_literal_newlines(cls, value: object) -> object:
        if isinstance(value, str):
            return value.replace("\\n", "\n")
        return value

    @field_validator("title", "summary")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("no puede estar vacío ni en blanco")
        return value

    @field_validator("title")
    @classmethod
    def _title_length(cls, value: str) -> str:
        if len(value) > DIGEST_TITLE_MAX_CHARS:
            raise ValueError(f"el titular supera {DIGEST_TITLE_MAX_CHARS} caracteres")
        return value


def parse_digest_writer_output(text: str) -> DigestWriterOutput:
    """Extrae y valida la salida cruda del redactor del resumen; todo fallo es
    `InvalidAgentOutput`."""
    payload = extract_json_object(text)
    try:
        return DigestWriterOutput.model_validate(payload)
    except ValidationError as exc:
        raise InvalidAgentOutput(
            f"salida del redactor del resumen con forma inválida: {exc}"
        ) from exc
