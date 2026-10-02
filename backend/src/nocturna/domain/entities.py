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

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
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
    """Estado de un ítem de ingesta a lo largo del pipeline.

    `FAILED` (decidido por el autor, ex `OPEN_DECISIONS.md:13`) es terminal:
    un ítem cuyo Reader agotó los reintentos de JSON inválido no vuelve a
    `NEW`. Sin un estado terminal de fallo, un ítem así sería un ítem
    envenenado permanente: `next_unread` lo devolvería cada noche y volvería
    a gastar `max_calls_per_item` llamadas contra él para siempre.
    """

    NEW = "new"
    READ = "read"
    DISCARDED = "discarded"
    PUBLISHED = "published"
    FAILED = "failed"


_ITEM_TRANSITIONS: Mapping[ItemStatus, frozenset[ItemStatus]] = {
    ItemStatus.NEW: frozenset({ItemStatus.READ, ItemStatus.FAILED}),
    ItemStatus.READ: frozenset({ItemStatus.DISCARDED, ItemStatus.PUBLISHED}),
    ItemStatus.DISCARDED: frozenset(),
    ItemStatus.PUBLISHED: frozenset(),
    ItemStatus.FAILED: frozenset(),
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
    # T79: marca de la ingesta (`ExoplanetFilter`); decide la variante del
    # Reader (v3 solo si True y categoría de medidas). Default False = v2.
    exoplanet_match: bool = False

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

    def mark_failed(self) -> None:
        """Marca el ítem como fallido tras agotar los reintentos del Reader.

        Terminal: solo alcanzable desde `NEW` (el Reader falla antes de
        producir un `Reading` válido). Un ítem ya `READ` no puede fallar
        retroactivamente por esta vía.
        """
        self._transition_to(ItemStatus.FAILED)


class MeasuredParameter(StrEnum):
    """Parámetro físico de un planeta que el Reader v3 puede medir (T71.c).

    Fase 2, alcance inicial: masa, radio y periodo. `m sin i` (masa mínima
    de velocidad radial, distinta de la masa verdadera) queda fuera de este
    esquema por decisión expresa del autor; el riesgo de mezclarla con
    `MASS` se acepta durante T71.c (ver `OPEN_DECISIONS.md`).
    """

    MASS = "mass"
    RADIUS = "radius"
    PERIOD = "period"


class MeasurementUnit(StrEnum):
    """Unidad de una medida estructurada (T71.c)."""

    M_JUP = "M_jup"
    M_EARTH = "M_earth"
    R_JUP = "R_jup"
    R_EARTH = "R_earth"
    DAY = "day"


class MeasurementLimit(StrEnum):
    """Si una medida es un valor puntual o una cota (T71.c).

    Una cota (`UPPER`/`LOWER`) no tiene los dos errores del valor puntual
    que hace falta para calcular una tensión: por eso `Measurement` la
    excluye de `usable_for_tension` con independencia de qué traigan
    `err_plus`/`err_minus`.
    """

    NONE = "none"
    UPPER = "upper"
    LOWER = "lower"


class MeasurementOrigin(StrEnum):
    """Si la medida es el resultado propio del paper o una cita a literatura
    previa (T71.c). Solo `THIS_WORK` es candidata a tensión: citar el valor
    de otro trabajo no es lo que T73 quiere comparar contra el archivo.
    """

    THIS_WORK = "this_work"
    LITERATURE = "literature"


UNITS_BY_PARAMETER: Mapping[MeasuredParameter, frozenset[MeasurementUnit]] = {
    MeasuredParameter.MASS: frozenset({MeasurementUnit.M_JUP, MeasurementUnit.M_EARTH}),
    MeasuredParameter.RADIUS: frozenset({MeasurementUnit.R_JUP, MeasurementUnit.R_EARTH}),
    MeasuredParameter.PERIOD: frozenset({MeasurementUnit.DAY}),
}


@dataclass(frozen=True, slots=True)
class Measurement:
    """Medida de un parámetro físico de un planeta, atribuida por el Reader
    v3 a partir de un abstract (T71.c).

    Value object sin identidad propia: nace dentro de `Reading.measurements`
    y no se referencia desde fuera de esa tupla.

    `evidence` es la subcadena literal del abstract de la que sale la
    medida; el filtrado de si lo es de verdad (salvo espacios) vive en
    `application/`, no aquí -- este `__post_init__` solo exige que no esté
    en blanco.
    """

    planet_name: str
    parameter: MeasuredParameter
    value: float
    err_plus: float | None
    err_minus: float | None
    unit: MeasurementUnit
    limit: MeasurementLimit
    origin: MeasurementOrigin
    evidence: str

    def __post_init__(self) -> None:
        _require_non_empty(self.planet_name, "planet_name")
        _require_non_empty(self.evidence, "evidence")
        if not math.isfinite(self.value):
            raise InvariantViolation("'value' debe ser un número finito")
        if self.value <= 0:
            raise InvariantViolation("'value' debe ser mayor que cero")
        for err_field_name in ("err_plus", "err_minus"):
            err_value = getattr(self, err_field_name)
            if err_value is None:
                continue
            if not math.isfinite(err_value):
                raise InvariantViolation(f"'{err_field_name}' debe ser un número finito")
            if err_value < 0:
                raise InvariantViolation(f"'{err_field_name}' no puede ser negativo")
        if self.unit not in UNITS_BY_PARAMETER[self.parameter]:
            raise InvariantViolation(
                f"'unit' {self.unit.value!r} no es coherente con "
                f"'parameter' {self.parameter.value!r}"
            )

    @property
    def usable_for_tension(self) -> bool:
        """Utilizable para calcular una tensión contra el archivo (T73):
        resultado propio del paper (no una cita a literatura previa), un
        valor puntual (no una cota superior o inferior) y con los dos
        errores informados, que es lo que necesita la fórmula de σ.
        """
        return (
            self.origin == MeasurementOrigin.THIS_WORK
            and self.limit == MeasurementLimit.NONE
            and self.err_plus is not None
            and self.err_minus is not None
        )


@dataclass(frozen=True, slots=True)
class CatalogSolution:
    """Una solución publicada de un parámetro de un planeta en el catálogo.

    Es la "previa" frente a la que se compara la medida de un paper.
    `is_default` marca la solución que el archivo declara por defecto para
    el planeta (referencia de `TensionResult.is_candidate`, OPEN_DECISIONS
    T73, 2026-09-30). `arxiv_id` identifica el paper de origen, si el
    archivo lo conoce, para que el caso de uso excluya la solución del
    propio paper que se está analizando. Contrato para el adaptador (T74):
    identificador arXiv SIN versión y con el mismo formato que
    `Item.external_id` (p. ej. `2609.30038`, nunca `2609.30038v2` ni
    `arXiv:2609.30038`), porque la exclusión compara por igualdad exacta
    de cadenas. `None` si el adaptador no lo reconoce: entonces el paper se
    compara consigo mismo y sale σ ≈ 0 (fallo hacia el lado seguro).
    """

    planet_name: str
    parameter: MeasuredParameter
    value: float
    err_plus: float | None
    err_minus: float | None
    unit: MeasurementUnit
    limit: MeasurementLimit
    reference: str
    is_default: bool
    arxiv_id: str | None
    # T88: metadatos del archivo para elegir la referencia (`select_reference`).
    solution_key: str | None = None
    soltype: str | None = None
    pl_pubdate: str | None = None
    releasedate: date | None = None
    ttv_flag: bool | None = None

    def __post_init__(self) -> None:
        if not self.planet_name.strip():
            raise InvariantViolation("'planet_name' no puede estar vacío")
        if not self.reference.strip():
            raise InvariantViolation("'reference' no puede estar vacío")
        if not math.isfinite(self.value):
            raise InvariantViolation("'value' debe ser un número finito")
        if self.value <= 0:
            raise InvariantViolation("'value' debe ser mayor que cero")
        for name in ("err_plus", "err_minus"):
            err = getattr(self, name)
            if err is None:
                continue
            if not math.isfinite(err):
                raise InvariantViolation(f"'{name}' debe ser un número finito")
            if err < 0:
                raise InvariantViolation(f"'{name}' no puede ser negativo")
        if self.unit not in UNITS_BY_PARAMETER[self.parameter]:
            raise InvariantViolation(
                f"'unit' {self.unit.value!r} no es coherente con "
                f"'parameter' {self.parameter.value!r}"
            )

    @property
    def usable_as_prior(self) -> bool:
        """Utilizable como previa: valor puntual (no cota) y con los dos
        errores informados y estrictamente positivos (un error cero haría
        degenerar el denominador de σ)."""
        return (
            self.limit == MeasurementLimit.NONE
            and self.err_plus is not None
            and self.err_plus > 0
            and self.err_minus is not None
            and self.err_minus > 0
        )


@dataclass(frozen=True, slots=True)
class CatalogTensionComparison:
    """Una medida del paper frente a una previa del catálogo, con su σ."""

    paper: Measurement
    prior: CatalogSolution
    sigma: float


@dataclass(frozen=True, slots=True)
class CatalogTension:
    """Tensión de un parámetro de un planeta frente al catálogo (ADR 0012).

    Value object sin identidad: nace dentro de `Finding.catalog_tension`.
    `planet_name` es el nombre canónico del archivo y `archive_url` la ficha
    del planeta. `reference_sigma` es el σ frente a la previa por defecto.
    """

    planet_name: str
    parameter: MeasuredParameter
    archive_url: str
    threshold_sigma: float
    reference_sigma: float
    comparisons: tuple[CatalogTensionComparison, ...]

    def __post_init__(self) -> None:
        _require_non_empty(self.planet_name, "planet_name")
        _require_non_empty(self.archive_url, "archive_url")
        object.__setattr__(self, "comparisons", tuple(self.comparisons))
        if not self.comparisons:
            raise InvariantViolation("'comparisons' no puede estar vacía")
        if not math.isfinite(self.threshold_sigma) or self.threshold_sigma <= 0:
            raise InvariantViolation("'threshold_sigma' debe ser finito y mayor que cero")
        if not math.isfinite(self.reference_sigma) or self.reference_sigma < self.threshold_sigma:
            raise InvariantViolation("'reference_sigma' debe ser finito y >= 'threshold_sigma'")
        for comparison in self.comparisons:
            if not isinstance(comparison, CatalogTensionComparison):
                raise InvariantViolation(
                    "cada elemento de 'comparisons' debe ser CatalogTensionComparison"
                )
            if comparison.paper.parameter != self.parameter:
                raise InvariantViolation("'paper' debe medir el 'parameter' de la tensión")
            if comparison.prior.parameter != self.parameter:
                raise InvariantViolation("'prior' debe ser del 'parameter' de la tensión")
            if comparison.prior.planet_name != self.planet_name:
                raise InvariantViolation("'prior' debe ser del planeta de la tensión")
            if not comparison.paper.usable_for_tension:
                raise InvariantViolation("'paper' no es utilizable para tensión")
            if not comparison.prior.usable_as_prior:
                raise InvariantViolation("'prior' no es utilizable como previa")
            if not math.isfinite(comparison.sigma) or comparison.sigma < 0:
                raise InvariantViolation("'sigma' debe ser finito y no negativo")
        # Misma regla que `TensionResult.reference_sigma()` (domain/tension.py):
        # una única previa `is_default` (por igualdad de valor) y `reference_sigma`
        # igual al mínimo de los σ frente a ella. Comparación exacta: la factoría
        # copia el valor y el JSON de Python round-tripea los float sin pérdida.
        defaults = {c.prior for c in self.comparisons if c.prior.is_default}
        if len(defaults) != 1:
            raise InvariantViolation(
                "las comparaciones deben tener exactamente una previa 'is_default'"
            )
        (reference,) = defaults
        if self.reference_sigma != min(c.sigma for c in self.comparisons if c.prior == reference):
            raise InvariantViolation(
                "'reference_sigma' debe ser el mínimo de los σ frente a la previa por defecto"
            )


@dataclass(frozen=True, slots=True)
class Reading:
    """Salida del Reader para un `Item`: una llamada ya ocurrida.

    No tiene `created_at`: es un hecho inmutable, no un registro con su
    propio ciclo de vida.

    `measurements` distingue dos hechos distintos de una lectura (T71.c):
    `None` significa que esta lectura no se hizo con el prompt que extrae
    medidas (`reader-v2`, o un ítem fuera de las categorías con medidas de
    `pipeline.toml`); `()` significa que sí se hizo con ese prompt y no
    encontró ninguna medida utilizable en el abstract. Confundir ambos casos
    escondería, en el informe de la noche, la diferencia entre "no se buscó"
    y "se buscó y no había".
    """

    item_id: UUID
    summary: str
    objects: tuple[str, ...]
    claims: tuple[str, ...]
    interest_score: int
    tokens_in: int
    tokens_out: int
    model: str
    measurements: tuple[Measurement, ...] | None = None
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
        if self.measurements is not None:
            if not isinstance(self.measurements, tuple):
                raise InvariantViolation(
                    "'measurements' debe ser una tupla, o None si no se extrajo"
                )
            for measurement in self.measurements:
                if not isinstance(measurement, Measurement):
                    raise InvariantViolation("cada elemento de 'measurements' debe ser Measurement")


class FindingType(StrEnum):
    """Tipo de hallazgo publicado: explicación de un paper o tensión con el catálogo."""

    PAPER_EXPLAINED = "paper_explained"
    CATALOG_TENSION = "catalog_tension"


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
    catalog_tension: CatalogTension | None = None

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
        if (self.type == FindingType.CATALOG_TENSION) != (self.catalog_tension is not None):
            raise InvariantViolation(
                "'catalog_tension' debe informarse si y solo si 'type' es catalog_tension"
            )
        if self.catalog_tension is not None and not isinstance(
            self.catalog_tension, CatalogTension
        ):
            raise InvariantViolation("'catalog_tension' debe ser CatalogTension")
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

    `prompt_version` es nullable a propósito: las filas históricas del humo
    manual de T40 no llevan versión de prompt y no hay que inventárselas.
    T60 son dos semanas de calibración ajustando los prompts de
    `application/agents/prompts/`; sin este campo desde la primera noche que
    lo tiene, los datos de distintas versiones de un mismo prompt no son
    comparables entre sí.
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
    prompt_version: str | None = None

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
