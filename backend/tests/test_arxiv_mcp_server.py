"""Tests de `infrastructure/mcp/arxiv_server.py::create_arxiv_mcp_server`.

Nada de `query()` ni de `ClaudeSDKClient` aquí: ningún test de este fichero
habla con Claude. `create_sdk_mcp_server` (la función real del SDK, la que
envuelve las herramientas en un `mcp.server.Server`) se sustituye por un
doble que solo captura la lista de `SdkMcpTool` que recibe; el `@tool`
real sigue decorando `fetch_new`/`get_abstract` dentro de
`create_arxiv_mcp_server`, así que los objetos capturados son los mismos
`SdkMcpTool` de producción, con su `.handler` real invocable directamente
(sin pasar por el transporte MCP ni por Claude).
"""

from __future__ import annotations

import inspect
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any

import pytest

from nocturna.domain.entities import Item
from nocturna.infrastructure.arxiv.atom import ArxivEntry
from nocturna.infrastructure.mcp import arxiv_server
from nocturna.infrastructure.mcp.arxiv_server import SERVER_NAME, create_arxiv_mcp_server

_PUBLISHED_AT = datetime(2026, 9, 15, 17, 57, 6, tzinfo=UTC)


def _make_item(external_id: str, *, categories: Sequence[str] = ("astro-ph.EP",)) -> Item:
    return Item(
        source="arxiv",
        external_id=external_id,
        title=f"Título de {external_id}",
        abstract="Abstract que nunca debe llegar a fetch_new.",
        categories=list(categories),
        published_at=_PUBLISHED_AT,
        fetched_at=_PUBLISHED_AT,
    )


def _make_entry(arxiv_id: str) -> ArxivEntry:
    return ArxivEntry(
        arxiv_id=arxiv_id,
        version=1,
        title="NFTrig",
        abstract="Abstract completo de una única entrada.",
        categories=("cs.HC",),
        published_at=_PUBLISHED_AT,
        updated_at=_PUBLISHED_AT,
    )


class _FakeArxivClient:
    """Doble de `ArxivClient`: no abre red, devuelve lo que se le configure."""

    def __init__(
        self,
        *,
        fetch_items: list[Item] | None = None,
        fetch_truncated: bool = False,
        abstract_entry: ArxivEntry | None = None,
    ) -> None:
        self._fetch_items = fetch_items or []
        self._fetch_truncated = fetch_truncated
        self._abstract_entry = abstract_entry
        self.fetch_calls: list[dict[str, Any]] = []
        self.abstract_calls: list[str] = []

    async def fetch_new(self, *, since: datetime, categories: Sequence[str], max_results: int):
        self.fetch_calls.append(
            {"since": since, "categories": list(categories), "max_results": max_results}
        )
        from nocturna.domain.sources import SourceFetch

        return SourceFetch(items=self._fetch_items, truncated=self._fetch_truncated, skipped=0)

    async def get_abstract(self, arxiv_id: str) -> ArxivEntry | None:
        self.abstract_calls.append(arxiv_id)
        return self._abstract_entry


def _build_server(
    client: _FakeArxivClient,
    *,
    monkeypatch: pytest.MonkeyPatch,
    default_categories: Sequence[str] = ("astro-ph.EP", "astro-ph.GA"),
    max_results_cap: int = 400,
) -> dict[str, Any]:
    """Construye el servidor interceptando `create_sdk_mcp_server`.

    Devuelve un dict con `name` (el que se pasó a `create_sdk_mcp_server`) y
    `tools_by_name` (los `SdkMcpTool` reales, indexados por nombre), para
    poder invocar `.handler(args)` directamente.
    """
    captured: dict[str, Any] = {}

    def _fake_create_sdk_mcp_server(*, name: str, tools: list[Any]):
        captured["name"] = name
        captured["tools"] = tools
        return {"type": "sdk", "name": name, "instance": None}

    monkeypatch.setattr(arxiv_server, "create_sdk_mcp_server", _fake_create_sdk_mcp_server)

    create_arxiv_mcp_server(
        client, default_categories=default_categories, max_results_cap=max_results_cap
    )

    captured["tools_by_name"] = {tool.name: tool for tool in captured["tools"]}
    return captured


def _text_of(result: dict[str, Any]) -> str:
    return result["content"][0]["text"]


def _payload_of(result: dict[str, Any]) -> dict[str, Any]:
    return json.loads(_text_of(result))


def test_expone_exactamente_fetch_new_y_get_abstract_con_sus_esquemas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _build_server(_FakeArxivClient(), monkeypatch=monkeypatch)

    assert captured["name"] == SERVER_NAME
    assert set(captured["tools_by_name"]) == {"fetch_new", "get_abstract"}

    fetch_new = captured["tools_by_name"]["fetch_new"]
    assert fetch_new.input_schema == {"since": str, "categories": list[str], "max_results": int}

    get_abstract = captured["tools_by_name"]["get_abstract"]
    assert get_abstract.input_schema == {"arxiv_id": str}


