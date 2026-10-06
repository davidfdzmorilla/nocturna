"""Interfaces de repositorio del dominio.

Cada método lleva el nombre del caso de uso que lo necesita, no es CRUD
genérico. Las implementaciones (SQLAlchemy) viven en `infrastructure/db/` y
traducen entidad ↔ modelo ORM; el ORM no cruza hacia `application/`. Todos
los métodos son síncronos: la unidad de trabajo por ítem (una sesión, commit
al terminar) es cosa de `infrastructure/` y `application/`, no de esta
interfaz.
"""

from collections.abc import Collection, Sequence
from typing import Protocol
from uuid import UUID

from nocturna.domain.archive import ArchiveSnapshot, ArchiveSolution, SnapshotDiff
from nocturna.domain.entities import AgentCall, Finding, FindingType, Item, Reading, Run
from nocturna.domain.llm import AgentRole
from nocturna.domain.tension import TensionEvaluation


class ItemRepository(Protocol):
    """Persistencia de `Item`."""

    def add_many(self, items: list[Item]) -> int:
        """Inserta ítems nuevos, deduplicando por `(source, external_id)`. Usado por T20."""
        ...

    def get(self, item_id: UUID) -> Item | None:
        """Recupera un ítem por id. Usado por T41."""
        ...

    def next_unread(self, limit: int) -> list[Item]:
        """Ítems en estado `NEW` a procesar esta noche, hasta `limit`. Usado por T44."""
        ...

    def save(self, item: Item) -> None:
        """Persiste el estado actual del ítem. Usado por T41/T42/T43."""
        ...


class ReadingRepository(Protocol):
    """Persistencia de `Reading`."""

    def add(self, reading: Reading) -> None:
        """Inserta la lectura producida por el Reader. Usado por T41."""
        ...

    def get_for_item(self, item_id: UUID) -> Reading | None:
        """Recupera la lectura *vigente* de un ítem, si existe. Usado por T42.

        Una lectura sustituida (T82, `supersede`) no se devuelve nunca.
        """
        ...

    def supersede(self, previous_id: UUID, reading: Reading) -> None:
        """Marca `previous_id` como sustituida y añade `reading` como vigente (T82).

        Ambas cosas ocurren en la misma unidad de trabajo. La lectura previa se
        conserva. `reading.item_id` debe ser el de la previa. Lanza
        `InvariantViolation` si `previous_id` no existe, ya no es vigente o
        pertenece a otro ítem; en ese caso no se añade nada.
        """
        ...

    def with_measurements(self) -> list[Reading]:
        """Lecturas *vigentes* con `measurements` extraídas (T74).

        Incluye las de lista vacía (`()`, "se buscó y no había medidas") y
        excluye las de `None` (SQL NULL, "no extraído"). Orden determinista
        por `id`.
        """
        ...


class FindingRepository(Protocol):
    """Persistencia de `Finding`."""

    def add(self, finding: Finding) -> None:
        """Inserta un hallazgo candidato, aún no publicado. Usado por T42."""
        ...

    def unpublished_for_run(self, run_id: UUID) -> list[Finding]:
        """Candidatos de la noche pendientes de decisión del Editor. Usado por T43."""
        ...

    def save(self, finding: Finding) -> None:
        """Persiste un hallazgo tras la decisión del Editor. Usado por T43."""
        ...

    def published_page(
        self, limit: int, offset: int, *, finding_type: FindingType | None = None
    ) -> list[Finding]:
        """Página de hallazgos publicados, más recientes primero. Usado por T50.

        `finding_type` restringe a un tipo (T77); `None` los devuelve todos.
        """
        ...

    def count_published(self, *, finding_type: FindingType | None = None) -> int:
        """Número total de hallazgos publicados, para paginar. Usado por T50.

        Mismo filtro `finding_type` que `published_page` (T77).
        """
        ...

    def get_published(self, finding_id: UUID) -> Finding | None:
        """Recupera un hallazgo por id, solo si está publicado. Usado por T50."""
        ...

    def evaluation_ids_with_finding(self, type: FindingType) -> frozenset[UUID]:
        """Ids de `TensionEvaluation` que ya tienen un `Finding` de ese tipo.

        Publicado o no: el índice único `(tension_evaluation_id, type)` impide
        regenerarlo. Usado por T89.
        """
        ...

    def count_for_run(self, run_id: UUID, types: Collection[FindingType]) -> int:
        """Número de `Finding` del run con alguno de esos tipos, publicados o no.
        Usado por T89 para que el tope de candidatos sea por Run."""
        ...


