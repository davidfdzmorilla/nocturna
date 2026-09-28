"""Tests de contrato (marcador `db`) de `load_ep_readings` en
`backend/scripts/exoplanet_viability.py` (T71).

`load_ep_readings(session)` es la única función del script que toca
PostgreSQL de verdad: recorre los `Item` de categoría `astro-ph.EP` que ya
tienen `Reading`, junto con el conjunto de `run_id` (vía `AgentCall`,
`agent=reader`) que los leyó, sin pasar por `application/` ni por
`LLMProvider`. Vive en `tests/db/` porque necesita PostgreSQL real (mismo
motivo que el resto de `tests/db/`: la migración es la que corre en
producción, no `Base.metadata.create_all`), y en un fichero propio (no
dentro de `test_reading_repository.py` ni de ningún repositorio existente)
porque no consume ningún `Repository` de `domain/repositories.py` -- lee
directamente `AgentCallRow` para obtener `run_id`, algo que ningún
repositorio expone hoy (`SqlAlchemyAgentCallRepository` no tiene un método
"llamadas del Reader para un ítem").

Precedente de patrón (subproceso + `sys.modules` limpio) para "sin
Claude": `tests/db/test_cli_dry_run_db.py::
test_claude_agent_sdk_no_se_importa_durante_dry_run`. Aquí no se repite en
subproceso porque el propio `tests/test_exoplanet_viability_script.py`
(sin BD) ya congela esa propiedad para todo el módulo del script; este
fichero solo prueba lo que un test sin BD no puede probar: el cruce con
PostgreSQL real.

## Decisiones de forma para este test

- **Filtro de categoría**: "solo EP" se interpreta como "`astro-ph.EP` está
  en `Item.categories`", no como "`Item.categories == ['astro-ph.EP']`":
  un ítem con `["astro-ph.EP", "astro-ph.GA"]` (cross-listado) se incluye.
  Ver `test_incluye_items_cross_listados_ep_y_ga`.
- **Exclusión de siembra de demo**: `external_id` que empieza por
  `"9999."` es el prefijo que usa `scripts/seed_demo.py`
  (`_DemoFinding.external_id`); `load_ep_readings` debe excluirlo aunque
  tenga categoría EP y `Reading`, para que el experimento de T71 no cuente
  hallazgos de mentira.
- **`run_ids`**: `frozenset[UUID]` de los `run_id` de `AgentCall` con
  `agent="reader"` para ese `item_id`, cualquiera que sea su `status` (una
  llamada que gastó tokens gastó tokens, esté `ok` o no) -- mismo criterio
  que ya usa `tokens_used_for_run` en `repositories.py`, aplicado aquí a
  nivel de ítem en vez de nivel de run. Llamadas de otro `agent` (por
  ejemplo `popularizer`) sobre el mismo ítem no cuentan.
- **Transacción de solo lectura**: `load_ep_readings` debe dejar la
  sesión recibida en modo `SET TRANSACTION READ ONLY` (verificado
  empíricamente contra el PostgreSQL de `docker compose`: el comando
  toma efecto de inmediato para el resto de la transacción en curso,
  aunque ya se hayan ejecutado otras instrucciones antes -- a diferencia
  del nivel de aislamiento, el modo lectura/escritura no exige ser lo
  primero de la transacción). Cualquier intento posterior de escribir en
  esa misma sesión debe fallar.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import sqlalchemy as sa
from factories import aware, make_agent_call, make_item, make_reading, make_run

from nocturna.domain.entities import RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyAgentCallRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyReadingRepository,
    SqlAlchemyRunRepository,
)

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "exoplanet_viability.py"


def _load_script() -> ModuleType:
    if not SCRIPT_PATH.exists():
        pytest.fail(
            f"{SCRIPT_PATH} no existe: se ha borrado o movido. Este fichero fija el "
            "contrato de `load_ep_readings` contra ese script (T71); restaura el fichero."
        )
    spec = importlib.util.spec_from_file_location("exoplanet_viability", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["exoplanet_viability"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def script() -> ModuleType:
    return _load_script()


def _seed_ep_item_with_reading(db_session, *, external_id: str, categories: list[str]):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    runs = SqlAlchemyRunRepository(db_session)
    calls = SqlAlchemyAgentCallRepository(db_session)

    item = make_item(external_id=external_id, categories=categories)
    items.add_many([item])
    db_session.flush()

    reading = make_reading(
        item_id=item.id,
        objects=["WASP-12 b"],
        claims=["El planeta muestra una masa de 300 masas terrestres."],
    )
    readings.add(reading)

    # El índice único parcial `uq_runs_status_running` solo admite un Run
    # `running` a la vez; como este helper puede llamarse varias veces en
    # el mismo test, cada Run se cierra de inmediato (mismo patrón que
    # `tests/db/test_budget_source_of_truth.py`).
    run = make_run(started_at=aware())
    run.finish(RunStatus.COMPLETED, at=run.started_at)
    runs.add(run)
    db_session.flush()

    call = make_agent_call(run_id=run.id, item_id=item.id, agent=AgentRole.READER)
    calls.add(call)
    db_session.flush()

    return item, reading, run


def test_solo_devuelve_items_ep_con_reading(db_session, script):
    item_ep, reading_ep, run_ep = _seed_ep_item_with_reading(
        db_session, external_id="2601.10001", categories=["astro-ph.EP"]
    )

    # GA-only con Reading: excluido.
    _seed_ep_item_with_reading(db_session, external_id="2601.10002", categories=["astro-ph.GA"])

    # EP sin Reading: excluido (no se le añade Reading ni AgentCall).
    items = SqlAlchemyItemRepository(db_session)
    ep_sin_reading = make_item(external_id="2601.10003", categories=["astro-ph.EP"])
    items.add_many([ep_sin_reading])
    db_session.flush()

    # Siembra de demo (prefijo 9999.), EP y con Reading: excluida a propósito.
    _seed_ep_item_with_reading(db_session, external_id="9999.00099", categories=["astro-ph.EP"])

    result = script.load_ep_readings(db_session)

    assert [r.external_id for r in result] == ["2601.10001"]
    (ep_reading,) = result
    assert ep_reading.title == item_ep.title
    assert ep_reading.abstract == item_ep.abstract
    assert ep_reading.objects == tuple(reading_ep.objects)
    assert ep_reading.claims == tuple(reading_ep.claims)
    assert ep_reading.run_ids == frozenset({run_ep.id})


def test_incluye_items_cross_listados_ep_y_ga(db_session, script):
    _seed_ep_item_with_reading(
        db_session, external_id="2601.10010", categories=["astro-ph.EP", "astro-ph.GA"]
    )

    result = script.load_ep_readings(db_session)

    assert [r.external_id for r in result] == ["2601.10010"]


def test_excluye_prefijo_9999_aunque_sea_ep_con_reading(db_session, script):
    _seed_ep_item_with_reading(db_session, external_id="9999.00001", categories=["astro-ph.EP"])

    result = script.load_ep_readings(db_session)

    assert result == []


def test_run_ids_agrupa_solo_llamadas_del_reader_para_ese_item(db_session, script):
    items = SqlAlchemyItemRepository(db_session)
    readings = SqlAlchemyReadingRepository(db_session)
    runs = SqlAlchemyRunRepository(db_session)
    calls = SqlAlchemyAgentCallRepository(db_session)

    item = make_item(external_id="2601.10020", categories=["astro-ph.EP"])
    items.add_many([item])
    db_session.flush()

    reading = make_reading(item_id=item.id)
    readings.add(reading)

    # El índice único parcial `uq_runs_status_running` solo admite un Run
    # `running` a la vez; los tres Runs de este test se cierran de
    # inmediato para poder coexistir (mismo patrón que
    # `tests/db/test_budget_source_of_truth.py`).
    run_a = make_run(started_at=aware())
    run_b = make_run(started_at=aware(10))
    run_a.finish(RunStatus.COMPLETED, at=run_a.started_at)
    run_b.finish(RunStatus.COMPLETED, at=run_b.started_at)
    runs.add(run_a)
    runs.add(run_b)
    db_session.flush()

    # Dos llamadas del Reader para el mismo ítem, en dos runs distintos:
    # ambos run_id deben aparecer en el frozenset.
    calls.add(make_agent_call(run_id=run_a.id, item_id=item.id, agent=AgentRole.READER))
    calls.add(make_agent_call(run_id=run_b.id, item_id=item.id, agent=AgentRole.READER))
    # Llamada del Popularizer para el mismo ítem, otro run: no debe contarse.
    run_c = make_run(started_at=aware(20))
    run_c.finish(RunStatus.COMPLETED, at=run_c.started_at)
    runs.add(run_c)
    db_session.flush()
    calls.add(make_agent_call(run_id=run_c.id, item_id=item.id, agent=AgentRole.POPULARIZER))
    db_session.flush()

    (result,) = script.load_ep_readings(db_session)

    assert result.run_ids == frozenset({run_a.id, run_b.id})


def test_load_ep_readings_deja_la_transaccion_en_solo_lectura(db_session, script):
    _seed_ep_item_with_reading(db_session, external_id="2601.10030", categories=["astro-ph.EP"])

    script.load_ep_readings(db_session)

    # `db_session.add(intruso)` fallaría con `UnmappedInstanceError` --
    # `Item` es una entidad de dominio, no una entidad mapeada de
    # SQLAlchemy. Se escribe a través del repositorio, como el resto de
    # la suite.
    intruso = make_item(external_id="2601.10031", categories=["astro-ph.EP"])
    items = SqlAlchemyItemRepository(db_session)
    with pytest.raises(sa.exc.DBAPIError, match="(?i)read.only"):
        items.add_many([intruso])
        db_session.flush()


def test_load_ep_readings_no_devuelve_items_sin_reading_aunque_haya_agent_call(db_session, script):
    """Un `AgentCall` de Reader puede existir para un ítem que terminó en
    `failed` (JSON inválido agotando reintentos, ver `ItemStatus.FAILED`):
    sin `Reading` persistido, ese ítem no puede producir un `EpReading` --
    no hay `objects`/`claims` que cruzar."""
    items = SqlAlchemyItemRepository(db_session)
    runs = SqlAlchemyRunRepository(db_session)
    calls = SqlAlchemyAgentCallRepository(db_session)

    item = make_item(external_id="2601.10040", categories=["astro-ph.EP"])
    items.add_many([item])
    db_session.flush()

    run = make_run(started_at=aware())
    runs.add(run)
    db_session.flush()
    calls.add(make_agent_call(run_id=run.id, item_id=item.id, agent=AgentRole.READER))
    db_session.flush()

    result = script.load_ep_readings(db_session)

    assert result == []
