"""Fuente falsa de soluciones del archivo y constructores de soluciones (T81).

`FakeArchiveSource` cumple `ArchiveSolutionSource` sin HTTP: sirve una lista
en memoria, cuenta peticiones como el cliente real (una por consulta; una por
lote en `solutions_for_planets`) y registra cada llamada en `calls`.
"""

from __future__ import annotations

import math
from collections.abc import Collection, Sequence
from datetime import date

from nocturna.domain.archive import ArchiveSolution
from nocturna.infrastructure.exoplanet_archive.client import ExoplanetArchiveUnavailable
from nocturna.infrastructure.exoplanet_archive.mappers import archive_solution_from_ps_row
from nocturna.infrastructure.exoplanet_archive.snapshot import SNAPSHOT_COLUMNS


def make_solution(
    pl_name: str,
    ref: str = "Smith 2020",
    *,
    default: bool = False,
    release: str = "2026-08-01",
    mass: float = 1.0,
) -> ArchiveSolution:
    """Solución sintética. Dos soluciones del mismo planeta con distinta `ref`
    (o `mass`) tienen distinta `solution_key`; `default` y `release` no entran
    en la clave."""
    row: dict[str, str | None] = dict.fromkeys(SNAPSHOT_COLUMNS)
    row.update(
        pl_name=pl_name,
        hostname=pl_name.rsplit(" ", 1)[0],
        default_flag="1" if default else "0",
        soltype="Published Confirmed",
        pl_refname=ref,
        releasedate=release,
        pl_pubdate="2020-01",
        pl_bmasse=str(mass),
        pl_bmasseerr1="0.1",
        pl_bmasseerr2="-0.1",
        pl_bmasselim="0",
        discoverymethod="Transit",
        ttv_flag="0",
        pl_controv_flag="0",
    )
    return archive_solution_from_ps_row(row)


def make_catalog(planets: int = 10) -> list[ArchiveSolution]:
    """`planets` planetas `P00 b`, `P01 b`...; cada uno con una solución por
    defecto (`Smith 2020`) y otra no (`Jones 2021`). Todas con releasedate
    anterior a 2026-09-01."""
    out: list[ArchiveSolution] = []
    for i in range(planets):
        name = f"P{i:02d} b"
        out.append(make_solution(name, "Smith 2020", default=True))
        out.append(make_solution(name, "Jones 2021", release="2026-07-15"))
    return out


class FakeArchiveSource:
    def __init__(
        self,
        catalog: Sequence[ArchiveSolution],
        *,
        planet_batch_size: int = 75,
        fail_on: str | None = None,
    ) -> None:
        self.catalog = list(catalog)
        self._batch = planet_batch_size
        self._fail_on = fail_on
        self._requests = 0
        self.calls: list[tuple] = []

    @property
    def requests_made(self) -> int:
        return self._requests

    def _tick(self, name: str, *args: object, requests: int = 1) -> None:
        self.calls.append((name, *args))
        self._requests += requests
        if self._fail_on == name:
            raise ExoplanetArchiveUnavailable(f"fallo simulado en {name}")

    async def all_solutions(self) -> Sequence[ArchiveSolution]:
        self._tick("all")
        return list(self.catalog)

    async def released_since(self, day: date) -> Sequence[ArchiveSolution]:
        self._tick("released_since", day)
        return [s for s in self.catalog if s.releasedate >= day]

    async def default_solutions(self) -> Sequence[ArchiveSolution]:
        self._tick("default_solutions")
        return [s for s in self.catalog if s.is_default]

    async def solutions_for_planets(self, names: Collection[str]) -> Sequence[ArchiveSolution]:
        ordered = sorted(set(names))
        self._tick(
            "solutions_for_planets",
            tuple(ordered),
            requests=math.ceil(len(ordered) / self._batch),
        )
        return [s for s in self.catalog if s.pl_name in set(ordered)]
