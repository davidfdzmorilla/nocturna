"""La guarda antirred de `conftest.py` es infraestructura de la propia suite:
si se rompe en silencio, cualquier test podría empezar a salir a Internet
sin que nadie se entere. Estos tests existen solo para eso: confirmar que un
`httpx.Client`/`httpx.AsyncClient` reales (sin `MockTransport`) explotan al
intentar una petición real.
"""

import httpx
import pytest


@pytest.mark.anyio
async def test_cliente_async_real_no_puede_salir_a_la_red():
    async with httpx.AsyncClient() as client:
        with pytest.raises(RuntimeError, match="test intentando salir a la red"):
            await client.get("https://export.arxiv.org/api/query")


def test_cliente_sync_real_no_puede_salir_a_la_red():
    with httpx.Client() as client:
        with pytest.raises(RuntimeError, match="test intentando salir a la red"):
            client.get("https://export.arxiv.org/api/query")
