"""Tests de la API de lectura (`GET /findings`, `GET /findings/{id}`,
`GET /health`) contra PostgreSQL real (T50 paso 5).

Vive en `tests/db/` (no en `tests/`) porque necesita filas reales para
distinguir un `Finding` publicado de uno que no lo está -- justo el caso
que más importa de toda la tarea (§ 1 más abajo). Hereda de
`tests/db/conftest.py` la marca automática `db` y la fixture `db_session`
(rollback garantizado al final de cada test, ver su docstring).

## Por qué `httpx.AsyncClient` + `httpx.ASGITransport`, no `fastapi.testclient.TestClient`

`httpx.ASGITransport` (a diferencia de `httpx.HTTPTransport`/
`httpx.AsyncHTTPTransport`, los dos que `tests/conftest.py::_no_network`
parchea para bloquear tráfico real) hereda directamente de
`httpx.AsyncBaseTransport`: no es el transporte real que sale a Internet,
así que la guarda antirred de la suite no lo alcanza -- comprobado: si lo
alcanzara, cada test de este fichero fallaría con
`RuntimeError("test intentando salir a la red")` en la primera petición, y
no es lo que ocurre. `starlette.testclient.TestClient` habría funcionado
igual de bien (su transporte interno, `_TestClientTransport`, hereda de
`httpx.BaseTransport` directamente, con el mismo resultado), pero
construir el cliente a mano sobre `ASGITransport` deja explícito, sin tener
que leer el código de Starlette, por qué esta guarda no bloquea las
peticiones de este fichero.

## `app.dependency_overrides[get_session]`, no `settings=...`

`create_app(settings=...)` solo alimenta CORS (ver docstring de
`api/deps.py` y `OPEN_DECISIONS.md`): las dependencias de base de datos
usan el `Settings` cacheado de `deps.py`, no el que se pase a `create_app`.
Para inyectar `nocturna_test` (vía `db_session`) o una base inalcanzable
(§ 7), el único punto de inyección es `app.dependency_overrides[get_session]`
-- por eso todos los clientes de este fichero se construyen así, nunca
pasando `settings` a `create_app`.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import sqlalchemy as sa
from factories import make_finding, make_item, make_run
from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from nocturna.api.app import create_app
from nocturna.api.deps import get_session
from nocturna.api.routes import findings
from nocturna.domain.entities import Finding, RunStatus
from nocturna.infrastructure.config import Settings
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyFindingRepository,
    SqlAlchemyItemRepository,
    SqlAlchemyRunRepository,
)

PUBLISHED_AT = datetime(2026, 3, 2, 12, 0, 0, tzinfo=UTC)


def _seed_item_and_run(db_session: Session):
    """Mismo helper que `tests/db/test_finding_repository.py`: un `Finding`
    necesita un `Item` y un `Run` existentes por FK.

    El `Run` se cierra a `COMPLETED` de inmediato: el índice único parcial
    de `runs` solo permite un `Run` en estado `running` a la vez (ver
    `test_finding_repository.py`), y varios tests de este fichero siembran
    más de un `Item`/`Run` para tener varios `Finding`. El estado del `Run`
    no es lo que estos tests comprueban, así que cerrarlo aquí evita el
    conflicto sin distraer a cada llamante con esa decisión.
    """
    items = SqlAlchemyItemRepository(db_session)
    runs = SqlAlchemyRunRepository(db_session)
    # `external_id` por defecto de `make_item()` es fijo
    # ("2601.00001"): con `(source, external_id)` único, un segundo
    # `add_many([item])` en el mismo test lo insertaría vía
    # `ON CONFLICT DO NOTHING` -- silenciosamente, sin fallar -- dejando el
    # `item.id` en memoria sin fila real detrás, y el `INSERT` de `findings`
    # que lo referencia falla por FK más adelante. Un `external_id` nuevo
    # por llamada evita el conflicto.
    item = make_item(external_id=f"2601.{uuid4().hex[:5]}")
    run = make_run()
    run.finish(RunStatus.COMPLETED, at=run.started_at)
    items.add_many([item])
    runs.add(run)
    db_session.flush()
    return item, run


def _seed_published_finding(
    db_session: Session, *, published_at: datetime = PUBLISHED_AT, **overrides: object
) -> Finding:
    item, run = _seed_item_and_run(db_session)
    finding = make_finding(item_id=item.id, run_id=run.id, **overrides)
    finding.publish(confidence=0.8, at=published_at)
    SqlAlchemyFindingRepository(db_session).add(finding)
    db_session.flush()
    return finding


def _seed_unpublished_finding(db_session: Session, **overrides: object) -> Finding:
    item, run = _seed_item_and_run(db_session)
    finding = make_finding(item_id=item.id, run_id=run.id, **overrides)
    SqlAlchemyFindingRepository(db_session).add(finding)
    db_session.flush()
    return finding


@pytest.fixture
async def client(db_session: Session) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Cliente ASGI en proceso, con `get_session` apuntando a `db_session`.

    Nunca cierra `db_session` (ni hace `commit`/`rollback` sobre ella): eso
    es cosa de la propia fixture `db_session`, que garantiza el rollback al
    terminar el test con independencia de lo que haga este override.
    """
    app = create_app()

    def _override_get_session() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


