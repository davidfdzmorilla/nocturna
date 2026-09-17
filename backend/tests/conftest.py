"""Configuración compartida por toda la suite.

Tres responsabilidades:

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
- Guarda anti-Claude: ningún test de esta suite puede gastar suscripción
  de verdad. A diferencia de `httpx`, el Claude Agent SDK no habla por
  `httpx` dentro de nuestro proceso: tanto `query()` como `ClaudeSDKClient`
  lanzan el CLI `claude` como subproceso, así que la guarda antirred de
  arriba no lo cubre. Se parchea el punto real por el que pasan las dos
  APIs públicas -- `SubprocessCLITransport.connect`, ver docstring de
  `_no_claude` -- para que cualquier llamada real explote, salvo que el
  test esté marcado `@pytest.mark.manual` (el único camino legítimo para
  gastar suscripción; ver `pyproject.toml`, marcador `manual`).
"""

import httpx
import pytest

_NETWORK_ERROR_MESSAGE = "test intentando salir a la red"
_CLAUDE_ERROR_MESSAGE = (
    "test intentando llamar a Claude: usa FakeLLMProvider, o marca el test con @pytest.mark.manual"
)
_MOVED_TRANSPORT_ERROR_MESSAGE = (
    "la guarda anti-Claude no encuentra "
    "claude_agent_sdk._internal.transport.subprocess_cli.SubprocessCLITransport.connect: "
    "una actualización del paquete 'claude-agent-sdk' ha movido, renombrado o "
    "eliminado el transporte de subproceso que esta guarda parchea. Esto NO se "
    "silencia: localiza el nuevo punto por el que query()/ClaudeSDKClient lanzan "
    "el CLI 'claude' real y actualiza _no_claude en conftest.py antes de volver a "
    "correr la suite -- hasta entonces, cualquier test podría gastar suscripción "
    "de verdad."
)


def _blocked_handle_request(self: httpx.HTTPTransport, request: httpx.Request) -> httpx.Response:
    raise RuntimeError(_NETWORK_ERROR_MESSAGE)


async def _blocked_handle_async_request(
    self: httpx.AsyncHTTPTransport, request: httpx.Request
) -> httpx.Response:
    raise RuntimeError(_NETWORK_ERROR_MESSAGE)


def _blocked_query(*args: object, **kwargs: object) -> object:
    raise RuntimeError(_CLAUDE_ERROR_MESSAGE)


async def _blocked_transport_connect(self: object, *args: object, **kwargs: object) -> None:
    raise RuntimeError(_CLAUDE_ERROR_MESSAGE)


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


