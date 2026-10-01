"""Utilidades de test para el adaptador del NASA Exoplanet Archive (T74).

Todo sin red: el cliente se construye sobre `httpx.MockTransport` y el
`RateLimiter` recibe `sleep`/`monotonic` falsos, así que no hay esperas
reales. Los fixtures son respuestas reales grabadas
(`tests/fixtures/exoplanet_archive/`).
"""

import csv
import io
import json
from collections.abc import Callable
from pathlib import Path
from urllib.parse import parse_qs

import httpx

from nocturna.domain.entities import (
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementOrigin,
    MeasurementUnit,
)
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.exoplanet_archive.catalog import ExoplanetArchiveCatalog
from nocturna.infrastructure.exoplanet_archive.client import ArchiveHttpClient

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "exoplanet_archive"
T71C_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "t71c"

TAP_URL = "https://archive.test/TAP/sync"
ALIAS_URL = "https://archive.test/cgi-bin/nph-aliaslookup.py"


def fixture_text(name: str) -> str:
    return (FIXTURES_DIR / name).read_text(encoding="utf-8")


def fixture_rows(name: str) -> list[dict[str, str | None]]:
    """Filas de un CSV de fixture, con "" como `None` (igual que el cliente)."""
    reader = csv.DictReader(io.StringIO(fixture_text(name)))
    return [{k: (v if v != "" else None) for k, v in row.items()} for row in reader]


class FakeSleep:
    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)


_OPEN_HTTP_CLIENTS: list[httpx.AsyncClient] = []


async def close_open_clients() -> None:
    """Cierra los `httpx.AsyncClient` creados por `make_client` (lo llama la
    fixture autouse de `conftest.py`)."""
    while _OPEN_HTTP_CLIENTS:
        await _OPEN_HTTP_CLIENTS.pop().aclose()


def make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_requests: int = 40,
    interval_s: float = 2.0,
    sleep: FakeSleep | None = None,
    request_timeout_s: float = 30.0,
) -> tuple[ArchiveHttpClient, list[httpx.Request]]:
    """Cliente sobre MockTransport; devuelve también las peticiones que SALEN."""
    seen: list[httpx.Request] = []

    def recording(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    http = httpx.AsyncClient(transport=httpx.MockTransport(recording))
    _OPEN_HTTP_CLIENTS.append(http)
    limiter = RateLimiter(
        interval_s,
        sleep=sleep or FakeSleep(),
        monotonic=lambda: 0.0,
    )
    client = ArchiveHttpClient(
        http,
        tap_url=TAP_URL,
        alias_url=ALIAS_URL,
        limiter=limiter,
        request_timeout_s=request_timeout_s,
        max_requests=max_requests,
    )
    return client, seen


def adql_of(request: httpx.Request) -> str:
    return parse_qs(request.url.query.decode())["query"][0]


def archive_handler(*, alias_body: str | None = None) -> Callable[[httpx.Request], httpx.Response]:
    """Servidor falso con los fixtures reales.

    - índice (`select pl_name from pscomppars`): `pscomppars_names_full.csv`
    - `... from ps where pl_name='X'`: filas de `ps_v1298tau.csv` de ese planeta
    - alias: `alias_body` (por defecto `aliaslookup_wasp12.json`)
    """
    ps_rows = fixture_rows("ps_v1298tau.csv")
    header = fixture_text("ps_v1298tau.csv").splitlines()[0].split(",")

    def handler(request: httpx.Request) -> httpx.Response:
        if "aliaslookup" in request.url.path:
            return httpx.Response(200, text=alias_body or fixture_text("aliaslookup_wasp12.json"))
        adql = adql_of(request)
        if adql == "select pl_name from pscomppars":
            return httpx.Response(200, text=fixture_text("pscomppars_names_full.csv"))
        marker = "where pl_name='"
        assert marker in adql, adql
        name = adql.split(marker, 1)[1].rstrip("'").replace("''", "'")
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        for row in ps_rows:
            if row["pl_name"] == name:
                writer.writerow({k: (v or "") for k, v in row.items()})
        return httpx.Response(200, text=out.getvalue())

    return handler


def make_catalog(
    handler: Callable[[httpx.Request], httpx.Response] | None = None,
    **kwargs: object,
) -> tuple[ExoplanetArchiveCatalog, ArchiveHttpClient, list[httpx.Request]]:
    client, seen = make_client(handler or archive_handler(), **kwargs)  # type: ignore[arg-type]
    return ExoplanetArchiveCatalog(client), client, seen


def load_t71c_measurements(filename: str) -> tuple[Measurement, ...]:
    """`Measurement` de dominio desde un JSON real de `tests/fixtures/t71c/`."""
    payload = json.loads((T71C_DIR / filename).read_text(encoding="utf-8"))
    return tuple(
        Measurement(
            planet_name=raw["planet_name"],
            parameter=MeasuredParameter(raw["parameter"]),
            value=raw["value"],
            err_plus=raw["err_plus"],
            err_minus=raw["err_minus"],
            unit=MeasurementUnit(raw["unit"]),
            limit=MeasurementLimit(raw["limit"]),
            origin=MeasurementOrigin(raw["origin"]),
            evidence=raw["evidence"],
        )
        for raw in payload["measurements"]
    )


def snapshot_handler(
    rows: list[dict[str, str | None]] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    """Servidor falso de las cuatro consultas del snapshot (T81) sobre `ps`.

    Reconoce `select <cols> from ps` (todo), `... where releasedate >= 'D'`,
    `... where default_flag=1` y `... where pl_name in ('a','b')`. Por defecto
    sirve las filas de `ps_t81_planets.csv` y `ps_t81_released_since_7d.csv`.
    """
    header = fixture_text("ps_t81_planets.csv").splitlines()[0].split(",")
    data = (
        rows
        if rows is not None
        else fixture_rows("ps_t81_planets.csv") + fixture_rows("ps_t81_released_since_7d.csv")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        adql = adql_of(request)
        assert adql.startswith("select pl_name,hostname,default_flag") and " from ps" in adql, adql
        _, _, where = adql.partition(" where ")
        if not where:
            chosen = data
        elif where.startswith("releasedate >= '"):
            day = where.split("'")[1]
            chosen = [r for r in data if (r["releasedate"] or "") >= day]
        elif where == "default_flag=1":
            chosen = [r for r in data if r["default_flag"] == "1"]
        else:
            assert where.startswith("pl_name in ("), where
            inner = where[len("pl_name in (") : -1]
            names = {part.replace("''", "'") for part in inner[1:-1].split("','")}
            chosen = [r for r in data if r["pl_name"] in names]
        out = io.StringIO()
        writer = csv.DictWriter(out, fieldnames=header, lineterminator="\n")
        writer.writeheader()
        for row in chosen:
            writer.writerow({k: (v or "") for k, v in row.items()})
        return httpx.Response(200, text=out.getvalue())

    return handler
