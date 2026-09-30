"""Puerto del catálogo de exoplanetas y su solución publicada (T73).

Vive en `domain/` por el mismo motivo que `sources.py`: `application/`
(`ComputeTensions`) necesita hablar con el NASA Exoplanet Archive sin
importar `infrastructure/`. El puerto habla solo en tipos de dominio; el
adaptador HTTP (ADR 0012) cumple estructuralmente el contrato, sin heredar.
"""

from collections.abc import Sequence
from typing import Protocol

from nocturna.domain.entities import CatalogSolution, MeasuredParameter

__all__ = ["CatalogSolution", "ExoplanetCatalog"]


class ExoplanetCatalog(Protocol):
    """Catálogo de exoplanetas consultable por nombre de planeta."""

    async def resolve_planet(self, name: str) -> str | None:
        """Nombre canónico del planeta para `name` (alias incluidos), o
        `None` si el catálogo no lo reconoce."""
        ...

    async def solutions(
        self, planet_name: str, parameter: MeasuredParameter
    ) -> Sequence[CatalogSolution]:
        """Todas las soluciones publicadas de `parameter` para el planeta
        canónico `planet_name`, incluida la del propio paper analizado.

        No filtra: excluir la solución propia y las no utilizables como
        previa es responsabilidad del dominio/caso de uso. Toda solución
        devuelta debe ser de `parameter`; devolver otra es una violación de
        contrato y hace fallar el cálculo (`InvariantViolation` en
        `compare`).
        """
        ...
