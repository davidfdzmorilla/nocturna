"""`FakeLLMProvider`: doble de test de `LLMProvider`, sin red, sin SDK, sin base de datos.

Cumple `nocturna.domain.llm.LLMProvider` estructuralmente (el `Protocol` es
`runtime_checkable`, así que `isinstance(fake, LLMProvider)` vale `True` sin
que este módulo herede de nada). Ningún test de dominio o de aplicación debe
llamar a `AgentSDKProvider` de verdad: eso es exactamente lo que impide este
fake (`CLAUDE.md`, `.claude/skills/testing-without-claude`). No importa
`claude_agent_sdk` ni nada de `nocturna.infrastructure`.

Se configura por rol con colas FIFO independientes: cada llamada a
`respond()`/`fail()` encola una respuesta o un fallo para ese rol, y cada
llamada a `run_agent()` desencola la siguiente entrada de la cola del rol
pedido. Agotar la cola de un rol es un error explícito
(`ResponseQueueExhausted`), nunca repetir la última entrada: un caso de uso
que llama a un rol más veces de las que el test programó es, casi siempre,
un bucle o una llamada de más, y debe fallar en rojo señalando qué rol se
quedó sin respuestas.
"""

from __future__ import annotations

import json as json_lib
from collections import deque
from dataclasses import dataclass

from nocturna.domain.llm import AgentRequest, AgentResult, AgentRole


class ResponseQueueExhausted(AssertionError):
    """Se llamó a `run_agent()` para un rol sin respuestas ni fallos en cola.

    Hereda de `AssertionError`, no de `Exception` a secas: agotar la cola
    programada es, para el test que lo dispara, una aserción rota ("el
    código bajo test llamó a este rol el número de veces que yo esperaba"),
    no un error inesperado del propio fake. Así `pytest` lo trata igual que
    cualquier otro `assert` fallido, con traceback corto y sin necesidad de
    capturarlo aparte para que la suite lo señale en rojo.
    """

    def __init__(self, role: AgentRole, *, calls_so_far: int) -> None:
        super().__init__(
            f"FakeLLMProvider: no quedan respuestas programadas para el rol "
            f"'{role.value}' (van {calls_so_far} llamadas registradas en total en "
            "fake.calls). Programa una respuesta o un fallo más con "
            "fake.respond(...) / fake.fail(...), o revisa si el código bajo test "
            "está llamando a este agente más veces de las esperadas."
        )
        self.role = role
        self.calls_so_far = calls_so_far


@dataclass(slots=True)
class _QueuedResult:
    """Una entrada en cola: o bien una respuesta, o bien un fallo programado.

    `error is not None` marca un fallo; en ese caso el resto de campos no se
    usan. No se modela como dos clases separadas porque ambas conviven en la
    misma cola FIFO por rol y se desencolan de la misma forma.
    """

    output_text: str | None
    tokens_in: int
    tokens_out: int
    duration_ms: int
    model: str | None
    error: Exception | None


class FakeLLMProvider:
    """`LLMProvider` de test, configurado a mano con `respond()`/`fail()`.

    Ejemplo (ver también `.claude/skills/testing-without-claude`):

        fake = FakeLLMProvider()
        fake.respond(AgentRole.READER, json=valid_reading_dict, tokens_in=1200, tokens_out=300)
        fake.fail(AgentRole.POPULARIZER, error=LLMRateLimited("límite alcanzado"))

    `calls` guarda el `AgentRequest` íntegro de cada llamada a `run_agent()`,
    en el orden en que llegaron, incluidas las que terminan en una excepción
    programada con `fail()`: sirve para afirmar sobre orden, modelo,
    `max_turns`, `timeout_s` y número de llamadas por rol (por ejemplo, que
    el Editor se llamó exactamente una vez).
    """

    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []
        self._queues: dict[AgentRole, deque[_QueuedResult]] = {}

    def respond(
        self,
        role: AgentRole,
        *,
        json: dict | None = None,
        raw: str | None = None,
        tokens_in: int,
        tokens_out: int,
        duration_ms: int = 0,
        model: str | None = None,
    ) -> None:
        """Encola una respuesta con éxito para la próxima llamada a `role`.

        Exactamente uno de `json`/`raw`: `json` serializa el dict a
        `AgentResult.output_text` (JSON válido); `raw` deja el texto tal
        cual, sin serializar, para simular JSON inválido o un fence roto.
        Pasar ambos o ninguno es un error de programación del test, no un
        escenario a probar, así que falla aquí con `ValueError` en vez de
        colarse silenciosamente.

        `tokens_in`/`tokens_out` son obligatorios y sin valor por defecto,
        igual que en `AgentResult`: un test que no piensa en los tokens no
        debe poder escribirse por accidente (llamar sin ellos da
        `TypeError`, por ser parámetros solo-de-palabra-clave sin default).

        `model`: si no se pasa, `run_agent()` devuelve `request.model`,
        igual que hace `AgentSDKProvider`.
        """
        if (json is None) == (raw is None):
            raise ValueError(
                "FakeLLMProvider.respond: pasa exactamente uno de 'json' o 'raw', "
                "nunca ambos ni ninguno."
            )
        output_text = json_lib.dumps(json) if json is not None else raw
        self._queues.setdefault(role, deque()).append(
            _QueuedResult(
                output_text=output_text,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                duration_ms=duration_ms,
                model=model,
                error=None,
            )
        )

    def fail(self, role: AgentRole, *, error: Exception) -> None:
        """Encola un fallo: la próxima llamada a `role` relanza `error` tal cual.

        `error` puede ser cualquier excepción: `LLMTimeout`, `LLMRateLimited`,
        `LLMError` (con o sin `tokens_in`/`tokens_out`) o una excepción
        genérica, para probar que el código bajo test no da por hecho que
        todo fallo es un `LLMError` de dominio.
        """
        self._queues.setdefault(role, deque()).append(
            _QueuedResult(
                output_text=None,
                tokens_in=0,
                tokens_out=0,
                duration_ms=0,
                model=None,
                error=error,
            )
        )

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        """Desencola la siguiente entrada programada para `request.role`.

        Registra `request` en `calls` antes de resolver la cola, así que
        también queda registrada la llamada que agota la cola o que termina
        en una excepción programada con `fail()`.
        """
        self.calls.append(request)
        queue = self._queues.get(request.role)
        if not queue:
            raise ResponseQueueExhausted(request.role, calls_so_far=len(self.calls))
        item = queue.popleft()
        if item.error is not None:
            raise item.error
        assert item.output_text is not None  # invariante interna: ver _QueuedResult
        return AgentResult(
            role=request.role,
            model=item.model if item.model is not None else request.model,
            output_text=item.output_text,
            tokens_in=item.tokens_in,
            tokens_out=item.tokens_out,
            duration_ms=item.duration_ms,
        )
