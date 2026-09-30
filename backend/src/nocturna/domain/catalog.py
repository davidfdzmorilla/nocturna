"""Puerto del catálogo de exoplanetas y su solución publicada (T73).

Vive en `domain/` por el mismo motivo que `sources.py`: `application/`
(`ComputeTensions`) necesita hablar con el NASA Exoplanet Archive sin
importar `infrastructure/`. El puerto habla solo en tipos de dominio; el
adaptador HTTP (ADR 0012) cumple estructuralmente el contrato, sin heredar.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from nocturna.domain.entities import (
    UNITS_BY_PARAMETER,
    MeasuredParameter,
    MeasurementLimit,
    MeasurementUnit,
)
from nocturna.domain.errors import InvariantViolation


@dataclass(frozen=True, slots=True)
class CatalogSolution:
    """Una solución publicada de un parámetro de un planeta en el catálogo.

    Es la "previa" frente a la que se compara la medida de un paper.
    `is_default` marca la solución que el archivo declara por defecto para
    el planeta (referencia de `TensionResult.is_candidate`, OPEN_DECISIONS
    T73, 2026-09-30). `arxiv_id` identifica el paper de origen, si el
    archivo lo conoce, para que el caso de uso excluya la solución del
    propio paper que se está analizando. Contrato para el adaptador (T74):
    identificador arXiv SIN versión y con el mismo formato que
    `Item.external_id` (p. ej. `2609.30038`, nunca `2609.30038v2` ni
    `arXiv:2609.30038`), porque la exclusión compara por igualdad exacta
    de cadenas. `None` si el adaptador no lo reconoce: entonces el paper se
    compara consigo mismo y sale σ ≈ 0 (fallo hacia el lado seguro).
    """

    planet_name: str
    parameter: MeasuredParameter
    value: float
    err_plus: float | None
    err_minus: float | None
    unit: MeasurementUnit
    limit: MeasurementLimit
    reference: str
    is_default: bool
    arxiv_id: str | None

    def __post_init__(self) -> None:
        if not self.planet_name.strip():
            raise InvariantViolation("'planet_name' no puede estar vacío")
        if not self.reference.strip():
            raise InvariantViolation("'reference' no puede estar vacío")
        if not math.isfinite(self.value):
            raise InvariantViolation("'value' debe ser un número finito")
        if self.value <= 0:
            raise InvariantViolation("'value' debe ser mayor que cero")
        for name in ("err_plus", "err_minus"):
            err = getattr(self, name)
            if err is None:
                continue
            if not math.isfinite(err):
                raise InvariantViolation(f"'{name}' debe ser un número finito")
            if err < 0:
                raise InvariantViolation(f"'{name}' no puede ser negativo")
        if self.unit not in UNITS_BY_PARAMETER[self.parameter]:
            raise InvariantViolation(
                f"'unit' {self.unit.value!r} no es coherente con "
                f"'parameter' {self.parameter.value!r}"
            )

    @property
    def usable_as_prior(self) -> bool:
        """Utilizable como previa: valor puntual (no cota) y con los dos
        errores informados y estrictamente positivos (un error cero haría
        degenerar el denominador de σ)."""
        return (
            self.limit == MeasurementLimit.NONE
            and self.err_plus is not None
            and self.err_plus > 0
            and self.err_minus is not None
            and self.err_minus > 0
        )


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