class RunRepository(Protocol):
    """Persistencia de `Run`."""

    def add(self, run: Run) -> None:
        """Inserta el Run al empezar la noche. Usado por T44."""
        ...

    def get(self, run_id: UUID) -> Run | None:
        """Recupera un Run por id. Usado por T30."""
        ...

    def current(self) -> Run | None:
        """Recupera el Run en estado `RUNNING`, para recuperarse tras un reinicio. Usado por T30."""
        ...

    def save(self, run: Run) -> None:
        """Persiste el estado actual del Run. Usado por T30/T44."""
        ...


class AgentCallRepository(Protocol):
    """Persistencia de `AgentCall`."""

    def add(self, call: AgentCall) -> None:
        """Inserta el registro de una llamada a un agente. Usado por T41/T42/T43."""
        ...

    def tokens_used_for_run(self, run_id: UUID) -> int:
        """Suma de tokens consumidos en el Run, leída de la BD. Usado por T30."""
        ...

    def count_for_run(self, run_id: UUID, agent: AgentRole) -> int:
        """Número de llamadas de un rol en el Run, para el límite del Editor. Usado por T30."""
        ...

    def count_runs_with_prompt_version(self, prompt_version: str) -> int:
        """Número de Runs distintos con al menos una llamada de esa `prompt_version` (T74)."""
        ...


class ArchiveRepository(Protocol):
    """Persistencia del snapshot del Exoplanet Archive (T81)."""

    def last_snapshot(self) -> ArchiveSnapshot | None:
        """Último snapshot, completo o incremental."""
        ...

    def last_full_snapshot(self) -> ArchiveSnapshot | None:
        """Último snapshot completo."""
        ...

    def active_keys(self, planets: Collection[str] | None = None) -> dict[str, str]:
        """clave -> pl_name de las activas (sin `removed_at`); filtra por planetas si se pasan."""
        ...

    def removed_keys(self, keys: Collection[str]) -> frozenset[str]:
        """Subconjunto de `keys` que existe en la base con `removed_at`."""
        ...

    def current_defaults(self) -> dict[str, str]:
        """pl_name -> clave de la solución por defecto vigente."""
        ...

    def save_snapshot(
        self,
        snapshot: ArchiveSnapshot,
        solutions: Sequence[ArchiveSolution],
        diff: SnapshotDiff,
    ) -> None:
        """Persiste snapshot, soluciones y diff en una sola transacción."""
        ...

    def planet_names(self) -> frozenset[str]:
        """`pl_name` de los planetas con alguna solución activa."""
        ...

    def active_solutions(self, pl_name: str) -> list[tuple[ArchiveSolution, bool]]:
        """Soluciones activas del planeta, cada una con `is_default_current`."""
        ...


class TensionEvaluationRepository(Protocol):
    """Persistencia de `TensionEvaluation` (T88)."""

    def all(self) -> list[TensionEvaluation]:
        """Todas las evaluaciones."""
        ...

    def add(self, evaluation: TensionEvaluation) -> None:
        """Inserta una evaluación nueva."""
        ...

    def update(self, evaluation: TensionEvaluation) -> None:
        """Sustituye la evaluación con el mismo `id`."""
        ...
