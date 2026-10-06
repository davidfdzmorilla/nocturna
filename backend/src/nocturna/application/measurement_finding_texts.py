"""Textos deterministas de los findings `primera_medida` y `confirmacion_independiente` (T89).

Plantillas en castellano, sin LLM y sin red: título y tres niveles salen solo
de los números del payload (`FirstMeasurement`, `IndependentConfirmation`) y
del `external_id` del `Item`. Decisión D1 (opción C) y D15 de T89: el texto
exacto lo aprueba el autor antes del commit.

Formato de números: coma decimal y el valor tal como vino de la medida (la
representación más corta del `float`, sin redondear más allá). Errores
simétricos como "± e", asimétricos como "+a / −b". σ con dos decimales.
Unidades: M_earth -> M⊕, R_earth -> R⊕, M_jup -> M♃, R_jup -> R♃. Si el paper da varias medidas
del mismo parámetro, amateur y technical las listan todas (separadas por
"; ", porque la coma ya es el separador decimal); título y nivel curioso usan
la primera.
"""

from dataclasses import dataclass
from decimal import Decimal

from nocturna.domain.entities import (
    FirstMeasurement,
    IndependentConfirmation,
    MeasuredParameter,
    MeasurementUnit,
    PaperMeasurement,
)
from nocturna.domain.tension import to_canonical

_MINUS = "−"

_UNIT_LABEL: dict[MeasurementUnit, str] = {
    MeasurementUnit.M_EARTH: "M⊕",
    MeasurementUnit.R_EARTH: "R⊕",
    MeasurementUnit.M_JUP: "M♃",
    MeasurementUnit.R_JUP: "R♃",
    MeasurementUnit.DAY: "d",
}

# {param_art}, {param_del}, {tierra}, {param}
_PARAM_ART = {MeasuredParameter.MASS: "la masa", MeasuredParameter.RADIUS: "el radio"}
_PARAM_DEL = {MeasuredParameter.MASS: "de la masa", MeasuredParameter.RADIUS: "del radio"}
_EARTH = {MeasuredParameter.MASS: "la de la Tierra", MeasuredParameter.RADIUS: "el de la Tierra"}
_PARAM_NAME = {MeasuredParameter.MASS: "masa", MeasuredParameter.RADIUS: "radio"}


@dataclass(frozen=True, slots=True)
class FindingTexts:
    """Título y tres niveles de un `Finding` de medidas."""

    title: str
    level_curious: str
    level_amateur: str
    level_technical: str


def format_number(value: float) -> str:
    """`repr` más corto del `float`, en notación posicional y con coma decimal."""
    return format(Decimal(repr(float(value))), "f").replace(".", ",")


def format_sigma(value: float) -> str:
    """σ con dos decimales y coma decimal."""
    return f"{value:.2f}".replace(".", ",")


def _format_errors(err_plus: float, err_minus: float) -> str:
    if err_plus == err_minus:
        return f"± {format_number(err_plus)}"
    return f"+{format_number(err_plus)} / {_MINUS}{format_number(err_minus)}"


def _format_with_errors(
    value: float, err_plus: float, err_minus: float, unit: MeasurementUnit
) -> str:
    return f"{format_number(value)} {_format_errors(err_plus, err_minus)} {_UNIT_LABEL[unit]}"


def _format_measurement(measurement: PaperMeasurement) -> str:
    return _format_with_errors(
        measurement.value, measurement.err_plus, measurement.err_minus, measurement.unit
    )


def _format_measurements(measurements: tuple[PaperMeasurement, ...]) -> str:
    return "; ".join(_format_measurement(m) for m in measurements)


def _earth_multiples(value: float, unit: MeasurementUnit) -> str:
    """Valor en masas o radios terrestres para el nivel curioso ("unas N
    veces"). En unidades terrestres, el valor tal cual; en las de Júpiter,
    convertido con la misma constante que el cálculo de σ y a tres cifras
    significativas."""
    if unit in (MeasurementUnit.M_EARTH, MeasurementUnit.R_EARTH):
        return format_number(value)
    rounded = Decimal(f"{to_canonical(value, unit):.3g}")
    return format(rounded, "f").replace(".", ",")


