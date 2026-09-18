"""`GET /health`: proceso vivo y base de datos alcanzable (T50 paso 3).

Reutiliza la misma sesión de lectura (`api.deps.get_session`) para el
`SELECT 1`: no abre una conexión propia. Es la única ruta que necesita
distinguir "la base de datos no responde" de un fallo genérico, así que es
también la única que captura `OperationalError` en vez de dejarlo llegar
al manejador genérico de `api/app.py`.
"""

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from nocturna.api.deps import get_session
from nocturna.api.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
def health(
    session: Annotated[Session, Depends(get_session)],
) -> HealthResponse | JSONResponse:
    """`200` si la base de datos responde, `503` con cadena fija si no.

    Nunca propaga la excepción original de `psycopg`/SQLAlchemy al cuerpo
    de la respuesta: ni traza, ni mensaje de driver, ni nombre de tabla.
    """
    try:
        session.execute(text("SELECT 1"))
    except OperationalError:
        return JSONResponse(
            status_code=503,
            content={"status": "degraded", "database": "unreachable"},
        )
    return HealthResponse(status="ok", database="ok")
