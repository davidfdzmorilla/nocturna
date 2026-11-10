"""`GET /data-status`: fecha de los datos más recientes (T108).

Solo lectura: último Run terminado o último snapshot del archivo. La web la muestra
para que se vea hasta cuándo llegan los datos publicados.
"""

from typing import Annotated

from fastapi import APIRouter, Depends

from nocturna.api.deps import get_data_freshness_reader
from nocturna.api.schemas import DataStatusResponse
from nocturna.domain.repositories import DataFreshnessReader

router = APIRouter()


@router.get("/data-status", response_model=DataStatusResponse)
def data_status(
    reader: Annotated[DataFreshnessReader, Depends(get_data_freshness_reader)],
) -> DataStatusResponse:
    return DataStatusResponse(data_updated_at=reader.last_update())
