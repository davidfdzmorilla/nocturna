"""`ExoplanetCatalog` sobre el snapshot local del NASA Exoplanet Archive (T88, ADR 0012).

Cumple el `Protocol` de `domain/catalog.py` por estructura, sin heredar.
Las soluciones y los nombres de planeta salen de la base (`ArchiveRepository`,
tablas del snapshot de T81); la red solo se usa para el servicio de alias,
cuando un nombre no está en el índice local. Todo es perezoso y cacheado por
instancia: sin medidas utilizables no se llama nunca a `resolve_planet` y, por
tanto, no sale ninguna petición ni se lee el índice.
"""

from collections.abc import Mapping, Sequence

from nocturna.domain.archive import catalog_solution_from_archive
from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.entities import MeasuredParameter
from nocturna.domain.errors import PlanetResolutionFailed
from nocturna.domain.repositories import ArchiveRepository
from nocturna.infrastructure.exoplanet_archive.client import (
    ArchiveHttpClient,
    ExoplanetArchiveUnavailable,
)
from nocturna.infrastructure.exoplanet_archive.names import clean_name, normalize_name


class ExoplanetArchiveCatalog:
    def __init__(self, archive: ArchiveRepository, alias_client: ArchiveHttpClient) -> None:
        self._archive = archive
        self._alias_client = alias_client
        self._index: dict[str, str] | None = None
        self._alias_cache: dict[str, str | None] = {}

    def _planet_index(self) -> dict[str, str]:
        if self._index is None:
            index = {normalize_name(name): name for name in self._archive.planet_names()}
            if not index:
                raise ExoplanetArchiveUnavailable(
                    "el índice local de planetas del Exoplanet Archive está vacío "
                    "(¿falta ejecutar archive-snapshot?)"
                )
            self._index = index
        return self._index

    async def resolve_planet(self, name: str) -> str | None:
        norm = normalize_name(name)
        index = self._planet_index()
        exact = index.get(norm)
        if exact is not None:
            return exact
        if norm not in self._alias_cache:
            payload = await self._alias_client.lookup_alias(clean_name(name))
            # D16: un fallo de resolución lanza antes de tocar la caché; solo
            # "System Not Found" queda cacheado como ausente.
            self._alias_cache[norm] = _default_name_from_alias(payload, norm)
        default_name = self._alias_cache[norm]
        if default_name is None:
            return None
        return index.get(normalize_name(default_name))

    async def solutions(
        self, planet_name: str, parameter: MeasuredParameter
    ) -> Sequence[CatalogSolution]:
        out: list[CatalogSolution] = []
        for sol, is_default_current in self._archive.active_solutions(planet_name):
            converted = catalog_solution_from_archive(sol, parameter, is_default=is_default_current)
            if converted is not None:
                out.append(converted)
        return out


SYSTEM_NOT_FOUND = "system not found"


def _normalized_status(status: object) -> str:
    return " ".join(status.split()).lower() if isinstance(status, str) else ""


def _default_name_from_alias(payload: Mapping[str, object], norm_name: str) -> str | None:
    """`default_name` del planeta cuyo conjunto de alias contiene `norm_name`.

    Solo la rama `planet_set`: no se resuelven alias de estrella (un nombre
    de estrella no identifica un planeta concreto).

    D16 (T89): `None` (planeta ausente del archivo) solo sale de
    `lookup_status == "System Not Found"` (sin distinguir mayúsculas ni
    espacios). Cualquier otro estado, un manifiesto ilegible, o `OK` sin el
    nombre en `planet_set`, lanzan `PlanetResolutionFailed`.
    """
    manifest = payload.get("manifest")
    status = manifest.get("lookup_status") if isinstance(manifest, dict) else None
    normalized = _normalized_status(status)
    if normalized == SYSTEM_NOT_FOUND:
        return None
    if normalized != "ok":
        raise PlanetResolutionFailed(
            f"el servicio de alias respondió lookup_status {status!r} para {norm_name!r}"
        )
    system = payload.get("system")
    objects = system.get("objects") if isinstance(system, dict) else None
    planet_set = objects.get("planet_set") if isinstance(objects, dict) else None
    planets = planet_set.get("planets") if isinstance(planet_set, dict) else None
    if isinstance(planets, dict):
        for planet_info in planets.values():
            alias_set = planet_info.get("alias_set") if isinstance(planet_info, dict) else None
            if not isinstance(alias_set, dict):
                continue
            aliases = alias_set.get("aliases")
            default_name = alias_set.get("default_name")
            if (
                isinstance(aliases, list)
                and isinstance(default_name, str)
                and any(isinstance(a, str) and normalize_name(a) == norm_name for a in aliases)
            ):
                return default_name
    raise PlanetResolutionFailed(
        f"el servicio de alias respondió OK pero {norm_name!r} no figura en 'planet_set'"
    )
