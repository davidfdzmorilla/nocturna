"""Piezas para los tests de deduplicación entre vías (T95): un paper de arXiv que ya
cuenta un planeta/parámetro por la vía arXiv y la misma alta en el archivo.

Casos reales: HD 65216 c, periodo (arXiv 2610.02783, Wallace et al. 2026 frente a
Wittenmyer et al. 2019, 10,09 sigma) y TOI-2158 b, radio (arXiv 2609.30915,
Knudstrup et al. 2026, 3,18 sigma).
"""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helpers.exoplanet import make_measurement
from helpers.finding_payloads import (
    catalog_tension_two_priors,
    first_measurement_absent,
    independent_confirmation,
)
from helpers.solution_tension_writer import NEW_KEY, make_solution_tension
from nocturna.domain.entities import (
    Finding,
    FindingType,
    MeasuredParameter,
    MeasurementUnit,
)
from nocturna.domain.solution_tension import SolutionTension
from nocturna.domain.tension import EvaluationStatus, TensionEvaluation

HD65216_ARXIV_ID = "2610.02783"
TOI2158_ARXIV_ID = "2609.30915"
EVALUATED_AT = datetime(2026, 10, 15, 3, 0, tzinfo=UTC)

PERIOD = MeasuredParameter.PERIOD
_UNIT = {
    MeasuredParameter.PERIOD: MeasurementUnit.DAY,
    MeasuredParameter.MASS: MeasurementUnit.M_JUP,
    MeasuredParameter.RADIUS: MeasurementUnit.R_EARTH,
}


def arxiv_evaluation(
    item_id: UUID,
    *,
    planet: str,
    parameter: MeasuredParameter,
    reading_id: UUID | None = None,
) -> TensionEvaluation:
    """Evaluación del paper sobre `planet` (nombre del archivo) y `parameter`.

    `awaiting_reference` basta: lo que importa a T95 es la clave
    (paper, planeta, parámetro) y no el resultado de la comparación."""
    return TensionEvaluation(
        reading_id=reading_id or uuid4(),
        item_id=item_id,
        planet_name=planet,
        parameter=parameter,
        measurements=(
            make_measurement(
                1.0, 0.1, 0.1, planet_name=planet, parameter=parameter, unit=_UNIT[parameter]
            ),
        ),
        status=EvaluationStatus.AWAITING_REFERENCE,
        evaluated_at=EVALUATED_AT,
        archive_planet_name=planet,
    )


def arxiv_finding(
    finding_type: FindingType,
    *,
    item_id: UUID,
    run_id: UUID,
    evaluation_id: UUID,
    published: bool = False,
) -> Finding:
    """`Finding` de la vía arXiv del tipo pedido (payload mínimo válido)."""
    payload: dict[str, object] = {
        FindingType.CATALOG_TENSION: {"catalog_tension": catalog_tension_two_priors()},
        FindingType.PRIMERA_MEDIDA: {"first_measurement": first_measurement_absent()},
        FindingType.CONFIRMACION_INDEPENDIENTE: {
            "independent_confirmation": independent_confirmation()
        },
    }[finding_type]
    extra: dict[str, object] = {}
    if published:
        extra = {"published_at": EVALUATED_AT, "confidence": 0.8}
    return Finding(
        item_id=item_id,
        run_id=run_id,
        type=finding_type,
        title="Hallazgo de la vía arXiv",
        level_curious="c",
        level_amateur="a",
        level_technical="t",
        tension_evaluation_id=evaluation_id,
        **payload,
        **extra,
    )


def archive_tension(
    item_id: UUID | None,
    *,
    planet: str = "HD 65216 c",
    parameter: MeasuredParameter = MeasuredParameter.PERIOD,
    arxiv_id: str | None = HD65216_ARXIV_ID,
    new_key: str = NEW_KEY,
    sigma: float = 10.09,
) -> SolutionTension:
    """Alta del archivo elegible cuyo `new.arxiv_id` es `arxiv_id`."""
    # `make_solution_tension` solo sabe de periodo y masa: radio se obtiene desde masa.
    base_parameter = PERIOD if parameter is PERIOD else MeasuredParameter.MASS
    base = make_solution_tension(
        item_id, planet=planet, parameter=base_parameter, new_key=new_key, sigma=sigma
    )
    unit = _UNIT[parameter]
    return replace(
        base,
        parameter=parameter,
        new=replace(base.new, arxiv_id=arxiv_id, unit=unit, parameter=parameter),
        reference=replace(base.reference, unit=unit, parameter=parameter),
    )
