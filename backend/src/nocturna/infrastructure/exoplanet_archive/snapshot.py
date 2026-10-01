"""Fuente de soluciones del NASA Exoplanet Archive para el snapshot (T81).

Cumple `domain.archive.ArchiveSolutionSource` por estructura. Cuatro
consultas sobre la tabla `ps`, todas con las mismas 29 columnas (las del
paso 0 de T81; formatos observados en `tests/fixtures/exoplanet_archive/`).
"""

from collections.abc import Collection, Sequence
from datetime import date

from nocturna.domain.archive import ArchiveSolution
from nocturna.infrastructure.exoplanet_archive.client import ArchiveHttpClient, adql_string
from nocturna.infrastructure.exoplanet_archive.mappers import archive_solution_from_ps_row

SNAPSHOT_COLUMNS: tuple[str, ...] = (
    "pl_name",
    "hostname",
    "default_flag",
    "soltype",
    "pl_refname",
    "releasedate",
    "pl_pubdate",
    "pl_bmassprov",
    "pl_bmasse",
    "pl_bmasseerr1",
    "pl_bmasseerr2",
    "pl_bmasselim",
    "pl_rade",
    "pl_radeerr1",
    "pl_radeerr2",
    "pl_radelim",
    "pl_orbper",
    "pl_orbpererr1",
    "pl_orbpererr2",
    "pl_orbperlim",
    "st_rad",
    "st_raderr1",
    "st_raderr2",
    "st_mass",
    "st_masserr1",
    "st_masserr2",
    "discoverymethod",
    "ttv_flag",
    "pl_controv_flag",
)

_SELECT = "select " + ",".join(SNAPSHOT_COLUMNS) + " from ps"


class ArchiveSnapshotSource:
    def __init__(self, client: ArchiveHttpClient, *, planet_batch_size: int) -> None:
        if planet_batch_size < 1:
            raise ValueError("planet_batch_size debe ser >= 1")
        self._client = client
        self._batch = planet_batch_size

    @property
    def requests_made(self) -> int:
        return self._client.requests_made

    async def _query(self, where: str | None) -> list[ArchiveSolution]:
        adql = _SELECT if where is None else f"{_SELECT} where {where}"
        rows = await self._client.query_csv(adql)
        return [archive_solution_from_ps_row(row) for row in rows]

    async def all_solutions(self) -> Sequence[ArchiveSolution]:
        return await self._query(None)

    async def released_since(self, day: date) -> Sequence[ArchiveSolution]:
        return await self._query(f"releasedate >= '{day.isoformat()}'")

    async def default_solutions(self) -> Sequence[ArchiveSolution]:
        return await self._query("default_flag=1")

    async def solutions_for_planets(self, names: Collection[str]) -> Sequence[ArchiveSolution]:
        ordered = sorted(set(names))
        out: list[ArchiveSolution] = []
        for start in range(0, len(ordered), self._batch):
            batch = ordered[start : start + self._batch]
            in_list = ",".join(adql_string(n) for n in batch)
            out.extend(await self._query(f"pl_name in ({in_list})"))
        return out
