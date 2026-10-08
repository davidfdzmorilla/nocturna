"""Evaluaciones `evaluated` de V1298 Tau b para los tests del redactor (T76)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from uuid import UUID, uuid4

from helpers.exoplanet import v1298_tension_results
from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.tension import EvaluationStatus, TensionEvaluation, TensionResult

EVALUATED_AT = datetime(2026, 10, 3, 3, 0, tzinfo=UTC)
V1298_ARXIV_ID = "2609.30038"
INDEPENDENT_ARXIV_ID = "2301.00001"

# Calculado al importar (recolección, sin bucle de eventos): `v1298_tension_results`
# usa `anyio.run` y no puede llamarse desde un test async.
_V1298_B_RESULT = v1298_tension_results()["V1298 Tau b"]


def v1298_evaluation(
    item_id: UUID,
    *,
    reference_overrides: dict | None = None,
    evidence: str | None = None,
    status: EvaluationStatus = EvaluationStatus.EVALUATED,
) -> TensionEvaluation:
    """Evaluación `evaluated` de la masa de V1298 Tau b (referencia 3,37-3,91 σ).

    La referencia por defecto (Livingston et al. 2026) lleva un `arxiv_id`
    distinto del paper para que `classify_solution` la dé por `INDEPENDENT`
    (las filas grabadas no lo traen). `reference_overrides` se aplica encima.
    """
    result = _V1298_B_RESULT
    overrides = {"arxiv_id": INDEPENDENT_ARXIV_ID, **(reference_overrides or {})}

    def _patched(prior: CatalogSolution) -> CatalogSolution:
        return replace(prior, **overrides) if prior.is_default else prior

    def _paper(m):
        return m if evidence is None else replace(m, evidence=evidence)

    comparisons = tuple(
        replace(c, prior=_patched(c.prior), paper=_paper(c.paper)) for c in result.comparisons
    )
    patched = TensionResult(
        item_id=item_id,
        planet_name=result.planet_name,
        parameter=result.parameter,
        comparisons=comparisons,
    )
    return TensionEvaluation(
        reading_id=uuid4(),
        item_id=item_id,
        planet_name=patched.planet_name,
        parameter=patched.parameter,
        measurements=tuple(dict.fromkeys(c.paper for c in comparisons)),
        status=status,
        evaluated_at=EVALUATED_AT,
        archive_planet_name="V1298 Tau b",
        result=patched,
    )