@pytest.mark.anyio
async def test_fetch_new_devuelve_metadatos_del_cliente_fake_en_content_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeArxivClient(fetch_items=[_make_item("2609.17526"), _make_item("2609.17505")])
    tools = _build_server(client, monkeypatch=monkeypatch)["tools_by_name"]

    result = await tools["fetch_new"].handler(
        {"since": "2026-09-01", "categories": ["astro-ph.EP"], "max_results": 10}
    )

    assert "is_error" not in result
    payload = _payload_of(result)
    assert payload["count"] == 2
    assert payload["truncated"] is False
    assert [entry["arxiv_id"] for entry in payload["entries"]] == ["2609.17526", "2609.17505"]
    assert payload["entries"][0]["title"] == "Título de 2609.17526"
    assert payload["entries"][0]["published_at"] == _PUBLISHED_AT.isoformat()
    assert payload["entries"][0]["categories"] == ["astro-ph.EP"]

    assert client.fetch_calls == [
        {
            "since": datetime(2026, 9, 1, tzinfo=UTC),
            "categories": ["astro-ph.EP"],
            "max_results": 10,
        }
    ]


@pytest.mark.anyio
async def test_fetch_new_no_incluye_abstracts_en_la_salida(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Guarda de gasto: 400 abstracts completos en `fetch_new` serían del
    orden de 300 000 tokens de entrada (ver docstring del módulo). Si algún
    día alguien añade el campo `abstract` a la salida de `fetch_new`, este
    test tiene que ponerse rojo.
    """
    client = _FakeArxivClient(fetch_items=[_make_item("2609.17526")])
    tools = _build_server(client, monkeypatch=monkeypatch)["tools_by_name"]

    result = await tools["fetch_new"].handler(
        {"since": "2026-09-01", "categories": [], "max_results": 10}
    )

    payload = _payload_of(result)
    assert payload["entries"][0].keys() == {"arxiv_id", "title", "published_at", "categories"}
    assert "abstract" not in _text_of(result)


@pytest.mark.anyio
async def test_since_con_formato_invalido_devuelve_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tools = _build_server(_FakeArxivClient(), monkeypatch=monkeypatch)["tools_by_name"]

    result = await tools["fetch_new"].handler(
        {"since": "15-09-2026", "categories": [], "max_results": 10}
    )

    assert result["is_error"] is True
    assert "15-09-2026" in _text_of(result)


@pytest.mark.anyio
async def test_fetch_new_recorta_max_results_contra_max_results_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeArxivClient(fetch_items=[])
    tools = _build_server(client, monkeypatch=monkeypatch, max_results_cap=50)["tools_by_name"]

    await tools["fetch_new"].handler(
        {"since": "2026-09-01", "categories": ["astro-ph.EP"], "max_results": 1000}
    )

    assert client.fetch_calls[0]["max_results"] == 50


@pytest.mark.anyio
async def test_fetch_new_no_recorta_si_max_results_ya_esta_por_debajo_del_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeArxivClient(fetch_items=[])
    tools = _build_server(client, monkeypatch=monkeypatch, max_results_cap=50)["tools_by_name"]

    await tools["fetch_new"].handler(
        {"since": "2026-09-01", "categories": ["astro-ph.EP"], "max_results": 10}
    )

    assert client.fetch_calls[0]["max_results"] == 10


@pytest.mark.anyio
async def test_get_abstract_devuelve_una_sola_entrada_nunca_una_lista(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeArxivClient(abstract_entry=_make_entry("2301.00001"))
    tools = _build_server(client, monkeypatch=monkeypatch)["tools_by_name"]

    result = await tools["get_abstract"].handler({"arxiv_id": "2301.00001"})

    assert "is_error" not in result
    payload = _payload_of(result)
    assert isinstance(payload, dict)
    assert payload["found"] is True
    assert payload["arxiv_id"] == "2301.00001"
    assert payload["title"] == "NFTrig"
    assert payload["abstract"] == "Abstract completo de una única entrada."
    assert payload["categories"] == ["cs.HC"]
    assert client.abstract_calls == ["2301.00001"]


@pytest.mark.anyio
async def test_get_abstract_id_desconocido_devuelve_found_false_sin_is_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = _FakeArxivClient(abstract_entry=None)
    tools = _build_server(client, monkeypatch=monkeypatch)["tools_by_name"]

    result = await tools["get_abstract"].handler({"arxiv_id": "id_invalido_xyz"})

    assert "is_error" not in result
    assert _payload_of(result) == {"found": False}


def test_create_arxiv_mcp_server_no_admite_repositorio_ni_fabrica_de_sesion() -> None:
    """Imposibilidad estructural de persistir: si alguien añadiera un
    `ItemRepository` o una fábrica de sesión a esta firma, este test debe
    ponerse rojo (ver docstring de `arxiv_server.py`)."""
    signature = inspect.signature(create_arxiv_mcp_server)

    assert set(signature.parameters) == {"client", "default_categories", "max_results_cap"}
