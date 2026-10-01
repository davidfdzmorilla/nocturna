"""`ExoplanetCatalog` sobre el NASA Exoplanet Archive (T74, ADR 0012).

Cumple el `Protocol` de `domain/catalog.py` por estructura, sin heredar.
Todo es perezoso y cacheado por instancia: sin medidas utilizables no se
llama nunca a `resolve_planet` y, por tanto, no sale ninguna petición.
"""

from collections.abc import Mapping, Sequence

from nocturna.domain.catalog import CatalogSolution
from nocturna.domain.entities import MeasuredParameter
from nocturna.infrastructure.exoplanet_archive.client import (
    ArchiveHttpClient,
    ExoplanetArchiveUnavailable,
    adql_string,
)
from nocturna.infrastructure.exoplanet_archive.mappers import solutions_from_ps_rows
from nocturna.infrastructure.exoplanet_archive.names import clean_name, normalize_name

_INDEX_ADQL = "select pl_name from pscomppars"
_PS_COLUMNS = (
    "pl_name,default_flag,pl_refname,pl_bmassprov,"
    "pl_bmasse,pl_bmasseerr1,pl_bmasseerr2,pl_bmasselim,"
    "pl_rade,pl_radeerr1,pl_radeerr2,pl_radelim,"
    "pl_orbper,pl_orbpererr1,pl_orbpererr2,pl_orbperlim"
)


class ExoplanetArchiveCatalog:
    def __init__(self, client: ArchiveHttpClient) -> None:
        self._client = client
        self._index: dict[str, str] | None = None
        self._alias_cache: dict[str, str | None] = {}
        self._ps_rows: dict[str, list[dict[str, str | None]]] = {}

    async def _planet_index(self) -> dict[str, str]:
        if self._index is None:
            rows = await self._client.query_csv(_INDEX_ADQL)
            index = {normalize_name(name): name for row in rows if (name := row.get("pl_name"))}
            if not index:
                raise ExoplanetArchiveUnavailable(
                    "el índice de planetas del Exoplanet Archive llegó vacío"
                )
            self._index = index
        return self._index

    async def resolve_planet(self, name: str) -> str | None:
        norm = normalize_name(name)
        index = await self._planet_index()
        exact = index.get(norm)
        if exact is not None:
            return exact
        if norm not in self._alias_cache:
            payload = await self._client.lookup_alias(clean_name(name))
            self._alias_cache[norm] = _default_name_from_alias(payload, norm)
        default_name = self._alias_cache[norm]
        if default_name is None:
            return None
        return index.get(normalize_name(default_name))

    async def solutions(
        self, planet_name: str, parameter: MeasuredParameter
    ) -> Sequence[CatalogSolution]:
        rows = self._ps_rows.get(planet_name)
        if rows is None:
            rows = await self._client.query_csv(
                f"select {_PS_COLUMNS} from ps where pl_name={adql_string(planet_name)}"
            )
            self._ps_rows[planet_name] = rows
        return solutions_from_ps_rows(rows, parameter)


def _default_name_from_alias(payload: Mapping[str, object], norm_name: str) -> str | None:
    """`default_name` del planeta cuyo conjunto de alias contiene `norm_name`.

    Solo la rama `planet_set`: no se resuelven alias de estrella (un nombre
    de estrella no identifica un planeta concreto).
    """
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict) or manifest.get("lookup_status") != "OK":
        return None
    system = payload.get("system")
    objects = system.get("objects") if isinstance(system, dict) else None
    planet_set = objects.get("planet_set") if isinstance(objects, dict) else None
    planets = planet_set.get("planets") if isinstance(planet_set, dict) else None
    if not isinstance(planets, dict):
        return None
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
    return None
