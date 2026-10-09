"""Texto del resumen semanal del archivo (T84): valores con errores y unidad.

Misma regla de unidades que los textos de T89/T91 (`unit_label`: M⊕, R⊕, M♃,
R♃, d) y mismo formato de número (coma decimal, `format_number`). Sin LLM.
"""

from nocturna.application.measurement_finding_texts import format_number, unit_label
from nocturna.domain.archive import ArchiveParameterValue, limit_of, parameter_unit
from nocturna.domain.entities import MeasuredParameter, MeasurementLimit

_MINUS = "−"
_MISSING = "sin valor"
_PARAMETER_LABEL = {
    MeasuredParameter.MASS: "masa",
    MeasuredParameter.RADIUS: "radio",
    MeasuredParameter.PERIOD: "periodo",
}


def parameter_label(parameter: MeasuredParameter) -> str:
    return _PARAMETER_LABEL[parameter]


def format_archive_value(p: ArchiveParameterValue | None, parameter: MeasuredParameter) -> str:
    """ "5,2 ± 0,3 M⊕", "5,2 +0,4 / −0,3 M⊕", "< 5,2 M⊕" (cota) o "sin valor".

    Los errores se muestran en valor absoluto (el archivo da `err2` negativo).
    """
    if p is None or p.value is None:
        return _MISSING
    unit = unit_label(parameter_unit(parameter))
    limit = limit_of(p)
    number = format_number(p.value)
    if limit == MeasurementLimit.UPPER:
        return f"< {number} {unit}"
    if limit == MeasurementLimit.LOWER:
        return f"> {number} {unit}"
    if p.err1 is None or p.err2 is None:
        return f"{number} {unit}"
    plus, minus = abs(p.err1), abs(p.err2)
    if plus == minus:
        return f"{number} ± {format_number(plus)} {unit}"
    return f"{number} +{format_number(plus)} / {_MINUS}{format_number(minus)} {unit}"
