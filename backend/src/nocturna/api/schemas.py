"""Esquemas Pydantic de respuesta de la API de lectura (T50 paso 3).

Lo que **no** sale en ningún esquema, deliberadamente, es tan importante
como lo que sale:

- `confidence`: nota editorial interna del Editor. Junto a un texto
  generado por IA se leería como "grado de certeza científica", justo lo
  contrario del banner obligatorio de `CLAUDE.md` ("Análisis generado
  automáticamente por IA. No es un resultado científico validado.").
- `run_id`: telemetría del pipeline. Revela la cadencia y el tamaño de las
  noches del autor.
- `item_id`: el identificador público de un hallazgo es `finding.id`; no
  hay ninguna vista pública de `Item` en fase 1.

Ningún esquema envuelve un `Reading` ni un `Run`: ninguno de los dos se
publica nunca.
"""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


def source_url(source: str, external_id: str) -> str | None:
    """URL pública del ítem original, o `None` si la fuente no se conoce.

    Fase 1 solo ingesta arXiv (`CLAUDE.md`); cualquier otra fuente futura
    que no sepamos enlazar debe degradar a `None` en vez de construir una
    URL inventada.
    """
    if source == "arxiv":
        return f"https://arxiv.org/abs/{external_id}"
    return None


class FindingSummary(BaseModel):
    """Entrada del listado paginado (`GET /findings`). Sin `source_url`:
    el listado no hace join con `Item`, así se evita el N+1."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    published_at: datetime
    level_curious: str


class FindingDetail(BaseModel):
    """Detalle de un hallazgo publicado (`GET /findings/{finding_id}`)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    published_at: datetime
    type: str
    level_curious: str
    level_amateur: str
    level_technical: str
    source_url: str | None


class FindingsPageResponse(BaseModel):
    """Página de hallazgos publicados."""

    model_config = ConfigDict(frozen=True)

    items: list[FindingSummary]
    page: int
    size: int
    total: int


class HealthResponse(BaseModel):
    """Estado del proceso y de la base de datos (`GET /health`)."""

    model_config = ConfigDict(frozen=True)

    status: str
    database: str
