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
from nocturna.domain.entities import AgentCall, Finding, Item, Reading, Run
from nocturna.domain.llm import AgentRole


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
        """Recupera la lectura de un ítem, si existe. Usado por T42."""
        ...

    def with_measurements(self) -> list[Reading]:
        """Lecturas con `measurements` extraídas (T74).

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

    def published_page(self, limit: int, offset: int) -> list[Finding]:
        """Página de hallazgos publicados, más recientes primero. Usado por T50."""
        ...

    def count_published(self) -> int:
        """Número total de hallazgos publicados, para paginar. Usado por T50."""
        ...

    def get_published(self, finding_id: UUID) -> Finding | None:
        """Recupera un hallazgo por id, solo si está publicado. Usado por T50."""
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
