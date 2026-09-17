"""Excepciones de dominio.

Todo lo que puede fallar por incumplir una regla de negocio se expresa aquí,
nunca con `Exception` genérica ni con `assert`. `application/` e
`infrastructure/` capturan estas excepciones por su nombre, no por texto.

`BudgetExceeded` y `OutsideExecutionWindow` NO se definen en este módulo:
son responsabilidad exclusiva de `BudgetGuard` (T30), que vive en
`application/budget.py`. El control de gasto no es una invariante de
entidad y no le corresponde al dominio decidir cuándo se agota un
presupuesto o se cierra la ventana de ejecución.
"""


class DomainError(Exception):
    """Raíz de toda excepción de dominio."""


class InvariantViolation(DomainError):
    """Una entidad no cumple una regla que debe cumplir siempre."""


class InterestScoreOutOfRange(InvariantViolation):
    """`Reading.interest_score` fuera del rango permitido (1 a 5)."""


class ConfidenceOutOfRange(InvariantViolation):
    """`Finding.confidence` fuera del rango permitido (0.0 a 1.0)."""


class InvalidTimestamp(InvariantViolation):
    """Un `datetime` de dominio es naive o incoherente con otro timestamp."""


class InvalidTransition(DomainError):
    """Una entidad intenta pasar de un estado a otro no permitido.

    `entity` es obligatorio (no hay transición sin saber de qué entidad) y
    se expone como atributo junto a `origin` y `target`, igual que ellos,
    para que quien capture pueda distinguir "Run cerrado" de "Item ya
    leído" sin parsear el mensaje: estas excepciones se capturan por
    nombre/atributos, no por texto.
    """

    def __init__(self, origin: str, target: str, entity: str) -> None:
        super().__init__(f"transición no permitida para {entity} de '{origin}' a '{target}'")
        self.origin = origin
        self.target = target
        self.entity = entity


class RunAlreadyFinished(InvalidTransition):
    """Se intentó registrar una llamada a un agente contra un `Run` ya cerrado.

    No es una transición de estado: nadie pidió pasar a `killed`, es un
    intento de contabilizar tokens contra un Run terminal. Hereda de
    `InvalidTransition` para que el código que ya captura por la clase
    padre (`record_agent_call`) siga funcionando, pero el mensaje dice lo
    único que importa a las 3 de la mañana: que el Run ya está cerrado.
    """

    def __init__(self, status: str) -> None:
        DomainError.__init__(
            self,
            f"no se pueden contabilizar tokens: el Run ya está cerrado (status='{status}')",
        )
        self.origin = status
        self.target = status
        self.entity = "Run"


class GuardedFieldAssignment(InvariantViolation):
    """Se intentó asignar directamente un campo que solo cambia a través de
    un método de la entidad (transición de estado o publicación)."""


class LLMError(DomainError):
    """Fallo al invocar un agente a través de `LLMProvider`.

    `tokens_in`/`tokens_out` transportan el gasto ya incurrido cuando se
    conoce (T40, `AgentSDKProvider`): un `ResultMessage` de fallo puede
    traer `usage` igualmente, porque la suscripción ya pagó esos tokens
    aunque la llamada no haya producido una salida utilizable. Por defecto
    `0`, no porque el gasto real pueda ser cero "por si acaso", sino porque
    la mayoría de fallos (timeout, error de proceso) no tienen ningún
    `usage` que propagar. Quien captura esta excepción (T41-T44) es
    responsable de contabilizar estos tokens en `AgentCall` igual que si la
    llamada hubiera tenido éxito: el presupuesto ya se gastó.

    `api_error_status` transporta el código HTTP (429/500/529/...) que el
    CLI informó para el fallo, cuando lo informó; `None` si el fallo no
    vino acompañado de un status HTTP (timeout, error de proceso, JSON
    inválido...). Se expone como atributo, no solo incrustado en el texto
    del mensaje, porque este módulo dice explícitamente que estas
    excepciones "se capturan por su nombre, no por texto": quien reaccione
    a un límite de tasa (T41/T44) necesita poder escribir
    `exc.api_error_status == 429` sin parsear `str(exc)`.
    """

    def __init__(
        self,
        message: str,
        *,
        tokens_in: int = 0,
        tokens_out: int = 0,
        api_error_status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.tokens_in = tokens_in
        self.tokens_out = tokens_out
        self.api_error_status = api_error_status


class LLMRateLimited(LLMError):
    """El proveedor LLM ha rechazado la llamada por límite de tasa.

    Sin emisor todavía, pero no por falta de señal del SDK: `claude-agent-sdk`
    (0.2.153, versión instalada en T40) sí exporta señal de límite de tasa —
    `RateLimitEvent`/`RateLimitInfo` (`claude_agent_sdk/types.py`), con
    `status` (`allowed`/`allowed_warning`/`rejected`), `utilization` y
    `resets_at` — emitida como mensaje más en el mismo stream que recorre
    `AgentSDKProvider.run_agent` (ver ese módulo). Lo que falta no es la
    señal sino decidir la reacción (reintentar, cortar la noche, avisar):
    eso es alcance de T41/T44, no de T40. `AgentSDKProvider` no lanza esta
    excepción todavía.
    """


class LLMTimeout(LLMError):
    """El proveedor LLM no ha respondido dentro del tiempo permitido.

    La lanza `AgentSDKProvider` (T40) cuando `request.timeout_s` se agota
    dentro de `asyncio.timeout`. Distinta de una cancelación externa
    (`asyncio.CancelledError`, p. ej. el corte de `hard_stop` en T44):
    `AgentSDKProvider` nunca traduce una cancelación a este error.
    """
