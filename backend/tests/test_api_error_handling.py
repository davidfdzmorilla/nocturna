"""Congela el manejador de excepción genérico de `api/app.py` y `debug=False`
(T50, corrección de revisión, bloqueante 1).

El comportamiento de hoy es correcto -- ninguna traza, ningún mensaje de
driver SQL ni ninguna URL de conexión llega al cuerpo de una respuesta
`500` -- pero no había ningún test que lo congelara. Borrar
`@app.exception_handler(Exception)` de `api/app.py`, o cambiar `debug=False`
a `debug=True`, deja `uv run pytest` en verde mientras Starlette empieza a
devolver la traza completa en el cuerpo de la respuesta: exactamente el
escenario que este fichero cierra.

No usa base de datos ni la marca `db`: la excepción se fuerza
sobreescribiendo `get_findings_repository` con un doble que la lanza
directamente, sin pasar por `get_session` en absoluto. Sigue el mismo
mecanismo de `tests/db/test_api_findings.py` (`httpx.AsyncClient` +
`httpx.ASGITransport`), con `raise_app_exceptions=False`: sin ese parámetro,
`ASGITransport` relanza la excepción de la aplicación en el propio test en
vez de dejar que el manejador genérico la convierta en una respuesta -- lo
contrario de lo que un cliente real recibiría.
"""

import httpx
import pytest

from nocturna.api.app import create_app
from nocturna.api.deps import get_findings_repository

# Mensaje deliberadamente parecido a lo que un driver real filtraría: SQL,
# un nombre de tabla del proyecto y algo con forma de DSN.
_LEAKY_MESSAGE = (
    'psycopg.errors.UndefinedColumn: column "confidence" does not exist\n'
    "LINE 1: SELECT confidence FROM findings WHERE id = $1\n"
    "        ^\n"
    "dsn=postgresql://nocturna:s3cr3t@db-prod.internal:5432/nocturna"
)


class _BoomFindingRepository:
    """Doble de `FindingRepository` (`domain/repositories.py`) que lanza en
    cualquier método que `list_findings` pueda invocar."""

    def published_page(self, limit: int, offset: int) -> list[object]:
        raise RuntimeError(_LEAKY_MESSAGE)

    def count_published(self) -> int:
        raise RuntimeError(_LEAKY_MESSAGE)

    def get_published(self, finding_id: object) -> object:
        raise RuntimeError(_LEAKY_MESSAGE)

    def add(self, finding: object) -> None:
        raise RuntimeError(_LEAKY_MESSAGE)

    def unpublished_for_run(self, run_id: object) -> list[object]:
        raise RuntimeError(_LEAKY_MESSAGE)

    def save(self, finding: object) -> None:
        raise RuntimeError(_LEAKY_MESSAGE)


@pytest.mark.anyio
async def test_excepcion_no_gestionada_devuelve_500_generico_sin_filtrar_detalle() -> None:
    app = create_app()
    app.dependency_overrides[get_findings_repository] = lambda: _BoomFindingRepository()

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        response = await client.get("/findings")

    assert response.status_code == 500
    assert response.json() == {"detail": "internal error"}
    lowered = response.text.lower()
    for leaked_term in ("traceback", "select", "findings", "postgresql://"):
        assert leaked_term not in lowered, f"el cuerpo del 500 filtró detalle: '{leaked_term}'"


def test_create_app_no_activa_debug() -> None:
    # `debug=True` hace que Starlette devuelva la traza de una excepción no
    # gestionada en el cuerpo de la respuesta (ver docstring de
    # `api/app.py`): exactamente lo que el manejador genérico existe para
    # evitar. Si alguien lo activara, este test es el único que lo nota sin
    # tener que forzar una excepción de verdad.
    assert create_app().debug is False
