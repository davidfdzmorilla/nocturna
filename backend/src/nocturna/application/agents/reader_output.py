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

import math
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from nocturna.application.agents.parsing import InvalidAgentOutput, extract_json_object

#: Mismo mapa que `domain.entities.UNITS_BY_PARAMETER`, con claves y
#: valores en texto (no en los enums `MeasuredParameter`/`MeasurementUnit`)
#: porque `MeasurementOut` valida el JSON crudo del agente, antes de que
#: exista ningún enum de dominio -- la construcción de `Measurement(...)`
#: (`application/agents/reader_measurements.py`) vuelve a comprobar esta
#: misma regla con los enums reales, así que una divergencia entre ambos
#: mapas se vería ahí como `InvariantViolation`, nunca como un dato
#: corrupto persistido en silencio.
_UNITS_BY_PARAMETER: dict[str, frozenset[str]] = {
    "mass": frozenset({"M_jup", "M_earth"}),
    "radius": frozenset({"R_jup", "R_earth"}),
    "period": frozenset({"day"}),
}


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


class MalformedMeasurement(Exception):
    """Un elemento crudo de `measurements` no tiene la forma de `MeasurementOut`.

    Deliberadamente NO hereda de `InvalidAgentOutput` (eso reintentaría el
    ítem entero por culpa de una sola medida) ni de `InvariantViolation`
    (que es del dominio, y esto todavía es JSON crudo sin validar). Es la
    señal que consume `reader_measurements.filter_measurements` para
    descartar solo esa medida, sin tocar el resto de la salida del Reader
    v3 ni el resultado de `AgentRunner.run` (T71.c, decisión del autor).
    """


class MeasurementOut(BaseModel):
    """Una medida cruda, tal como la pide `prompts/reader-v3.md` (T71.c).

    Espejo exacto de las invariantes de `Measurement`
    (`domain/entities.py`): mismos campos, mismas comprobaciones de valor
    finito, error no negativo y unidad coherente con el parámetro. Vive
    aquí y no como un `dataclass` de dominio porque, a diferencia de
    `Measurement`, esto valida JSON crudo de un tercero no confiable --
    mismo criterio que `ReaderOutput` frente a `Reading` (ver el docstring
    del módulo). `strict=True`/`extra="ignore"`: mismo criterio que
    `ReaderOutput` -- un campo de más no cuesta un reintento, un tipo laxo
    (`"4"` en vez de `4`) sí debe verse en el primer intento.

    No comprueba `planet_name`/`evidence` contra el abstract (eso exige
    contexto que este modelo no tiene) ni la salvaguarda de la anfitriona:
    ambas viven en `application/agents/reader_measurements.py`, después de
    que esta forma ya haya validado.
    """

    model_config = ConfigDict(extra="ignore", strict=True)

    planet_name: str
    parameter: str
    value: float
    err_plus: float | None = None
    err_minus: float | None = None
    unit: str
    limit: str
    origin: str
    evidence: str

    @field_validator("planet_name", "evidence")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        """Espejo de `_require_non_empty` para `planet_name`/`evidence`."""
        if not value.strip():
            raise ValueError("no puede estar vacío ni en blanco")
        return value

    @field_validator("parameter")
    @classmethod
    def _parameter_known(cls, value: str) -> str:
        if value not in _UNITS_BY_PARAMETER:
            raise ValueError(f"'parameter' debe ser uno de {sorted(_UNITS_BY_PARAMETER)}")
        return value

    @field_validator("limit")
    @classmethod
    def _limit_known(cls, value: str) -> str:
        if value not in {"none", "upper", "lower"}:
            raise ValueError("'limit' debe ser uno de ['lower', 'none', 'upper']")
        return value

    @field_validator("origin")
    @classmethod
    def _origin_known(cls, value: str) -> str:
        if value not in {"this_work", "literature"}:
            raise ValueError("'origin' debe ser uno de ['literature', 'this_work']")
        return value

    @field_validator("value")
    @classmethod
    def _value_finite(cls, value: float) -> float:
        """Espejo de `math.isfinite(self.value)` y `self.value <= 0` en
        `Measurement.__post_init__`: finito y estrictamente positivo (masa,
        radio y periodo no admiten cero ni negativos, T71.c, decisión del
        autor, revisión 2)."""
        if not math.isfinite(value):
            raise ValueError("'value' debe ser un número finito")
        if value <= 0:
            raise ValueError("'value' debe ser mayor que cero")
        return value

    @field_validator("err_plus", "err_minus")
    @classmethod
    def _err_valid(cls, value: float | None) -> float | None:
        """Espejo del bucle de `err_plus`/`err_minus` en `Measurement.__post_init__`:
        finito y no negativo, si está informado."""
        if value is None:
            return value
        if not math.isfinite(value):
            raise ValueError("el error debe ser un número finito")
        if value < 0:
            raise ValueError("el error no puede ser negativo")
        return value

    @model_validator(mode="after")
    def _unit_coherent_with_parameter(self) -> "MeasurementOut":
        """Espejo de `self.unit not in _UNITS_BY_PARAMETER[self.parameter]`
        en `Measurement.__post_init__`."""
        allowed = _UNITS_BY_PARAMETER[self.parameter]
        if self.unit not in allowed:
            raise ValueError(
                f"unit={self.unit!r} no es coherente con parameter={self.parameter!r} "
                f"(esperado uno de {sorted(allowed)})"
            )
        return self


def parse_measurement(raw: object) -> MeasurementOut:
    """Valida un elemento crudo de `measurements` como `MeasurementOut`.

    Lanza `MalformedMeasurement` -- nunca `InvalidAgentOutput` ni
    `InvariantViolation` -- para que
    `reader_measurements.filter_measurements` descarte solo esta medida sin
    invalidar el resto de la salida del Reader v3 ni disparar un reintento
    del ítem completo (T71.c, decisión del autor).
    """
    try:
        return MeasurementOut.model_validate(raw)
    except ValidationError as exc:
        raise MalformedMeasurement(f"medida con forma inválida: {exc}") from exc


class ReaderV3Output(ReaderOutput):
    """`ReaderOutput` (los cuatro campos de producción) + `measurements`.

    `measurements: list[Any]` a propósito, sin validar cada elemento como
    `MeasurementOut` aquí: una sola medida mal formada no debe invalidar
    toda la salida del Reader (lo que dispararía un reintento del ítem
    completo por culpa de un solo elemento de la lista). Cada elemento se
    valida por separado, después, con `parse_measurement` -- ver
    `application/agents/reader_measurements.py::filter_measurements`. Este
    campo solo exige que exista y sea una lista: ausente o de otro tipo
    (`None`, un objeto, una cadena) es una salida con forma inválida,
    `InvalidAgentOutput`, que sí reintenta -- igual que un `summary`
    ausente.
    """

    measurements: list[Any]


def parse_reader_v3_output(text: str) -> ReaderV3Output:
    """Extrae y valida la salida cruda del Reader v3 (T71.c).

    Mismo patrón que `parse_reader_output`: compone `extract_json_object`
    con la validación Pydantic y traduce `ValidationError` a
    `InvalidAgentOutput`. La ausencia de `measurements`, o un
    `measurements` que no sea una lista, cae en esa misma `ValidationError`
    -- y por tanto en el mismo reintento que ya tenía cualquier otro campo
    de `ReaderOutput` con forma inválida.
    """
    payload = extract_json_object(text)
    try:
        return ReaderV3Output.model_validate(payload)
    except ValidationError as exc:
        raise InvalidAgentOutput(f"salida del Reader v3 con forma inválida: {exc}") from exc
