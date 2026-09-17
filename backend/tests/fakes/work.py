"""`AgentWorkFactory` en memoria, para tests de casos de uso de agente (T41+).

Sustituye por completo `infrastructure/db/session.py::unit_of_work` (ADR
0003): ningún repositorio de este módulo toca PostgreSQL. `make_work_factory`
construye el `Callable[[], AbstractContextManager[AgentWork]]` que
`application/unit_of_work.py::AgentWorkFactory` describe, sin abrir ninguna
sesión real -- exactamente lo que pide `.claude/skills/testing-without-claude`
para probar `ReadItem` (T41) sin PostgreSQL.

Los cuatro repositorios en memoria (`Run`, `Item`, `Reading`, `AgentCall`)
siguen el mismo patrón que `_InMemoryRunRepository`/`_InMemoryAgentCallRepository`
de `tests/test_budget_guard.py`, pero viven aquí -- no en ese fichero --
porque `test_read_item.py` (y T42/T43 después, cuando llamen a
`PopularizeReading`/`EditNight`) los necesitan fuera de ese módulo de test.

No hay commit/rollback real: cada `with work() as w:` cede el MISMO
`AgentWork` (los mismos objetos en memoria, no copias). Eso es intencional:
`ReadItem` abre varias unidades de trabajo por ítem (una para `authorize`,
otra para `record_call`, ver el docstring de `application/unit_of_work.py`),
y todas deben ver el gasto que registró la anterior -- lo mismo que
garantizaría una sesión de PostgreSQL real contra la misma base entre
transacciones sucesivas. Ningún test de este módulo depende de que una
excepción a mitad de un bloque `with` deshaga una escritura ya hecha: eso es
responsabilidad de la implementación real de `unit_of_work`, fuera del
alcance de T41 paso 7.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from uuid import UUID

from nocturna.application.budget import BudgetGuard
from nocturna.application.unit_of_work import AgentWork, AgentWorkFactory
from nocturna.domain.entities import AgentCall, Item, ItemStatus, Reading, Run, RunStatus
from nocturna.domain.llm import AgentRole


class InMemoryRunRepository:
    """Mismo contrato que `_InMemoryRunRepository` de `test_budget_guard.py`."""

    def __init__(self, run: Run) -> None:
        self._runs: dict[UUID, Run] = {run.id: run}

    def add(self, run: Run) -> None:
        self._runs[run.id] = run

    def get(self, run_id: UUID) -> Run | None:
        return self._runs.get(run_id)

    def current(self) -> Run | None:
        return next((r for r in self._runs.values() if r.status is RunStatus.RUNNING), None)

    def save(self, run: Run) -> None:
        self._runs[run.id] = run


class InMemoryAgentCallRepository:
    """Mismo contrato que `_InMemoryAgentCallRepository` de `test_budget_guard.py`."""

    def __init__(self) -> None:
        self.calls: list[AgentCall] = []

    def add(self, call: AgentCall) -> None:
        self.calls.append(call)

    def tokens_used_for_run(self, run_id: UUID) -> int:
        return sum(call.total_tokens for call in self.calls if call.run_id == run_id)

    def count_for_run(self, run_id: UUID, agent: AgentRole) -> int:
        return sum(1 for call in self.calls if call.run_id == run_id and call.agent is agent)


class InMemoryItemRepository:
    """Repositorio de `Item` en memoria: suficiente para lo que `ReadItem` usa
    (`get`/`save`); `add_many`/`next_unread` se implementan igual, por si un
    test futuro (T42/T43, o T44 más adelante) los necesita, pero ningún test
    de T41 los ejercita.
    """

    def __init__(self, *items: Item) -> None:
        self._items: dict[UUID, Item] = {item.id: item for item in items}

    def add_many(self, items: list[Item]) -> int:
        added = 0
        for item in items:
            if item.id not in self._items:
                self._items[item.id] = item
                added += 1
        return added

    def get(self, item_id: UUID) -> Item | None:
        return self._items.get(item_id)

    def next_unread(self, limit: int) -> list[Item]:
        return [item for item in self._items.values() if item.status is ItemStatus.NEW][:limit]

    def save(self, item: Item) -> None:
        self._items[item.id] = item


class InMemoryReadingRepository:
    def __init__(self) -> None:
        self.readings: list[Reading] = []

    def add(self, reading: Reading) -> None:
        self.readings.append(reading)

    def get_for_item(self, item_id: UUID) -> Reading | None:
        return next((r for r in self.readings if r.item_id == item_id), None)


def make_work_factory(
    *,
    guard: BudgetGuard,
    runs: InMemoryRunRepository,
    items: InMemoryItemRepository,
    readings: InMemoryReadingRepository,
    agent_calls: InMemoryAgentCallRepository,
) -> AgentWorkFactory:
    """Construye el `AgentWorkFactory` en memoria que `ReadItem` espera.

    Cada llamada cede el mismo `AgentWork` (los mismos objetos en memoria,
    no copias por unidad de trabajo): ver el docstring del módulo.
    """

    @contextmanager
    def _work() -> Iterator[AgentWork]:
        yield AgentWork(
            guard=guard, runs=runs, items=items, readings=readings, agent_calls=agent_calls
        )

    return _work


def counting_work_factory(
    factory: AgentWorkFactory,
) -> tuple[AgentWorkFactory, list[int]]:
    """Envuelve `factory` para contar cuántas veces se abrió una unidad de trabajo.

    Solo para test 9 (`ítem no NEW`): probar que `ReadItem` no llega a abrir
    ninguna unidad de trabajo de gasto requiere algo más que mirar que no
    quedó rastro en los repositorios -- este contador es la prueba directa.
    `calls` es una lista de un elemento (`[0]`) en vez de un `int` suelto
    para que el test pueda leer el conteo tras la llamada sin depender de
    `nonlocal`.
    """
    calls: list[int] = [0]

    @contextmanager
    def _counting() -> Iterator[AgentWork]:
        calls[0] += 1
        with factory() as w:
            yield w

    return _counting, calls
