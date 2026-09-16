"""Puerto de ingesta desde fuentes externas.

Vive en `domain/` por el mismo motivo que `repositories.py`: sin esta
interfaz, `application/` tendría que importar `infrastructure/arxiv/`
directamente para orquestar una ingesta, rompiendo la regla de dependencias
solo hacia dentro. La interfaz habla exclusivamente en tipos de dominio
(`Item`, `datetime`); el DTO de Atom (`infrastructure/arxiv/atom.py`) no
cruza esta frontera.

Fase 1 solo tiene una fuente (arXiv), pero el nombre es específico
(`ArxivSource`, no `Source` genérico): no hay plan de una segunda fuente
todavía, así que no se generaliza por adelantado.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from nocturna.domain.entities import Item


@dataclass(frozen=True, slots=True)
class SourceFetch:
    """Resultado de una ingesta contra una fuente externa.

    `truncated` y `skipped` existen para que nada se pierda en silencio:
    quien orquesta la ingesta (T30/T44) puede registrar en `Run.notes` que
    la noche se cortó por el tope de resultados o que algunas entradas
    llegaron incompletas, en vez de que el conteo de ítems simplemente
    parezca bajo sin explicación.
    """

    items: list[Item]
    truncated: bool
    skipped: int


class ArxivSource(Protocol):
    """Fuente de ingesta de novedades de arXiv.

    Implementada estructuralmente por `infrastructure/arxiv/client.py`
    (`ArxivClient`), que no hereda de este `Protocol`: es infraestructura
    quien cumple el contrato del dominio, nunca al revés.
    """

    async def fetch_new(
        self, *, since: datetime, categories: Sequence[str], max_results: int
    ) -> SourceFetch:
        """Ítems nuevos publicados en `categories` desde `since`, hasta `max_results`."""
        ...
