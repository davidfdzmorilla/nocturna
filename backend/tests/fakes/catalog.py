"""`FakeExoplanetCatalog`: `ExoplanetCatalog` en memoria para tests (T73).

Nada de red ni de NASA Exoplanet Archive. Registra cada llamada recibida
para poder afirmar que, por ejemplo, un `UNMATCHED` no consulta `solutions`.
"""

from collections.abc import Mapping, Sequence

from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.entities import MeasuredParameter
from nocturna.domain.errors import PlanetResolutionFailed


class FakeExoplanetCatalog:
    def __init__(
        self,
        aliases: Mapping[str, str] | None = None,
        solutions: Mapping[tuple[str, MeasuredParameter], Sequence[CatalogSolution]] | None = None,
        failing: set[str] | None = None,
    ) -> None:
        # D16: nombres cuya resolución falla (`PlanetResolutionFailed`); mutable.
        self.failing: set[str] = set(failing or ())
        self._aliases = dict(aliases or {})
        self._solutions = {key: tuple(value) for key, value in (solutions or {}).items()}
        self.resolve_calls: list[str] = []
        self.solutions_calls: list[tuple[str, MeasuredParameter]] = []

    async def resolve_planet(self, name: str) -> str | None:
        self.resolve_calls.append(name)
        if name in self.failing:
            raise PlanetResolutionFailed(f"alias anómalo para {name!r}")
        return self._aliases.get(name)

    async def solutions(
        self, planet_name: str, parameter: MeasuredParameter
    ) -> Sequence[CatalogSolution]:
        self.solutions_calls.append((planet_name, parameter))
        return self._solutions.get((planet_name, parameter), ())

    @property
    def total_calls(self) -> int:
        return len(self.resolve_calls) + len(self.solutions_calls)
