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
    """Fallo al invocar un agente a través de `LLMProvider`."""


class LLMRateLimited(LLMError):
    """El proveedor LLM ha rechazado la llamada por límite de tasa.

    Sin consumidor en fase 1: la lanza `AgentSDKProvider` en T40.
    """


class LLMTimeout(LLMError):
    """El proveedor LLM no ha respondido dentro del tiempo permitido.

    Sin consumidor en fase 1: la lanza `AgentSDKProvider` en T40.
    """