def _unreachable_session_factory() -> sessionmaker[Session]:
    """Fábrica de sesión ligada a un puerto local que nadie escucha.

    `connect_timeout=2` evita que la comprobación se quede colgada más de
    lo necesario; el puerto `1` en `localhost` rechaza la conexión casi de
    inmediato (`ECONNREFUSED`) en cualquier máquina, sin depender de que
    exista o no un firewall externo.
    """
    bad_url = make_url(Settings().database_url).set(port=1).render_as_string(hide_password=False)
    engine = create_engine(
        bad_url, connect_args={"connect_timeout": 2, "options": "-c timezone=UTC"}
    )
    return sessionmaker(bind=engine)


@pytest.fixture
async def unreachable_db_client() -> AsyncGenerator[httpx.AsyncClient, None]:
    factory = _unreachable_session_factory()

    def _override_get_session() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app = create_app()
    app.dependency_overrides[get_session] = _override_get_session

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


# --- 1. Un Finding no publicado NO aparece en GET /findings -----------------


@pytest.mark.anyio
async def test_hallazgo_no_publicado_no_aparece_en_listado(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    """El caso que más importa de toda la tarea: un candidato que el Editor
    no ha aprobado (o ha rechazado) no puede filtrar a la web."""
    _seed_unpublished_finding(db_session, title="candidato sin decidir")
    published = _seed_published_finding(db_session, title="publicado de verdad")

    response = await client.get("/findings")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [str(published.id)]
    assert body["total"] == 1


# --- 2. 404 idéntico para no publicado y para id inexistente ---------------


@pytest.mark.anyio
async def test_hallazgo_no_publicado_devuelve_404_identico_a_uuid_inexistente(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    """Distinguir los dos casos confirmaría a un tercero la existencia de un
    candidato que el Editor rechazó, y permitiría enumerarlos probando ids
    uno a uno (ver docstring de `api/routes/findings.py::get_finding`)."""
    unpublished = _seed_unpublished_finding(db_session, title="candidato rechazado")
    missing_id = uuid4()
    assert missing_id != unpublished.id

    response_unpublished = await client.get(f"/findings/{unpublished.id}")
    response_missing = await client.get(f"/findings/{missing_id}")

    assert response_unpublished.status_code == 404
    assert response_missing.status_code == 404
    assert response_unpublished.content == response_missing.content
    assert dict(response_unpublished.headers) == dict(response_missing.headers)
    assert response_unpublished.json() == {"detail": "finding not found"}


# --- 3. Orden published_at DESC con desempate estable por id ---------------


@pytest.mark.anyio
async def test_listado_ordena_por_published_at_desc_con_desempate_estable_por_id(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    """El Editor publica el lote entero de la noche en el mismo instante: los
    empates de `published_at` son la norma, no la excepción."""
    tied = [
        _seed_published_finding(db_session, title=f"empatado {i}", published_at=PUBLISHED_AT)
        for i in range(3)
    ]
    earlier = _seed_published_finding(
        db_session, title="más antiguo", published_at=PUBLISHED_AT - timedelta(days=60)
    )
    expected_order = [str(fid) for fid in sorted((f.id for f in tied), reverse=True)] + [
        str(earlier.id)
    ]

    full = await client.get("/findings", params={"size": 10})
    assert [item["id"] for item in full.json()["items"]] == expected_order

    page_1 = await client.get("/findings", params={"page": 1, "size": 2})
    page_2 = await client.get("/findings", params={"page": 2, "size": 2})
    combined = [item["id"] for item in page_1.json()["items"]] + [
        item["id"] for item in page_2.json()["items"]
    ]
    assert combined == expected_order, (
        "dos páginas consecutivas no deben repetir ni omitir filas de la misma noche"
    )


# --- 4. page/size por defecto, segunda página, total, página fuera de rango -


@pytest.mark.anyio
async def test_listado_usa_page_1_y_size_20_por_defecto(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    _seed_published_finding(db_session, title="único hallazgo")

    response = await client.get("/findings")

    body = response.json()
    assert body["page"] == 1
    assert body["size"] == 20
    assert body["total"] == 1


@pytest.mark.anyio
async def test_listado_segunda_pagina_y_total_son_correctos(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    findings = [
        _seed_published_finding(
            db_session,
            title=f"hallazgo {i}",
            published_at=PUBLISHED_AT - timedelta(minutes=i),
        )
        for i in range(5)
    ]
    expected_order = [str(f.id) for f in findings]  # ya en orden published_at DESC

    page_2 = await client.get("/findings", params={"page": 2, "size": 2})

    body = page_2.json()
    assert body["total"] == 5
    assert [item["id"] for item in body["items"]] == expected_order[2:4]


@pytest.mark.anyio
async def test_listado_pagina_fuera_de_rango_devuelve_200_con_lista_vacia_y_total_real(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    _seed_published_finding(db_session, title="único hallazgo")

    response = await client.get("/findings", params={"page": 999, "size": 20})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 1


# --- 5. Validación de query params y de finding_id -------------------------


@pytest.mark.anyio
@pytest.mark.parametrize("params", [{"size": 51}, {"size": 0}, {"page": 0}])
async def test_listado_rechaza_parametros_fuera_de_rango_con_422(
    client: httpx.AsyncClient, params: dict[str, int]
) -> None:
    response = await client.get("/findings", params=params)

    assert response.status_code == 422


@pytest.mark.anyio
async def test_detalle_con_finding_id_que_no_es_uuid_devuelve_422(
    client: httpx.AsyncClient,
) -> None:
    response = await client.get("/findings/no-es-un-uuid")

    assert response.status_code == 422


# --- 6. Detalle de un publicado: tres niveles + source_url ------------------


@pytest.mark.anyio
async def test_detalle_de_publicado_devuelve_los_tres_niveles_y_source_url(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    item, run = _seed_item_and_run(db_session)
    finding = make_finding(
        item_id=item.id,
        run_id=run.id,
        title="Un hallazgo completo",
        level_curious="Nivel curioso.",
        level_amateur="Nivel aficionado.",
        level_technical="Nivel técnico.",
    )
    finding.publish(confidence=0.9, at=PUBLISHED_AT)
    SqlAlchemyFindingRepository(db_session).add(finding)
    db_session.flush()

    response = await client.get(f"/findings/{finding.id}")

    assert response.status_code == 200
    body = response.json()
    assert body["level_curious"] == "Nivel curioso."
    assert body["level_amateur"] == "Nivel aficionado."
    assert body["level_technical"] == "Nivel técnico."
    assert body["source_url"] == f"https://arxiv.org/abs/{item.external_id}"
    assert "confidence" not in body
    assert "run_id" not in body
    assert "item_id" not in body


# --- 7. GET /health: BD viva vs BD inalcanzable -----------------------------


@pytest.mark.anyio
async def test_health_con_base_de_datos_viva_devuelve_200(client: httpx.AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "ok"}


@pytest.mark.anyio
async def test_health_con_base_de_datos_inalcanzable_devuelve_503_sin_detalle_de_excepcion(
    unreachable_db_client: httpx.AsyncClient,
) -> None:
    response = await unreachable_db_client.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "unreachable"}
    lowered = response.text.lower()
    for leaked_term in ("traceback", "psycopg", "operationalerror", "connection refused", "5433"):
        assert leaked_term not in lowered, f"el cuerpo de /health filtró detalle: '{leaked_term}'"


# --- 8. CORS: solo el origen configurado recibe la cabecera -----------------


@pytest.mark.anyio
async def test_cors_permite_localhost_3000_y_no_otros_origenes(
    client: httpx.AsyncClient,
) -> None:
    allowed = await client.get("/findings", headers={"Origin": "http://localhost:3000"})
    other = await client.get("/findings", headers={"Origin": "http://evil.example"})

    assert allowed.headers.get("access-control-allow-origin") == "http://localhost:3000"
    assert "access-control-allow-origin" not in other.headers


# --- 9. Ningún test de este fichero deja filas en findings ------------------


@pytest.mark.anyio
async def test_ningun_test_previo_de_este_fichero_dejo_filas_en_findings(
    db_session: Session,
) -> None:
    """`db_session` (rollback garantizado, ver `tests/db/conftest.py`) ya
    hace estructuralmente imposible que un test anterior deje filas: cada
    test abre su propia transacción externa sobre una conexión nueva y la
    deshace al terminar, así que ninguna fila sembrada por otro test de este
    fichero pudo llegar a confirmarse. Esta prueba lo comprueba de forma
    ejecutable en vez de darlo solo por descontado: si algún test de este
    fichero alguna vez usara `db_session_factory` (con `commit()` real) en
    vez de `db_session`, esto lo pondría en rojo.
    """
    count = db_session.execute(sa.text("SELECT count(*) FROM findings")).scalar_one()
    assert count == 0


# --- 10. Tope de `page` (T70): 422 en vez de 500 por desbordamiento de bigint -


@pytest.fixture
async def client_sin_relanzar_excepciones(
    db_session: Session,
) -> AsyncGenerator[httpx.AsyncClient, None]:
    """Mismo `get_session` que la fixture `client`, pero con
    `raise_app_exceptions=False` (patrón de
    `tests/test_api_error_handling.py::test_excepcion_no_gestionada_devuelve_500_generico_sin_filtrar_detalle`).

    Sin este parámetro, `ASGITransport` relanza en el propio test cualquier
    excepción no capturada por la aplicación en vez de dejar que el
    manejador genérico la convierta en una respuesta -- lo contrario de lo
    que recibiría un cliente real. Necesario aquí porque, con la mutación
    descrita en el plan de T70 (quitar `le=MAX_PAGE`), un `page` como
    `10**20` desborda el `bigint` de PostgreSQL en el cálculo de `offset` y
    llega a levantar una excepción de base de datos: sin
    `raise_app_exceptions=False` ese caso no produciría una respuesta `500`
    observable, sino un fallo del propio test.

    No reutiliza la fixture `client`: el plan pide no tocarla ni a ella ni
    a los demás tests de este fichero.
    """
    app = create_app()

    def _override_get_session() -> Generator[Session, None, None]:
        yield db_session

    app.dependency_overrides[get_session] = _override_get_session

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


@pytest.mark.anyio
async def test_listado_con_page_en_el_tope_devuelve_200_con_lista_vacia(
    client: httpx.AsyncClient, db_session: Session
) -> None:
    """`page=999_999` (el propio `MAX_PAGE`) sigue siendo una página válida:
    ningún hallazgo real llega tan lejos, pero la API responde `200` con
    `items` vacío y el `total` real, igual que cualquier otra página fuera
    del rango de datos existentes (test 4 más arriba)."""
    _seed_published_finding(db_session, title="único hallazgo")

    response = await client.get("/findings", params={"page": 999_999})

    assert response.status_code == 200
    body = response.json()
    assert body["items"] == []
    assert body["total"] == 1
    assert body["page"] == 999_999


@pytest.mark.parametrize("page_por_encima_del_tope", [1_000_000, 10**20])
@pytest.mark.anyio
async def test_listado_con_page_por_encima_del_tope_devuelve_422_no_500(
    client_sin_relanzar_excepciones: httpx.AsyncClient,
    page_por_encima_del_tope: int,
) -> None:
    """Por encima de `MAX_PAGE`, `422` como con cualquier otro parámetro
    fuera de rango (`page=0`, `size=51`) -- nunca un `500` por desbordamiento
    de `bigint` en PostgreSQL al calcular `offset = (page - 1) * size`."""
    response = await client_sin_relanzar_excepciones.get(
        "/findings", params={"page": page_por_encima_del_tope}
    )

    assert response.status_code == 422
    assert response.status_code != 500
    lowered = response.text.lower()
    for leaked_term in ("traceback", "dataerror", "numericvalueoutofrange", "bigint"):
        assert leaked_term not in lowered, (
            f"el cuerpo del 422 filtró detalle de base de datos: '{leaked_term}'"
        )


def test_max_page_coincide_con_el_limite_documentado() -> None:
    """Congela el valor de `MAX_PAGE` para que cambiarlo obligue a tocar un
    test. No lee `web/src/lib/pagination.ts`: la coincidencia con la web se
    mantiene por convención (comentario de la constante en ambos ficheros)."""
    assert findings.MAX_PAGE == 999_999
