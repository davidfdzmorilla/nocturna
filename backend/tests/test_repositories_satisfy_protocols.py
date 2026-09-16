"""Las implementaciones SQLAlchemy cumplen estructuralmente sus `Protocol`.

`domain/repositories.py` define interfaces con `typing.Protocol`, sin
`@runtime_checkable`, así que `isinstance()` no sirve para comprobarlas en
tiempo de ejecución. En su lugar: cada implementación se asigna a una
variable anotada con su `Protocol` (para que un `mypy`/`pyright` real
también lo comprobara) y, en tiempo de ejecución, se compara la firma de
cada método declarado en el `Protocol` con la de la implementación vía
`inspect.signature`, nombre a nombre y parámetro a parámetro.

Sin base de datos: no hace falta persistir nada, solo inspeccionar clases.
Vive fuera de `tests/db/` a propósito, aunque documenta el contrato de los
mismos repositorios que `tests/db/` ejercita contra PostgreSQL: todo lo que
cuelga de `tests/db/` se marca automáticamente con `db` (ver su
`conftest.py`), y ese marcador significa "necesita el PostgreSQL de
compose". Este fichero no lo necesita.
"""

from __future__ import annotations

import inspect
from typing import Protocol, get_type_hints

from nocturna.domain.repositories import (
    AgentCallRepository,
    FindingRepository,
    ItemRepository,
    ReadingRepository,
    RunRepository,
)
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)


def _protocol_methods(protocol_cls: type[Protocol]) -> list[str]:
    return [
        name
        for name, value in vars(protocol_cls).items()
        if not name.startswith("_") and inspect.isfunction(value)
    ]


def _assert_same_signature(protocol_cls: type, impl_cls: type, method_name: str) -> None:
    protocol_method = getattr(protocol_cls, method_name)
    impl_method = getattr(impl_cls, method_name)

    protocol_params = list(inspect.signature(protocol_method).parameters)
    impl_params = list(inspect.signature(impl_method).parameters)
    assert protocol_params == impl_params, (
        f"{impl_cls.__name__}.{method_name} tiene parámetros {impl_params}, "
        f"se esperaban {protocol_params} (los de {protocol_cls.__name__})"
    )

    # Comparación de anotaciones resueltas (no strings), para no depender
    # de `from __future__ import annotations` en un lado sí y en el otro no.
    protocol_hints = get_type_hints(protocol_method)
    impl_hints = get_type_hints(impl_method)
    for param_name in protocol_params:
        if param_name == "self":
            continue
        assert impl_hints.get(param_name) == protocol_hints.get(param_name), (
            f"{impl_cls.__name__}.{method_name}: el parámetro '{param_name}' no "
            "coincide en tipo con el Protocol"
        )
    assert impl_hints.get("return") == protocol_hints.get("return"), (
        f"{impl_cls.__name__}.{method_name}: el tipo de retorno no coincide con el Protocol"
    )


def _assert_conforms(protocol_cls: type, impl_cls: type) -> None:
    methods = _protocol_methods(protocol_cls)
    assert methods, f"{protocol_cls.__name__} no declaró ningún método; revisa el test"
    for method_name in methods:
        assert hasattr(impl_cls, method_name), (
            f"{impl_cls.__name__} no implementa '{method_name}', requerido por "
            f"{protocol_cls.__name__}"
        )
        _assert_same_signature(protocol_cls, impl_cls, method_name)


# `session=None`: la conformidad estructural con el `Protocol` es cosa de
# clases y firmas, no de comportamiento; no hace falta una sesión real ni
# PostgreSQL levantado para comprobarla.
def test_sqlalchemy_item_repository_cumple_item_repository() -> None:
    repo: ItemRepository = SqlAlchemyItemRepository(None)
    _assert_conforms(ItemRepository, type(repo))


def test_sqlalchemy_reading_repository_cumple_reading_repository() -> None:
    repo: ReadingRepository = SqlAlchemyReadingRepository(None)
    _assert_conforms(ReadingRepository, type(repo))


def test_sqlalchemy_finding_repository_cumple_finding_repository() -> None:
    repo: FindingRepository = SqlAlchemyFindingRepository(None)
    _assert_conforms(FindingRepository, type(repo))


def test_sqlalchemy_run_repository_cumple_run_repository() -> None:
    repo: RunRepository = SqlAlchemyRunRepository(None)
    _assert_conforms(RunRepository, type(repo))


def test_sqlalchemy_agent_call_repository_cumple_agent_call_repository() -> None:
    repo: AgentCallRepository = SqlAlchemyAgentCallRepository(None)
    _assert_conforms(AgentCallRepository, type(repo))
