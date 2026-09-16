"""Entidades del dominio de Nocturna.

Cada entidad protege sus propias invariantes: los campos que dependen de una
transición de estado no se escriben desde fuera, se cambian a través de
métodos que consultan una tabla de transiciones explícita y lanzan
`InvalidTransition` ante cualquier salto no permitido, incluida la
repetición del mismo estado. No hay idempotencia silenciosa: quien llama dos
veces a la misma transición se equivoca y el dominio se lo dice.

Esa protección no es solo de convención: `Item`, `Finding` y `Run` son
dataclasses mutables (`slots=True`, sin `frozen`) porque necesitan poder
reconstruirse por constructor desde el ORM (T11), pero cada una sobrescribe
`__setattr__` para que los campos que dependen de una transición no se
puedan pisar con una asignación directa desde fuera. La detección de "es la
primera asignación" (la que hace el propio `__init__` generado por
`dataclass`, incluida la rehidratación) se apoya en que, con `slots=True`,
un atributo no asignado aún no existe: `hasattr(self, campo)` es `False`
hasta que se asigna una vez. A partir de ahí, cualquier reasignación externa
se rechaza; los métodos internos que sí necesitan cambiar el campo (la
transición ya validada) usan `object.__setattr__` para saltarse su propio
guarda. El rechazo lanza `GuardedFieldAssignment` (un `InvariantViolation`),
nunca un `AttributeError` pelado.

Todos los `datetime` que entran en una entidad deben ser *aware* (con
`tzinfo`); el dominio no convierte ni asume zonas horarias, así que un
`datetime` naive es un `InvalidTimestamp`. La conversión, si hace falta,
es cosa de `infrastructure/`.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import ClassVar
from uuid import UUID, uuid4

from nocturna.domain.errors import (
    ConfidenceOutOfRange,
    GuardedFieldAssignment,
    InterestScoreOutOfRange,
    InvalidTimestamp,
    InvalidTransition,
    InvariantViolation,
    RunAlreadyFinished,
)
from nocturna.domain.llm import AgentRole


def _require_aware(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise InvalidTimestamp(f"'{field_name}' debe ser un datetime con zona horaria")


def _require_non_empty(value: str, field_name: str) -> None:
    if not value.strip():
        raise InvariantViolation(f"'{field_name}' no puede estar vacío")


class ItemStatus(StrEnum):
    """Estado de un ítem de ingesta a lo largo del pipeline."""

    NEW = "new"
    READ = "read"
    DISCARDED = "discarded"
    PUBLISHED = "published"


_ITEM_TRANSITIONS: Mapping[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.NEW: frozenset({ItemStatus.READ}),
    ItemStatus.READ: frozenset({ItemStatus.DISCARDED, ItemStatus.PUBLISHED}),
    ItemStatus.DISCARDED: frozenset(),
    ItemStatus.PUBLISHED: frozenset(),
}


@dataclass(slots=True)
class Item:
    """Unidad de ingesta (fase 1: un abstract de arXiv).

    La unicidad de `(source, external_id)` no es una invariante de esta
    entidad: es una restricción de conjunto y se aplica con un índice único
    en la capa de persistencia (T11), no aquí.
    """

    source: str
    external_id: str
    title: str
    abstract: str
    categories: list[str]
    published_at: datetime
    fetched_at: datetime
    status: ItemStatus = ItemStatus.NEW
    id: UUID = field(default_factory=uuid4)

    _GUARDED_FIELDS: ClassVar[frozenset[str]] = frozenset({"status"})

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._GUARDED_FIELDS and hasattr(self, name):
            raise GuardedFieldAssignment(
                f"'{name}' de Item no se puede asignar directamente; "
                "usa mark_read()/discard()/publish()"
            )
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        _require_non_empty(self.source, "source")
        _require_non_empty(self.external_id, "external_id")
        _require_non_empty(self.title, "title")
        _require_non_empty(self.abstract, "abstract")
        if not self.categories:
            raise InvariantViolation("'categories' debe tener al menos un elemento")
        for category in self.categories:
            _require_non_empty(category, "categories")
        _require_aware(self.published_at, "published_at")
        _require_aware(self.fetched_at, "fetched_at")

    def _transition_to(self, target: ItemStatus) -> None:
        allowed = _ITEM_TRANSITIONS[self.status]
        if target not in allowed:
            raise InvalidTransition(self.status.value, target.value, entity="Item")
        object.__setattr__(self, "status", target)

    def mark_read(self) -> None:
        """Marca el ítem como leído por el Reader."""
        self._transition_to(ItemStatus.READ)

    def discard(self) -> None:
        """Descarta el ítem (por ejemplo, `interest_score` bajo)."""
        self._transition_to(ItemStatus.DISCARDED)

    def publish(self) -> None:
        """Marca el ítem como publicado tras generar su `Finding`."""
        self._transition_to(ItemStatus.PUBLISHED)


@dataclass(frozen=True, slots=True)
class Reading:
    """Salida del Reader para un `Item`: una llamada ya ocurrida.

    No tiene `created_at`: es un hecho inmutable, no un registro con su
    propio ciclo de vida.
    """

    item_id: UUID
    summary: str
    objects: tuple[str, ...]
    claims: tuple[str, ...]
    interest_score: int
    tokens_in: int
    tokens_out: int
    model: str
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        object.__setattr__(self, "objects", tuple(self.objects))
        object.__setattr__(self, "claims", tuple(self.claims))
        _require_non_empty(self.summary, "summary")
        for obj in self.objects:
            _require_non_empty(obj, "objects")
        for claim in self.claims:
            _require_non_empty(claim, "claims")
        if not 1 <= self.interest_score <= 5:
            raise InterestScoreOutOfRange(
                f"'interest_score' debe estar entre 1 y 5, recibido {self.interest_score}"
            )
        if self.tokens_in < 0:
            raise InvariantViolation("'tokens_in' no puede ser negativo")
        if self.tokens_out < 0:
            raise InvariantViolation("'tokens_out' no puede ser negativo")
        _require_non_empty(self.model, "model")


class FindingType(StrEnum):
    """Tipo de hallazgo publicado. Fase 1: solo explicación de un paper."""

    PAPER_EXPLAINED = "paper_explained"


@dataclass(slots=True)
class Finding:
    """Hallazgo publicable en la web, con sus tres niveles de lectura."""

    item_id: UUID
    run_id: UUID
    type: FindingType
    title: str
    level_curious: str
    level_amateur: str
    level_technical: str
    confidence: float | None = None
    published_at: datetime | None = None
    id: UUID = field(default_factory=uuid4)

    _GUARDED_FIELDS: ClassVar[frozenset[str]] = frozenset({"confidence", "published_at"})

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._GUARDED_FIELDS and hasattr(self, name):
            raise GuardedFieldAssignment(
                f"'{name}' de Finding no se puede asignar directamente; usa publish()"
            )
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        _require_non_empty(self.title, "title")
        _require_non_empty(self.level_curious, "level_curious")
        _require_non_empty(self.level_amateur, "level_amateur")
        _require_non_empty(self.level_technical, "level_technical")
        if (self.published_at is None) != (self.confidence is None):
            raise InvariantViolation(
                "'published_at' y 'confidence' deben estar ambos informados o ambos vacíos"
            )
        if self.published_at is not None:
            _require_aware(self.published_at, "published_at")
        if self.confidence is not None:
            self._validate_confidence(self.confidence)

    @staticmethod
    def _validate_confidence(confidence: float) -> None:
        if not 0.0 <= confidence <= 1.0:
            raise ConfidenceOutOfRange(
                f"'confidence' debe estar entre 0.0 y 1.0, recibido {confidence}"
            )

    @property
    def is_published(self) -> bool:
        return self.published_at is not None

    def publish(self, confidence: float, at: datetime) -> None:
        """Publica el hallazgo con la confianza asignada por el Editor."""
        if self.is_published:
            raise InvalidTransition("published", "published", entity="Finding")
        self._validate_confidence(confidence)
        _require_aware(at, "published_at")
        object.__setattr__(self, "confidence", confidence)
        object.__setattr__(self, "published_at", at)


class RunStatus(StrEnum):
    """Estado de una ejecución nocturna del pipeline.

    `RUNNING` no está en `CLAUDE.md` § "Modelo de dominio": los cuatro
    estados descritos allí (`completed`, `partial`, `failed`, `killed`) son
    terminales, pero T44 crea el `Run` al empezar la noche y necesita un
    estado inicial no terminal.
    """

    RUNNING = "running"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    KILLED = "killed"


_RUN_TERMINAL_STATUSES: frozenset[RunStatus] = frozenset(
    {RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.FAILED, RunStatus.KILLED}
)


@dataclass(slots=True)
class Run:
    """Una ejecución nocturna del pipeline, con su presupuesto y su gasto.

    `budget_tokens` es el presupuesto EFECTIVO de la noche: ya lleva
    aplicado, si toca, el `reset_day_multiplier` de
    `infrastructure/config.py`. No se reaplica el multiplicador en ningún
    punto posterior del pipeline ni de la rehidratación.

    Dos políticas de guarda distintas conviven en esta entidad, según lo que
    representa cada campo:

    - **Congelados tras la construcción** (`budget_tokens`, `status`,
      `finished_at`, `tokens_used`): fijan el marco de la noche o solo
      cambian a través de un método que valida la operación. `budget_tokens`
      no se toca una vez arrancado el Run porque cambiar el presupuesto a
      mitad de ejecución invalidaría cualquier comprobación de `BudgetGuard`
      hecha hasta ese momento; `status` y su campo acoplado `finished_at`
      solo cambian mediante `finish()`, que valida la transición a un estado
      terminal y la coherencia de la fecha; `tokens_used` solo cambia
      mediante `record_agent_call()`, que es quien afirma en su docstring
      ser el único camino para incrementarlo, así que aquí no hace falta
      además una política monótona escribible: directamente no se puede
      asignar desde fuera.
    - **Monótonos no decrecientes** (`items_fetched`, `items_read`,
      `findings_published`): son contadores de sucesos ya ocurridos durante
      la noche (ítems ingeridos/leídos, hallazgos publicados) que T44
      incrementa directamente. Se permite asignarles un valor mayor o igual
      al actual —así T44 los va incrementando a medida que avanza el
      pipeline—, pero nunca un decremento: un hallazgo publicado o un ítem
      leído no se puede "deshacer" retrocediendo el contador. La guarda
      también exige que el valor sea `int` (y no `bool`, que en Python es
      subclase de `int`): cualquier otro tipo se rechaza con
      `GuardedFieldAssignment`, nunca con un `TypeError` de comparación sin
      capturar.

    Ninguna de estas dos guardas es hermética. Los métodos internos que sí
    necesitan escribir un campo congelado usan `object.__setattr__`, que
    esquiva `__setattr__` por diseño; con `slots=True` también lo esquivan
    `Run.tokens_used.__set__(run, valor)` (el descriptor de slot expuesto en
    la clase) y `dataclasses.replace(run, tokens_used=valor)` (que además
    conserva el `id`, así que el resultado parece "el mismo" Run). La guarda
    protege contra una asignación directa por error o por atajo, no contra
    quien deliberadamente rodea `__setattr__`; la defensa real de T30 contra
    un `tokens_used` manipulado en memoria es leer el acumulado con
    `AgentCallRepository.tokens_used_for_run` desde la base de datos, nunca
    fiarse del valor que trae el objeto `Run` en memoria.
    """

    started_at: datetime
    budget_tokens: int
    finished_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    tokens_used: int = 0
    items_fetched: int = 0
    items_read: int = 0
    findings_published: int = 0
    notes: str = ""
    id: UUID = field(default_factory=uuid4)

    _LOCKED_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"status", "budget_tokens", "finished_at", "tokens_used"}
    )
    _MONOTONIC_FIELDS: ClassVar[frozenset[str]] = frozenset(
        {"items_fetched", "items_read", "findings_published"}
    )

    def __setattr__(self, name: str, value: object) -> None:
        if name in self._MONOTONIC_FIELDS and hasattr(self, name):
            if not isinstance(value, int) or isinstance(value, bool):
                raise GuardedFieldAssignment(
                    f"'{name}' de Run debe ser int, recibido {type(value).__name__!r}"
                )
            current = getattr(self, name)
            if value < current:
                raise GuardedFieldAssignment(
                    f"'{name}' de Run es un contador monótono no decreciente; "
                    "solo se puede asignar un valor mayor o igual al actual"
                )
            object.__setattr__(self, name, value)
            return
        if name in self._LOCKED_FIELDS and hasattr(self, name):
            raise GuardedFieldAssignment(
                f"'{name}' de Run no se puede asignar directamente tras la construcción"
            )
        object.__setattr__(self, name, value)

    def __post_init__(self) -> None:
        _require_aware(self.started_at, "started_at")
        if self.budget_tokens <= 0:
            raise InvariantViolation("'budget_tokens' debe ser mayor que cero")
        if self.tokens_used < 0:
            raise InvariantViolation("'tokens_used' no puede ser negativo")
        if self.items_fetched < 0:
            raise InvariantViolation("'items_fetched' no puede ser negativo")
        if self.items_read < 0:
            raise InvariantViolation("'items_read' no puede ser negativo")
        if self.findings_published < 0:
            raise InvariantViolation("'findings_published' no puede ser negativo")
        if (self.status != RunStatus.RUNNING) != (self.finished_at is not None):
            raise InvariantViolation(
                "'finished_at' debe estar informado si y solo si 'status' no es 'running'"
            )
        if self.finished_at is not None:
            _require_aware(self.finished_at, "finished_at")
            if self.finished_at < self.started_at:
                raise InvariantViolation("'finished_at' no puede ser anterior a 'started_at'")

    def finish(self, status: RunStatus, at: datetime, notes: str | None = None) -> None:
        """Cierra el Run con un estado terminal."""
        if self.status != RunStatus.RUNNING:
            raise InvalidTransition(self.status.value, status.value, entity="Run")
        if status not in _RUN_TERMINAL_STATUSES:
            raise InvalidTransition(self.status.value, status.value, entity="Run")
        _require_aware(at, "finished_at")
        if at < self.started_at:
            raise InvariantViolation("'finished_at' no puede ser anterior a 'started_at'")
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "finished_at", at)
        if notes is not None:
            self.notes = notes

    def record_agent_call(self, call: "AgentCall") -> None:
        """Único camino para incrementar `tokens_used`, a partir de una llamada ya hecha."""
        if self.status != RunStatus.RUNNING:
            raise RunAlreadyFinished(self.status.value)
        if call.run_id != self.id:
            raise InvariantViolation("'call.run_id' no coincide con el id de este Run")
        object.__setattr__(self, "tokens_used", self.tokens_used + call.total_tokens)


class AgentCallStatus(StrEnum):
    """Resultado de una llamada a un agente."""

    OK = "ok"
    INVALID_OUTPUT = "invalid_output"
    ERROR = "error"
    TIMEOUT = "timeout"


@dataclass(frozen=True, slots=True)
class AgentCall:
    """Registro de una llamada a un agente ya ocurrida.

    Se construye con los datos ya conocidos tras la llamada: no hay estado
    intermedio "en curso" en esta entidad.
    """

    run_id: UUID
    item_id: UUID | None
    agent: AgentRole
    model: str
    tokens_in: int
    tokens_out: int
    duration_ms: int
    status: AgentCallStatus
    id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_non_empty(self.model, "model")
        if self.tokens_in < 0:
            raise InvariantViolation("'tokens_in' no puede ser negativo")
        if self.tokens_out < 0:
            raise InvariantViolation("'tokens_out' no puede ser negativo")
        if self.duration_ms < 0:
            raise InvariantViolation("'duration_ms' no puede ser negativo")

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out
