"""Dobles de `query()` de `claude_agent_sdk` construidos con los tipos
REALES del paquete instalado (`ResultMessage`), no con dataclasses propias
que casualmente encajen con la documentación.

Hallazgo del paso 1 de T40 (ver docstring de módulo de
`infrastructure/llm/agent_sdk_provider.py`): en `claude-agent-sdk 0.2.153`,
`ResultMessage.usage` es `dict[str, Any] | None`, no un `UsageData`
estructurado. `make_result_message` construye el mensaje real con esa forma.

`build_fake_query` reproduce la firma con la que `AgentSDKProvider.run_agent`
invoca a `query()` (`query(prompt=..., options=...)`) y devuelve un
generador asíncrono real, igual que hace el SDK de verdad: llamar a la
función no ejecuta nada hasta que se itera, y su cuerpo puede emitir
mensajes, dormir (para forzar un timeout o dejar hueco a una cancelación
externa) y terminar lanzando una excepción tras emitir mensajes -- la
secuencia real del SDK para un `query()` que falla es: emite un
`ResultMessage` con `is_error=True` y DESPUÉS el generador lanza
`ResultError` (ver `agent_sdk_provider.py`, `except ResultError`).
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence

from claude_agent_sdk import ResultMessage


def make_result_message(
    *,
    subtype: str = "success",
    is_error: bool = False,
    result: str | None = "resultado del agente",
    usage: dict | None = None,
    api_error_status: int | None = None,
    terminal_reason: str | None = None,
    session_id: str = "session-de-test",
) -> ResultMessage:
    """Construye un `ResultMessage` real, con solo los campos que usa el proveedor.

    El resto de campos obligatorios de `ResultMessage` (`duration_ms` propio
    del CLI, `duration_api_ms`, `num_turns`) no los lee `AgentSDKProvider`
    (mide su propia `duration_ms` con `time.monotonic()`), así que aquí
    llevan valores fijos irrelevantes para el test.

    `api_error_status` reproduce la combinación real que el CLI usa para un
    429/500/529 que interrumpe el turno sin que el propio agente falle:
    `subtype="success"` pero `is_error=True`, con el código HTTP en este
    campo (ver docstring de `agent_sdk_provider.AgentSDKProvider.run_agent`,
    rama tras el bucle: `subtype != "success" or last_result.is_error`).

    `terminal_reason` reproduce el campo que el CLI informa cuando el bucle
    de turnos terminó por un motivo distinto de agotar la conversación con
    normalidad (p. ej. `"max_turns"`): un `ResultMessage` puede llegar con
    `subtype="success"`, `is_error=False` y aun así traer un
    `terminal_reason` que invalida el texto de `result` (ver docstring de
    `agent_sdk_provider.AgentSDKProvider.run_agent`, rama
    `last_result.terminal_reason not in (None, "completed")`).
    """
    return ResultMessage(
        subtype=subtype,
        duration_ms=1,
        duration_api_ms=1,
        is_error=is_error,
        num_turns=1,
        session_id=session_id,
        result=result,
        usage=usage,
        api_error_status=api_error_status,
        terminal_reason=terminal_reason,
    )


def build_fake_query(
    messages: Sequence[object] = (),
    *,
    raise_after: BaseException | None = None,
    sleep_before_s: float | None = None,
    sleep_after_s: float | None = None,
    closed_flag: list[bool] | None = None,
):
    """Fábrica de un doble de `query()`: `query(prompt=..., options=...) -> AsyncIterator`.

    - `messages`: mensajes que emite el generador, en orden, antes de terminar.
    - `sleep_before_s`: si se pasa, el generador espera ese tiempo antes de
      emitir nada -- para forzar un `TimeoutError` de `asyncio.timeout` o
      dejar una ventana en la que cancelar la tarea desde fuera antes de
      cualquier `ResultMessage`.
    - `sleep_after_s`: si se pasa, el generador espera ese tiempo DESPUÉS de
      emitir todos los `messages` -- para dejar una ventana en la que
      cancelar la tarea desde fuera cuando ya se vio un `ResultMessage` (a
      diferencia de `sleep_before_s`, que cancela antes de ver ninguno). Con
      `raise_after` también presente, el `sleep_after_s` ocurre antes de
      lanzarla.
    - `raise_after`: si se pasa, el generador la lanza después de emitir
      todos los `messages` (reproduce la secuencia real de un `query()` que
      falla: `ResultMessage(is_error=True)` y luego `ResultError`).
    - `closed_flag`: si se pasa, se le hace `append(True)` en el `finally`
      del generador -- confirma que `agen.aclose()` (o la propagación de una
      excepción a través del generador) cerró de verdad el iterador, no solo
      que `run_agent` dejó de leerlo.
    """

    async def _fake_query(*, prompt: str, options: object) -> AsyncIterator[object]:
        try:
            if sleep_before_s is not None:
                await asyncio.sleep(sleep_before_s)
            for message in messages:
                yield message
            if sleep_after_s is not None:
                await asyncio.sleep(sleep_after_s)
            if raise_after is not None:
                raise raise_after
        finally:
            if closed_flag is not None:
                closed_flag.append(True)

    return _fake_query


def build_fake_query_with_unreliable_aclose(
    messages: Sequence[object] = (),
    *,
    raise_after: BaseException | None = None,
    sleep_before_s: float | None = None,
    aclose_raises: BaseException,
):
    """Fábrica de un doble de `query()` cuyo `.aclose()` falla siempre con
    `aclose_raises`, con independencia de si el generador interno seguiría
    vivo en ese instante -- simula un `agen.aclose()` que falla de verdad.

    **Por qué no basta con `build_fake_query` + un `finally: except
    GeneratorExit: raise ...` dentro del generador.** Se comprobó
    empíricamente (ver notas de la tarea T40, ciclo de corrección) que en
    TODO camino por el que `AgentSDKProvider.run_agent` sale de su
    `async for message in agen:` -- agotamiento normal, `raise_after` del
    propio generador, o el `CancelledError` que `asyncio.timeout`/una
    cancelación externa inyectan en el punto exacto donde el generador está
    suspendido -- el generador **ya está cerrado** (`agen.ag_frame is
    None`) en el momento en que `run_agent` llega a su `finally:
    await agen.aclose()`. Por diseño de Python, `aclose()` sobre un
    generador async ya cerrado es un no-op garantizado por el lenguaje: no
    hay ningún punto de suspensión al que entregarle `GeneratorExit`. Un
    doble basado únicamente en un generador real, por tanto, nunca puede
    ejercitar la rama `except ... as close_exc` de `run_agent` sin importar
    cómo se estructure su cuerpo.

    Este doble evita el problema envolviendo un generador real (construido
    igual que en `build_fake_query`, mismo comportamiento de mensajes y
    `raise_after`/`sleep_before_s` para dejar una `LLMTimeout`/`LLMError` en
    vuelo si hace falta) en un objeto que delega `__anext__` en él pero
    sustituye `aclose()` por completo: siempre lanza `aclose_raises`, sin
    tocar el generador interno. Así se ejercita la lógica de `run_agent`
    (qué hace cuando `aclose()` falla mientras hay o no una excepción de
    dominio en curso) sin depender de una suspensión real del generador que
    esta implementación jamás provoca.
    """
    inner_factory = build_fake_query(
        messages, raise_after=raise_after, sleep_before_s=sleep_before_s
    )

    class _UnreliableAcloseQuery:
        def __init__(self, *, prompt: str, options: object) -> None:
            self._inner = inner_factory(prompt=prompt, options=options)

        def __aiter__(self) -> AsyncIterator[object]:
            return self

        async def __anext__(self) -> object:
            return await self._inner.__anext__()

        async def aclose(self) -> None:
            raise aclose_raises

    def _fake_query(*, prompt: str, options: object) -> _UnreliableAcloseQuery:
        return _UnreliableAcloseQuery(prompt=prompt, options=options)

    return _fake_query
