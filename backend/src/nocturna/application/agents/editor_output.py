"""Esquema de validación de la salida cruda del Editor.

Mismo patrón que `popularizer_output.py` (T42): `domain/entities.py` define
`Finding` como entidad de dominio pura (sin Pydantic, ver
`ddd-conventions`); este módulo valida el JSON que devuelve el Editor
*antes* de que un caso de uso lo traduzca a las llamadas a
`Finding.publish(confidence, at)` sobre los `Finding` ya construidos por el
Popularizer (`item_id`/`run_id`/`type`/los tres niveles de texto los aporta
el Popularizer, no el Editor; el Editor solo decide el subconjunto a
publicar y su `confidence`).

**`EditorOutput` debe ser exactamente tan estricto como `Finding.publish`
y `Finding._validate_confidence`, ni más ni menos (misma lección que T41,
ver el docstring de `reader_output.py` para el bloqueante que la
motivó).** `Finding._validate_confidence` rechaza cualquier `confidence`
fuera de `[0.0, 1.0]`; el validador de rango de abajo replica eso
literalmente. `reason` no es un campo de `Finding` (es el motivo que el
Editor da para su propio informe, no algo que se persista en la entidad),
pero se exige no vacío ni en blanco por la misma regla defensiva que T41
dejó fijada para todo texto libre que un agente devuelve: una cadena en
blanco no es un fallo de validación que deba tratarse como gratis, y
aceptarla como "razón" sería aceptar basura sin motivo.
"""

from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from nocturna.application.agents.parsing import InvalidAgentOutput, extract_json_object


class EditorDecision(BaseModel):
    """Una decisión de publicación individual dentro de la lista `publish`.

    `extra="ignore"`: un campo de más que el modelo añada por su cuenta no
    justifica un reintento, que cuesta una llamada real. `strict=True` para
    todos los campos salvo `item_id`: el JSON que devuelve el Editor lleva
    el identificador como cadena (es lo único que un modelo puede escribir
    en JSON), así que `item_id` es la única excepción a `strict=True`, para
    parsear esa cadena como `UUID`; `confidence` y `reason` sí exigen su
    tipo exacto.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    item_id: UUID = Field(strict=False)
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str

    @field_validator("reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Espejo de la regla defensiva de T41: sin cadenas vacías ni en blanco."""
        if not value.strip():
            raise ValueError("no puede estar vacío ni en blanco")
        return value


class EditorOutput(BaseModel):
    """Forma validada del JSON que debe devolver el Editor.

    `extra="ignore"` por el mismo motivo que `EditorDecision`. `publish`
    vacía es una respuesta válida y legítima (ver `prompts/editor.md`): que
    ningún candidato merezca publicarse esta noche no es un fallo del
    Editor, es una decisión editorial correcta.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    publish: list[EditorDecision]


def parse_editor_output(text: str) -> EditorOutput:
    """Extrae y valida la salida cruda del Editor.

    Compone `extract_json_object` con la validación de `EditorOutput` y
    traduce cualquier `ValidationError` de Pydantic a `InvalidAgentOutput`,
    para que quien llame tenga un único tipo de fallo que capturar, sea
    cual sea el motivo (igual que `parse_popularizer_output`).
    """
    payload = extract_json_object(text)
    try:
        return EditorOutput.model_validate(payload)
    except ValidationError as exc:
        raise InvalidAgentOutput(f"salida del Editor con forma inválida: {exc}") from exc
