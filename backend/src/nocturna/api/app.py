"""Fábrica de la aplicación FastAPI de la API de lectura (T50 paso 3).

`create_app` monta CORS, registra los dos routers (`health`, `findings`) y
un manejador de excepción genérico. Es lo que arranca `uvicorn
nocturna.api.app:create_app --factory` (ver `pyproject.toml`, grupo `dev`).

**Restricción que gobierna este módulo** (`CLAUDE.md`, § Web fase 1 y §
restricción de suscripción): la API nunca llama a Claude. Este paquete no
importa `claude_agent_sdk`, `nocturna.infrastructure.llm`,
`nocturna.application.agents`, `nocturna.application.budget` ni
`nocturna.application.unit_of_work`.

`debug=False` (el valor por defecto de FastAPI, pero explícito aquí):
`debug=True` hace que Starlette devuelva la traza de una excepción no
gestionada en el cuerpo de la respuesta, exactamente lo que el manejador
genérico existe para evitar.
"""

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from nocturna.api.deps import get_settings
from nocturna.api.routes.findings import router as findings_router
from nocturna.api.routes.health import router as health_router
from nocturna.infrastructure.config import Settings
from nocturna.infrastructure.logging import configure_json_logging

logger = logging.getLogger(__name__)


def create_app(settings: Settings | None = None) -> FastAPI:
    """Construye la aplicación. `settings=None` usa `Settings()` cacheada."""
    configure_json_logging()
    resolved_settings = settings if settings is not None else get_settings()

    app = FastAPI(title="Nocturna API", debug=False)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved_settings.cors_origins,
        allow_methods=["GET"],
        allow_headers=[],
        allow_credentials=False,
    )

    app.include_router(health_router)
    app.include_router(findings_router)

    @app.exception_handler(Exception)
    async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        """Nunca deja llegar al cliente traza, mensaje de driver ni URL de conexión.

        El detalle real -- lo único útil para depurar -- va a `stderr` en
        JSON vía `logging` (formato de `infrastructure/logging.py`), nunca
        al cuerpo de la respuesta.
        """
        logger.error(
            "unhandled exception in %s %s",
            request.method,
            request.url.path,
            exc_info=exc,
            extra={"event": "unhandled_exception"},
        )
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app
