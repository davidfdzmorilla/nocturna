"""Esquema de validación de la salida cruda del Reader.

`domain/entities.py` define `Reading` como entidad de dominio pura (sin
Pydantic, ver `ddd-conventions`); este módulo valida el JSON que devuelve el
agente *antes* de que un caso de uso lo traduzca a un `Reading`, porque esa
salida no se valida en ningún otro punto del pipeline. `CLAUDE.md` ("Agentes")
exige Pydantic para esto, y el veto a Pydantic en `application/`
(`test_domain_purity.py`, revisión de T30) se levanta aquí para T41 por
decisión explícita del autor: el motivo original del veto -- no repetir una
validación que ya ocurrió en `infrastructure/config.py` -- no aplica a una
salida de agente, que no se valida en ningún otro sitio.

**`ReaderOutput` debe ser exactamente tan estricto como `Reading` (bloqueante
de la revisión de T41).** `Reading.__post_init__` (`domain/entities.py`)
rechaza `summary`/`objects`/`claims` vacíos o en blanco con
`_require_non_empty` (que hace `value.strip()`); antes de esta corrección
`ReaderOutput` solo exigía `str`/`list[str]`, así que un payload como
`{"summary": "   ", ...}` pasaba `parse_reader_output` sin error y reventaba
`Reading(...)` con `InvariantViolation` **fuera** del camino de reintento que
captura `InvalidAgentOutput` -- una llamada ya cobrada por la suscripción que
no dejaba ni `AgentCall` ni reintento. Los validadores de abajo replican
literalmente `_require_non_empty` (rechazar cadenas en blanco, no solo
vacías) para `summary` y para cada elemento de `objects`/`claims`, ni más ni
menos: si esta forma vuelve a divergir de la entidad, reaparece el mismo
bloqueante con otra forma.
"""

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from nocturna.application.agents.parsing import InvalidAgentOutput, extract_json_object


class ReaderOutput(BaseModel):
    """Forma validada del JSON que debe devolver el Reader.

    `extra="ignore"`: un campo de más que el modelo añada por su cuenta (p.
    ej. un `confidence` no pedido) no justifica un reintento, que cuesta una
    llamada real. `strict=True`: no se coacciona `"4"` a `4`; un modelo que
    hoy devuelve tipos laxos mañana devuelve basura, y conviene verlo en el
    primer intento y no en producción.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    summary: str
    objects: list[str]
    claims: list[str]
    interest_score: int = Field(ge=1, le=5)

    @field_validator("summary")
    @classmethod
    def _summary_not_blank(cls, value: str) -> str:
        """Espejo exacto de `_require_non_empty(self.summary, "summary")`."""
        if not value.strip():
            raise ValueError("'summary' no puede estar vacío ni en blanco")
        return value

    @field_validator("objects", "claims")
    @classmethod
    def _elements_not_blank(cls, value: list[str]) -> list[str]:
        """Espejo exacto del bucle `for obj in self.objects: _require_non_empty(...)`
        (e ídem para `claims`) de `Reading.__post_init__`. Una lista vacía sigue
        siendo válida -- la entidad tampoco la prohíbe -- solo sus elementos, si
        los hay, no pueden estar vacíos ni en blanco."""
        for element in value:
            if not element.strip():
                raise ValueError("los elementos de la lista no pueden estar vacíos ni en blanco")
        return value


def parse_reader_output(text: str) -> ReaderOutput:
    """Extrae y valida la salida cruda del Reader.

    Compone `extract_json_object` con la validación de `ReaderOutput` y
    traduce cualquier `ValidationError` de Pydantic a `InvalidAgentOutput`,
    para que quien llame (el caso de uso del Reader, T41-siguiente) tenga un
    único tipo de fallo que capturar, sea cual sea el motivo.
    """
    payload = extract_json_object(text)
    try:
        return ReaderOutput.model_validate(payload)
    except ValidationError as exc:
        raise InvalidAgentOutput(f"salida del Reader con forma inválida: {exc}") from exc
