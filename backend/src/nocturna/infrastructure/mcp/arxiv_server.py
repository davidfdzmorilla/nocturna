"""Servidor MCP in-process que expone arXiv como herramientas del pipeline.

Único fichero de T20 que importa `claude_agent_sdk` (skill `agent-sdk-usage`).
Las dos herramientas (`@tool`, `create_sdk_mcp_server`) son lo que un agente
del orquestador (Reader, en T40+) invoca dentro de su `query()`; este módulo
no llama a `query()` ni construye ningún agente, solo declara el servidor.

**Gasto de tokens, no solo de HTTP.** El motivo de que existan dos
herramientas separadas, y no una sola que devuelva abstracts, es que un
`fetch_new` con 400 abstracts completos de vuelta serían del orden de
300 000 tokens de entrada para el modelo que lo invoque: el presupuesto
(`nightly_tokens`) de la noche entera en una sola llamada de herramienta.
`fetch_new` devuelve solo metadatos; `get_abstract` trae el abstract de un
único ítem cuando el agente decide que lo necesita.

**Imposibilidad estructural de persistir, no disciplina.**
`create_arxiv_mcp_server` no recibe ningún `ItemRepository` ni fábrica de
sesión, y ninguna herramienta escribe en base de datos: con esta firma no
pueden hacerlo. Si una herramienta persistiera, sería el modelo quien decide
cuándo se crean `Item`, cuántas veces y bajo qué transacción, lo que rompe de
frente la frontera transaccional única de `unit_of_work` (ADR 0003). Quien
persiste es `application.use_cases.ingest_arxiv.IngestArxiv`, invocado por el
CLI dentro de una unidad de trabajo; este servidor solo habla con
`ArxivClient` y con la red.
"""

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

from claude_agent_sdk import McpSdkServerConfig, create_sdk_mcp_server, tool

from nocturna.infrastructure.arxiv.client import ArxivClient

SERVER_NAME = "arxiv-astro"

_SINCE_FORMAT = "%Y-%m-%d"


def _parse_since(raw: str) -> datetime:
    """`since` viaja como cadena ISO `YYYY-MM-DD`: JSON Schema no tiene tipo
    fecha, así que el handler hace la conversión a `datetime` aware UTC."""
    parsed = datetime.strptime(raw, _SINCE_FORMAT)
    return parsed.replace(tzinfo=UTC)


def _error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "is_error": True}


def _json_result(payload: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": json.dumps(payload)}]}


def create_arxiv_mcp_server(
    client: ArxivClient,
    *,
    default_categories: Sequence[str],
    max_results_cap: int,
) -> McpSdkServerConfig:
    """Construye el servidor MCP in-process con las herramientas de arXiv.

    `default_categories` y `max_results_cap` llegan por argumento, leídos de
    `config/pipeline.toml` por quien construye el servidor (el CLI): este
    módulo no lee configuración global por su cuenta.
    """

    @tool(
        "fetch_new",
        "Lista metadatos (sin abstract) de papers nuevos de arXiv desde una fecha, "
        "por categoría. Usa get_abstract para leer el abstract de una entrada concreta.",
        {"since": str, "categories": list[str], "max_results": int},
    )
    async def fetch_new(args: dict[str, Any]) -> dict[str, Any]:
        try:
            since = _parse_since(args["since"])
        except ValueError:
            return _error(
                f"'since' debe ser una fecha ISO 'YYYY-MM-DD', recibido {args['since']!r}"
            )

        categories = args["categories"] or list(default_categories)
        max_results = min(args["max_results"], max_results_cap)

        fetch = await client.fetch_new(since=since, categories=categories, max_results=max_results)

        entries = [
            {
                "arxiv_id": item.external_id,
                "title": item.title,
                "published_at": item.published_at.isoformat(),
                "categories": item.categories,
            }
            for item in fetch.items
        ]
        return _json_result(
            {"count": len(entries), "truncated": fetch.truncated, "entries": entries}
        )

    @tool(
        "get_abstract",
        "Devuelve título, abstract y metadatos de un único paper de arXiv por su id "
        "(sin versión: siempre la última).",
        {"arxiv_id": str},
    )
    async def get_abstract(args: dict[str, Any]) -> dict[str, Any]:
        entry = await client.get_abstract(args["arxiv_id"])
        if entry is None:
            # No encontrarlo no es un fallo de la herramienta: `is_error` se
            # reserva para fallos de verdad (arXiv caído, id ilegible).
            return _json_result({"found": False})
        return _json_result(
            {
                "found": True,
                "arxiv_id": entry.arxiv_id,
                "title": entry.title,
                "abstract": entry.abstract,
                "categories": list(entry.categories),
                "published_at": entry.published_at.isoformat(),
            }
        )

    return create_sdk_mcp_server(name=SERVER_NAME, tools=[fetch_new, get_abstract])
