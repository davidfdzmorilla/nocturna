"""`GET /findings` y `GET /findings/{finding_id}` (T50 paso 3).

Traduce `page`/`size` de la petición HTTP a `limit`/`offset` -- eso es
cosa de esta capa, no de `ListPublishedFindings` (ver docstring de
`application/use_cases/list_findings.py`) -- y los dos casos de uso de
lectura a los esquemas de `api/schemas.py`, que deciden qué campos de
`Finding` cruzan hacia fuera.
"""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from nocturna.api.deps import get_findings_repository, get_items_repository
from nocturna.api.schemas import (
    CatalogTensionOut,
    FindingDetail,
    FindingsPageResponse,
    FindingSummary,
    FirstMeasurementOut,
    IndependentConfirmationOut,
    source_url,
)
from nocturna.application.use_cases.list_findings import GetPublishedFinding, ListPublishedFindings
from nocturna.domain.entities import FindingType
from nocturna.domain.repositories import FindingRepository, ItemRepository

router = APIRouter()

_FINDING_NOT_FOUND = "finding not found"

#: `Depends(...)` como metadato de `Annotated`, no como valor por defecto:
#: el estilo que recomienda hoy la documentación de FastAPI, y el motivo por
#: el que `pyproject.toml` ya no necesita `extend-immutable-calls` para
#: `Depends`/`Query` (ver comentario de `_SessionDep` en `api/deps.py`).
_FindingsDep = Annotated[FindingRepository, Depends(get_findings_repository)]
_ItemsDep = Annotated[ItemRepository, Depends(get_items_repository)]

#: Coincide a propósito con `MAX_PAGE` de `web/src/lib/pagination.ts`. Su
#: función es acotar `offset` lejos del límite de `bigint` de PostgreSQL
#: (con `size <= 50`, `offset <= 49_999_900`), no limitar la paginación
#: real; por encima, FastAPI responde `422` como con cualquier otro
#: parámetro inválido (T70).
MAX_PAGE = 999_999


@router.get("/findings", response_model=FindingsPageResponse)
def list_findings(
    findings: _FindingsDep,
    page: Annotated[int, Query(ge=1, le=MAX_PAGE)] = 1,
    size: Annotated[int, Query(ge=1, le=50)] = 20,
    finding_type: Annotated[FindingType | None, Query(alias="type")] = None,
) -> FindingsPageResponse:
    """Listado paginado, más recientes primero.

    Dentro de `1..MAX_PAGE`, una página sin resultados no es un error:
    devuelve `200` con `items` vacío y el `total` real, igual que
    cualquier otra página sin resultados. Por encima de `MAX_PAGE`,
    `422`, igual que `page=0` o `size=51`.

    `type` (T77) filtra por un solo `FindingType`; un valor desconocido o
    vacío da `422` por el propio enum, y el `total` es el del filtro.
    """
    offset = (page - 1) * size
    result = ListPublishedFindings(findings)(limit=size, offset=offset, finding_type=finding_type)
    summaries = []
    for finding in result.findings:
        if finding.published_at is None:
            # No debería ocurrir: `is_published` (segunda barrera del caso de
            # uso) ya lo garantiza. Un `assert` desaparece bajo `python -O`;
            # esto no depende del modo del intérprete, así que si la garantía
            # se rompiera alguna vez, la fila se descarta en vez de servirse.
            continue
        summaries.append(
            FindingSummary(
                id=finding.id,
                title=finding.title,
                published_at=finding.published_at,
                type=finding.type,
                level_curious=finding.level_curious,
            )
        )
    return FindingsPageResponse(
        items=summaries,
        page=page,
        size=size,
        total=result.total,
    )


@router.get("/findings/{finding_id}", response_model=FindingDetail)
def get_finding(
    finding_id: UUID,
    findings: _FindingsDep,
    items: _ItemsDep,
) -> FindingDetail:
    """Detalle de un hallazgo publicado.

    `404` con el mismo cuerpo exacto (`{"detail": "finding not found"}`)
    tanto si el id no existe como si existe pero no está publicado: no hay
    ninguna diferencia observable entre los dos casos, ni de cuerpo ni de
    cabecera. Distinguirlos confirmaría a un tercero la existencia de un
    candidato que el Editor rechazó, y permitiría enumerarlos probando
    ids uno a uno.
    """
    published = GetPublishedFinding(findings, items)(finding_id)
    if published is None:
        raise HTTPException(status_code=404, detail=_FINDING_NOT_FOUND)
    finding = published.finding
    if finding.published_at is None:
        # No debería ocurrir: `is_published` (segunda barrera del caso de
        # uso) ya lo garantiza. Un `assert` desaparece bajo `python -O`; esto
        # no depende del modo del intérprete, así que si la garantía se
        # rompiera alguna vez, el 404 es indistinguible del de un id
        # inexistente (mismo cuerpo, ver docstring de esta función).
        raise HTTPException(status_code=404, detail=_FINDING_NOT_FOUND)
    return FindingDetail(
        id=finding.id,
        title=finding.title,
        published_at=finding.published_at,
        type=finding.type,
        level_curious=finding.level_curious,
        level_amateur=finding.level_amateur,
        level_technical=finding.level_technical,
        source_url=source_url(published.source, published.external_id),
        catalog_tension=(
            None
            if finding.catalog_tension is None
            else CatalogTensionOut.from_domain(finding.catalog_tension)
        ),
        first_measurement=(
            None
            if finding.first_measurement is None
            else FirstMeasurementOut.from_domain(finding.first_measurement)
        ),
        independent_confirmation=(
            None
            if finding.independent_confirmation is None
            else IndependentConfirmationOut.from_domain(finding.independent_confirmation)
        ),
    )