@pytest.fixture(autouse=True)
def _no_claude(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Bloquea el lanzamiento real del CLI `claude` salvo en tests `manual`.

    Importa `claude_agent_sdk`, su transporte interno y `agent_sdk_provider`
    dentro del fixture, no a nivel de módulo de este fichero: `conftest.py`
    se importa siempre que arranca la suite, y un import de
    `claude_agent_sdk` ahí lo pondría en `sys.modules` para toda la sesión
    (T20 verifica en un subproceso limpio que `run-night --dry-run` no lo
    importa; ese test corre en un subproceso propio y no lo notaría, pero
    mantener el import perezoso aquí evita el mismo problema en cualquier
    otro sitio que inspeccione `sys.modules` en proceso).

    **Cierre real del agujero, no de sus síntomas.** Una revisión de T40
    encontró que parchear solo `query` no cierra nada: (a) `ClaudeSDKClient`
    es una segunda API pública que llega al CLI sin pasar por `query()`, y
    la guarda anterior no la tocaba; (b) un test que hiciera
    `from claude_agent_sdk import query` A NIVEL DE MÓDULO vincula ese
    nombre a la función real en el momento en que pytest recolecta el
    fichero -- antes de que cualquier fixture, incluida esta, llegue a
    correr -- así que parchear el atributo `claude_agent_sdk.query` después
    no afecta a un nombre que ya apunta a la función real.

    Ambos caminos convergen en un único punto que sí es real: tanto
    `query()` (`claude_agent_sdk/query.py`, vía `InternalClient.process_query`)
    como `ClaudeSDKClient.connect()` (`claude_agent_sdk/client.py`,
    `_connect_inner`) construyen una instancia de `SubprocessCLITransport` y
    le llaman `.connect()` -- es ese método, y no `query` ni
    `ClaudeSDKClient`, el que de verdad ejecuta `anyio.open_process(...)`
    para lanzar el CLI (`claude_agent_sdk/_internal/transport/subprocess_cli.py`).
    Parcheando `SubprocessCLITransport.connect` en la propia clase se cierran
    los dos caminos de golpe, y además se cierra para cualquier nombre
    (`query` o `ClaudeSDKClient`) que ya estuviera vinculado a la función/
    clase real ANTES de que este fixture corriera: la clase es un único
    objeto compartido por todo el proceso, y tanto `query()` como
    `ClaudeSDKClient` construyen instancias nuevas y hacen la búsqueda de
    `.connect()` en el momento de la llamada (no en el momento del import),
    así que ven el parche sin importar cuándo se vinculó el nombre que
    llegó hasta aquí.

    **Contrapeso honesto.** `SubprocessCLITransport` es un símbolo privado
    (`_internal`): una actualización de `claude-agent-sdk` puede moverlo,
    renombrarlo o cambiar su forma de lanzar el proceso, y dejar esta guarda
    parcheando un método que ya no es el que se ejecuta -- un fallo
    silencioso sería peor que no tener guarda. Por eso el import y el
    parcheo de `.connect` van en un único `try/except` que relanza con un
    mensaje explícito (`_MOVED_TRANSPORT_ERROR_MESSAGE`) en vez de dejar
    pasar el `ImportError` (la clase ya no existe donde se espera) o el
    `AttributeError` (la clase sigue existiendo pero `connect` se renombró
    o desapareció): si el símbolo no aparece donde se espera, la suite
    entera falla en rojo desde el primer test que use esta fixture, con un
    mensaje que dice cómo arreglarlo, en vez de dejar la guarda muda o
    fallar con el `AttributeError` genérico que produciría
    `monkeypatch.setattr(..., raising=True)` por su cuenta. Es un cambio de
    modo de fallo deliberado: una actualización rutinaria del SDK puede
    bloquear la suite local hasta que se actualice esta guarda, y eso es
    preferible a que la bloquee en silencio y alguna llamada real se cuele.

    Se mantienen además, como cinturón sobre tirantes, los dos parcheos
    públicos que ya existían antes de este cambio (`claude_agent_sdk.query`
    y `agent_sdk_provider.query`): no son el cierre real del agujero -- eso
    lo hace el parcheo de `SubprocessCLITransport.connect` de arriba --, pero
    cubren el caso trivial de un `import claude_agent_sdk` a secas seguido de
    `claude_agent_sdk.query(...)` sin coste añadido.

    **Hueco que queda, documentado a propósito.** Un llamador que construya
    su propia subclase de `claude_agent_sdk.Transport` y la pase explícita-
    mente como `transport=...` a `query()` o a `ClaudeSDKClient(...)` nunca
    construye un `SubprocessCLITransport`, así que este parcheo no lo vería.
    Nada en este repositorio hace eso hoy, y para hacerlo haría falta
    importar `claude_agent_sdk.Transport` -- un import que
    `test_llm_call_sites.py` (extendido en este mismo cambio) marca como
    sensible fuera de los sitios legítimos, así que ese segundo camino
    quedaría señalado por el otro test antes de poder ejecutarse. Pero es un
    hueco real de esta guarda concreta, no cerrado por ella misma.
    """
    if request.node.get_closest_marker("manual") is not None:
        return

    import claude_agent_sdk

    from nocturna.infrastructure.llm import agent_sdk_provider

    try:
        from claude_agent_sdk._internal.transport.subprocess_cli import (
            SubprocessCLITransport,
        )

        monkeypatch.setattr(
            SubprocessCLITransport, "connect", _blocked_transport_connect, raising=True
        )
    except (
        ImportError,
        AttributeError,
    ) as exc:  # pragma: no cover - depende de la versión del SDK instalada
        raise RuntimeError(_MOVED_TRANSPORT_ERROR_MESSAGE) from exc

    monkeypatch.setattr(claude_agent_sdk, "query", _blocked_query, raising=True)
    monkeypatch.setattr(agent_sdk_provider, "query", _blocked_query, raising=True)