def render_primera_medida(first: FirstMeasurement, *, arxiv_id: str) -> FindingTexts:
    """Textos de una `primera_medida`. `arxiv_id` es el `external_id` del `Item`."""
    planet = first.paper_planet_name
    parameter = first.parameter
    head = first.measurements[0]
    absent = first.archive_planet_name is None

    title = f"Primera medida {_PARAM_DEL[parameter]} de {planet}: {_format_measurement(head)}"

    curious_state = (
        "El catálogo de exoplanetas de la NASA todavía no incluye este planeta."
        if absent
        else "El catálogo de exoplanetas de la NASA ya incluye el planeta, pero sin ninguna "
        f"medida previa {_PARAM_DEL[parameter]} publicada y confirmada con la que compararla."
    )
    level_curious = (
        f"Un artículo nuevo en arXiv mide por primera vez {_PARAM_ART[parameter]} del planeta "
        f"{planet}: unas {_earth_multiples(head.value, head.unit)} veces {_EARTH[parameter]}. "
        f"{curious_state}"
    )

    amateur_state = (
        "El planeta no figura en el NASA Exoplanet Archive."
        if absent
        else f"El NASA Exoplanet Archive tiene el planeta ({first.archive_planet_name}), pero "
        "ninguna solución publicada y confirmada con error en ambos sentidos para este "
        "parámetro."
    )
    level_amateur = (
        f"El artículo arXiv:{arxiv_id} da para {planet} {_PARAM_ART[parameter]} "
        f"{_format_measurements(first.measurements)}. {amateur_state} Es el resultado de un solo "
        "trabajo: aún no hay otra medida independiente que lo contraste."
    )

    technical_state = (
        "planeta no encontrado (servicio de alias: System Not Found)"
        if absent
        else f"planeta {first.archive_planet_name} presente; sin solución Published Confirmed "
        "con error bilateral, sin cota superior y sin solución del propio trabajo"
    )
    technical_parts = [
        f"Medidas extraídas del abstract de arXiv:{arxiv_id} ({planet}, {_PARAM_NAME[parameter]}): "
        f"{_format_measurements(first.measurements)}.",
        f"Estado en el NASA Exoplanet Archive: {technical_state}.",
    ]
    if first.archive_url:
        technical_parts.append(f"Ficha: {first.archive_url}.")
    technical_parts.append(
        "Comparación automática con el histórico del archivo; no sustituye a la lectura del "
        "artículo."
    )
    return FindingTexts(
        title=title,
        level_curious=level_curious,
        level_amateur=level_amateur,
        level_technical=" ".join(technical_parts),
    )


def render_confirmacion_independiente(
    confirmation: IndependentConfirmation, *, arxiv_id: str
) -> FindingTexts:
    """Textos de una `confirmacion_independiente`. `arxiv_id` es el `external_id` del `Item`."""
    planet = confirmation.paper_planet_name
    parameter = confirmation.parameter
    reference = confirmation.reference
    head = confirmation.measurements[0]

    title = f"Confirmación independiente {_PARAM_DEL[parameter]} de {planet}"

    level_curious = (
        f"Dos trabajos distintos han medido {_PARAM_ART[parameter]} del planeta {planet} y "
        "llegan a valores compatibles dentro de sus márgenes de error: "
        f"unas {_earth_multiples(head.value, head.unit)} veces {_EARTH[parameter]} frente a "
        f"unas {_earth_multiples(reference.value, reference.unit)}. "
        "Que dos medidas independientes coincidan da más confianza en el resultado."
    )

    reference_text = _format_with_errors(
        reference.value, reference.err_plus, reference.err_minus, reference.unit
    )
    level_amateur = (
        f"El artículo arXiv:{arxiv_id} mide {_PARAM_ART[parameter]} de {planet}: "
        f"{_format_measurements(confirmation.measurements)}. La referencia del NASA Exoplanet "
        f"Archive, {reference.refname}, da {reference_text}. La diferencia mayor es de "
        f"{format_sigma(max(confirmation.sigmas))} σ, dentro del margen de "
        f"{format_number(confirmation.max_sigma)} σ: las medidas son compatibles."
    )

    arxiv_suffix = f", arXiv:{reference.arxiv_id}" if reference.arxiv_id else ""
    level_technical = (
        f"Comparación de {planet} ({_PARAM_NAME[parameter]}) entre arXiv:{arxiv_id} y la solución "
        f"del NASA Exoplanet Archive {reference.refname} (entrada en el archivo: "
        f"{reference.releasedate.isoformat()}{arxiv_suffix}). σ por medida: "
        f"{'; '.join(format_sigma(s) for s in confirmation.sigmas)}, con "
        "σ = |x_p − x_r| / √(e_p² + e_r²) usando el error del lado que mira al otro valor. "
        f"Criterio: todas las medidas a ≤ {format_number(confirmation.max_sigma)} σ y la más "
        f"reciente de las dos entradas en los últimos {confirmation.window_days} días. "
        "Independencia comprobada solo como artículos distintos; no se comprueba si los métodos "
        f"o los equipos son distintos. Ficha: {confirmation.archive_url}."
    )
    return FindingTexts(
        title=title,
        level_curious=level_curious,
        level_amateur=level_amateur,
        level_technical=level_technical,
    )
