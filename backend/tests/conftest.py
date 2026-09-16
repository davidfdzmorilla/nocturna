"""Configuración compartida por toda la suite.

Dos responsabilidades:

- `anyio_backend`: fija el backend de `anyio` a `asyncio` para los tests
  async (`infrastructure/arxiv/` usa `anyio.sleep`). Sin esta fixture, el
  plugin de `anyio` parametriza sobre todos los backends disponibles.
- Guarda antirred: ningún test de esta suite puede salir a la red de
  verdad. Se parchean los dos transportes reales de `httpx` (`HTTPTransport`
  para clientes síncronos, `AsyncHTTPTransport` para asíncronos) para que
  cualquier intento de petición real explote. `httpx.MockTransport` (el que
  usan los tests de arXiv) y `httpx.ASGITransport` no heredan de estas
  clases, así que no se ven afectados: solo se bloquea el tráfico que de
  verdad saldría a Internet.
"""

import httpx
import pytest

_NETWORK_ERROR_MESSAGE = "test intentando salir a la red"


def _blocked_handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
    raise RuntimeError(_NETWORK_ERROR_MESSAGE)


async def _blocked_handle_async_request(
    self: httpx.AsyncHTTPTransport, request: httpx.Request
) -> httpx.Response:
    raise RuntimeError(_NETWORK_ERROR_MESSAGE)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        httpx.HTTPTransport, "handle_request", _blocked_handle_request, raising=True
    )
    monkeypatch.setattr(
        httpx.AsyncHTTPTransport,
        "handle_async_request",
        _blocked_handle_async_request,
        raising=True,
    )
