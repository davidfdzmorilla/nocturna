"""Puerto de unidad de trabajo para los casos de uso que llaman a un agente.

`domain/` no puede saber nada de SQLAlchemy, y sin embargo un caso de uso
como `ReadItem` (T41) necesita abrir transacciones de base de datos: la
solución es que `application/` defina el contrato (`AgentWork`,
`AgentWorkFactory`) y `infrastructure/`/`cli.py` -- el composition root, ver
T41 paso 8 -- lo implemente contra `infrastructure/db/session.py`. Este
módulo vive en `application/` y no en `domain/` porque describe una unidad
de trabajo de *aplicación* (qué repositorios y qué guarda de gasto necesita
un caso de uso concreto para hacer su trabajo), no una regla de negocio:
ninguna entidad ni invariante de dominio depende de que exista.

`AgentWork` agrupa exactamente lo que un caso de uso de agente (`ReadItem`,
T41; `PopularizeReading`, T42; `EditNight`, T43) necesita dentro de una
transacción: el `BudgetGuard` de esa noche y los cuatro repositorios que
puede tocar. No es un `UnitOfWork` genérico con `commit()`/`rollback()`
expuestos -- eso lo decide `unit_of_work` de `infrastructure/db/session.py`
(ADR 0003), la única frontera transaccional del proyecto -- sino el
contenido ya abierto de una de esas transacciones, listo para que el caso
de uso lo use y lo suelte.

## Por qué esto contradice `ddd-conventions/SKILL.md` § Unidad de trabajo

Ese skill dice «una sesión por ítem, commit al terminar». Con T41 esa
descripción queda obsoleta: ADR 0006 § 2 decide que la llamada al LLM ocurre
**fuera de toda transacción**, porque retener una conexión del pool de
PostgreSQL abierta hasta `item_timeout_s` (por defecto 180 s, ver
`config/pipeline.toml`) esperando la respuesta de un modelo es inaceptable
-- bloquea una conexión del pool para nada durante minutos, por cada ítem en
vuelo, cada noche. `AgentWorkFactory` es un `Callable` precisamente porque un
caso de uso de agente necesita poder pedir *varias* unidades de trabajo
independientes a lo largo de un solo `Item`: una para `authorize` antes de
llamar al LLM, otra para `record_call` después (ver `ReadItem` en
`use_cases/read_item.py`, que hereda el patrón de
`tests/manual/test_sdk_smoke.py`, la referencia que T40 dejó para T41-T44).
«Una sesión por ítem» ya no describe lo que ocurre: son varias sesiones por
ítem, ninguna de ellas viva mientras se espera al LLM. Queda para el
`docs-keeper` corregir el skill; esta nota documenta el motivo para que la
corrección no se pierda.
"""

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from nocturna.application.budget import BudgetGuard
from nocturna.domain.repositories import (
    AgentCallRepository,
    ItemRepository,
    ReadingRepository,
    RunRepository,
)


@dataclass(frozen=True, slots=True)
class AgentWork:
    """Contenido de una unidad de trabajo abierta para un caso de uso de agente.

    Cada campo es lo que ya existe en `domain/`: no se inventa ningún
    repositorio ni interfaz nueva aquí. `guard` es el `BudgetGuard` de la
    noche en curso (T30), ya construido contra la sesión de esta unidad de
    trabajo -- el caso de uso no lo construye, lo recibe.
    """

    guard: BudgetGuard
    runs: RunRepository
    items: ItemRepository
    readings: ReadingRepository
    agent_calls: AgentCallRepository


#: Fábrica de unidades de trabajo: cada llamada abre una transacción nueva
#: (una sesión SQLAlchemy en la implementación real, ver `cli.py`) y la cede
#: como `AgentWork` a través de un gestor de contexto que confirma al salir
#: sin excepción y deshace ante cualquier excepción -- el mismo contrato que
#: `infrastructure/db/session.py::unit_of_work`, pero sin que `application/`
#: importe `Session` para poder describirlo.
AgentWorkFactory = Callable[[], AbstractContextManager[AgentWork]]
