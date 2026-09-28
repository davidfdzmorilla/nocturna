"""Tests de `ArxivOaiClient` (`infrastructure/arxiv/oai_client.py`).

Sin red y sin esperas reales: `httpx.MockTransport` para HTTP y un
`RateLimiter` con `sleep`/`monotonic` falsos. El `Retrier` se controla con
una `RetryPolicy` de un solo intento salvo donde el reintento sea lo que se
prueba (eso vive en `test_arxiv_retry.py`).
"""

from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from nocturna.infrastructure.arxiv.oai_client import (
    ArxivOaiClient,
    UnknownArxivSet,
    category_to_set,
)
from nocturna.infrastructure.arxiv.rate_limit import RateLimiter
from nocturna.infrastructure.arxiv.retry import RetryPolicy

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arxiv" / "oai"

_NOW = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)
_RAW_NS = 'xmlns="http://arxiv.org/OAI/arXivRaw/"'


def _load(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


class _Recorder:
    """Sirve respuestas en orden y guarda las peticiones recibidas."""

    def __init__(self, *responses: httpx.Response) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if len(self._responses) == 1:
            return self._responses[0]
        return self._responses.pop(0)


def _sin_esperas() -> RateLimiter:
    async def _sleep(_: float) -> None:
        return None

    return RateLimiter(3.0, sleep=_sleep, monotonic=lambda: 0.0)


def _client(recorder: _Recorder, **kwargs: object) -> ArxivOaiClient:
    http = httpx.AsyncClient(transport=httpx.MockTransport(recorder.handle))
    defaults: dict[str, object] = {
        "limiter": _sin_esperas(),
        "now": lambda: _NOW,
        "retry_policy": RetryPolicy(max_attempts=1, base_delay_s=0.001, max_elapsed_s=1.0),
        "lookback_days": 2,
        "max_requests_per_fetch": 6,
    }
    defaults.update(kwargs)
    return ArxivOaiClient(http, **defaults)  # type: ignore[arg-type]


def _record(arxiv_id: str, *, fecha: str, categories: str = "astro-ph.EP") -> str:
    return f"""<record>
      <header><identifier>oai:arXiv.org:{arxiv_id}</identifier><datestamp>2026-09-24</datestamp></header>
      <metadata><arXivRaw {_RAW_NS}>
        <version version="v1"><date>{fecha}</date></version>
        <title>T {arxiv_id}</title><abstract>A {arxiv_id}</abstract>
        <categories>{categories}</categories>
      </arXivRaw></metadata>
    </record>"""


def _page(*registros: str, token: str | None = None) -> httpx.Response:
    cuerpo = "".join(registros)
    if token is not None:
        cuerpo += f"<resumptionToken>{token}</resumptionToken>"
    return httpx.Response(
        200,
        content=f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-09-25T09:00:00Z</responseDate>
  <ListRecords>{cuerpo}</ListRecords>
</OAI-PMH>""".encode(),
    )


# --- Traducción de categoría a set de OAI ---------------------------------


@pytest.mark.parametrize(
    ("categoria", "esperado"),
    [
        ("astro-ph.EP", "physics:astro-ph:EP"),
        ("astro-ph.GA", "physics:astro-ph:GA"),
        ("astro-ph", "physics:astro-ph"),
    ],
)
def test_la_categoria_se_traduce_al_set_jerarquico(categoria: str, esperado: str) -> None:
    assert category_to_set(categoria) == esperado


@pytest.mark.parametrize("categoria", ["math.AG", "gr-qc", "cs.LG", "inventada.XX"])
def test_una_categoria_sin_mapa_conocido_falla_ruidosamente(categoria: str) -> None:
    """No se adivina el prefijo de grupo: `astro-ph.EP` es
    `physics:astro-ph:EP`, pero `math.AG` es `math:math:AG` y `gr-qc` es
    `physics:gr-qc` (sin tercer nivel). Adivinarlo cosecharía el set
    equivocado, o ninguno, y el único síntoma sería una noche con pocos
    ítems."""
    with pytest.raises(UnknownArxivSet) as excinfo:
        category_to_set(categoria)
    assert categoria in str(excinfo.value)


@pytest.mark.anyio
async def test_una_categoria_desconocida_falla_antes_de_pedir_nada() -> None:
    recorder = _Recorder(_page())
    with pytest.raises(UnknownArxivSet):
        await _client(recorder).fetch_new(since=_NOW, categories=["math.AG"], max_results=10)
    assert recorder.requests == []


# --- Parámetros de la petición -------------------------------------------


@pytest.mark.anyio
async def test_la_peticion_lleva_verb_set_prefijo_y_ventana() -> None:
    recorder = _Recorder(_page())
    await _client(recorder).fetch_new(
        since=datetime(2026, 9, 24, tzinfo=UTC), categories=["astro-ph.EP"], max_results=10
    )
    params = recorder.requests[0].url.params
    assert params["verb"] == "ListRecords"
    assert params["set"] == "physics:astro-ph:EP"
    assert params["metadataPrefix"] == "arXivRaw"
    # lookback_days=2 sobre un `now` del 25 -> [24, 25].
    assert params["from"] == "2026-09-24"
    assert params["until"] == "2026-09-25"


@pytest.mark.anyio
async def test_un_since_mas_antiguo_que_la_ventana_la_ensancha() -> None:
    """`since` gobierna el extremo inferior: un relleno con `--since` de
    varios días atrás tiene que pedir esos lotes, no quedarse con la ventana
    nominal y no encontrar nada que filtrar."""
    recorder = _Recorder(_page())
    await _client(recorder).fetch_new(
        since=datetime(2026, 9, 20, tzinfo=UTC), categories=["astro-ph.EP"], max_results=10
    )
    params = recorder.requests[0].url.params
    assert params["from"] == "2026-09-20"
    assert params["until"] == "2026-09-25"


@pytest.mark.anyio
async def test_lookback_days_1_pide_un_solo_dia() -> None:
    recorder = _Recorder(_page())
    await _client(recorder, lookback_days=1).fetch_new(
        since=_NOW, categories=["astro-ph.EP"], max_results=10
    )
    params = recorder.requests[0].url.params
    assert params["from"] == params["until"] == "2026-09-25"


@pytest.mark.anyio
async def test_cada_categoria_se_pide_en_su_propio_set() -> None:
    recorder = _Recorder(_page())
    await _client(recorder).fetch_new(
        since=_NOW, categories=["astro-ph.EP", "astro-ph.GA"], max_results=10
    )
    sets = [r.url.params["set"] for r in recorder.requests]
    assert sets == ["physics:astro-ph:EP", "physics:astro-ph:GA"]


# --- Filtro por since ----------------------------------------------------


@pytest.mark.anyio
async def test_las_revisiones_de_papers_viejos_se_descartan_por_since() -> None:
    """El `datestamp` de OAI es de anuncio, así que la cosecha de un día trae
    revisiones de papers antiguos. El filtro es el MISMO que usa la vía Atom:
    `published_at >= since` sobre la fecha de la v1, sin regla nueva."""
    nuevo = _record("2609.30000", fecha="Thu, 24 Sep 2026 15:00:00 GMT")
    revision = _record("2509.12737", fecha="Tue, 16 Sep 2025 06:51:45 GMT")
    recorder = _Recorder(_page(nuevo, revision))
    fetch = await _client(recorder).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=10
    )
    assert [item.external_id for item in fetch.items] == ["2609.30000"]


# --- Deduplicación de cross-list -----------------------------------------


@pytest.mark.anyio
async def test_un_paper_en_dos_sets_se_emite_una_sola_vez() -> None:
    """Un cross-list aparece en el set de las dos categorías. Sin dedup aquí,
    `add_many` lo filtraría igual en base, pero `fetched` y `duplicates` de
    `IngestResult` quedarían inflados y ensuciarían la tabla de calibración."""
    cross = _record(
        "2609.30000", fecha="Thu, 24 Sep 2026 15:00:00 GMT", categories="astro-ph.EP astro-ph.GA"
    )
    recorder = _Recorder(_page(cross), _page(cross))
    fetch = await _client(recorder).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC),
        categories=["astro-ph.EP", "astro-ph.GA"],
        max_results=10,
    )
    assert [item.external_id for item in fetch.items] == ["2609.30000"]
    assert len(recorder.requests) == 2


# --- Paginación con resumptionToken --------------------------------------


@pytest.mark.anyio
async def test_se_sigue_el_resumption_token_hasta_que_viene_vacio() -> None:
    p1 = _page(_record("2609.00001", fecha="Thu, 24 Sep 2026 15:00:00 GMT"), token="tok1")
    p2 = _page(_record("2609.00002", fecha="Thu, 24 Sep 2026 16:00:00 GMT"))
    recorder = _Recorder(p1, p2)
    fetch = await _client(recorder).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=10
    )
    assert [item.external_id for item in fetch.items] == ["2609.00001", "2609.00002"]
    # La segunda petición va con el token y SIN los parámetros de la primera:
    # el protocolo lo exige (el token los lleva dentro).
    assert recorder.requests[1].url.params["resumptionToken"] == "tok1"
    assert "set" not in recorder.requests[1].url.params


@pytest.mark.anyio
async def test_max_requests_per_fetch_acota_las_peticiones() -> None:
    """Techo duro: una cadena de tokens inesperada no puede encadenar
    peticiones sin límite a las 00:05."""
    recorder = _Recorder(
        _page(_record("2609.00001", fecha="Thu, 24 Sep 2026 15:00:00 GMT"), token="siempre")
    )
    fetch = await _client(recorder, max_requests_per_fetch=3).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=100
    )
    assert len(recorder.requests) == 3
    assert fetch.truncated is True


@pytest.mark.anyio
async def test_max_results_corta_y_marca_truncated() -> None:
    registros = "".join(
        _record(f"2609.0000{i}", fecha="Thu, 24 Sep 2026 15:00:00 GMT") for i in range(1, 5)
    )
    recorder = _Recorder(_page(registros))
    fetch = await _client(recorder).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=2
    )
    assert len(fetch.items) == 2
    assert fetch.truncated is True


# --- skipped y cortesía --------------------------------------------------


@pytest.mark.anyio
async def test_los_registros_descartados_se_reportan_en_skipped() -> None:
    bueno = _record("2609.00001", fecha="Thu, 24 Sep 2026 15:00:00 GMT")
    borrado = """<record><header status="deleted">
      <identifier>oai:arXiv.org:2609.00002</identifier><datestamp>2026-09-24</datestamp>
    </header></record>"""
    recorder = _Recorder(_page(bueno + borrado))
    fetch = await _client(recorder).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=10
    )
    assert len(fetch.items) == 1
    assert fetch.skipped == 1


@pytest.mark.anyio
async def test_cada_peticion_pasa_por_el_limitador_de_cortesia() -> None:
    """Con el reloj congelado, el limitador espera sus 3 s en cada petición
    posterior a la primera: la política de arXiv se respeta también entre
    sets y entre páginas."""
    esperas: list[float] = []

    async def _sleep(segundos: float) -> None:
        esperas.append(segundos)

    limiter = RateLimiter(3.0, sleep=_sleep, monotonic=lambda: 0.0)
    recorder = _Recorder(_page())
    await _client(recorder, limiter=limiter).fetch_new(
        since=_NOW, categories=["astro-ph.EP", "astro-ph.GA"], max_results=10
    )
    assert esperas == [3.0]


@pytest.mark.anyio
async def test_el_user_agent_identifica_al_cliente() -> None:
    recorder = _Recorder(_page())
    await _client(recorder).fetch_new(since=_NOW, categories=["astro-ph.EP"], max_results=10)
    assert recorder.requests[0].headers["User-Agent"] == "nocturna/0.1.0"


# --- El suelo del filtro y el desfase del datestamp (B1) -----------------


def _real_page() -> httpx.Response:
    return httpx.Response(200, content=_load("list_records_ep.xml"))


@pytest.mark.anyio
async def test_el_lote_de_anuncio_se_cosecha_entero_no_solo_la_mitad() -> None:
    """**El test que congela el defecto más grave de T60.c.**

    Un lote con `datestamp = D` contiene envíos de `(D-2 18:00 UTC,
    D-1 18:00 UTC]`, porque arXiv anuncia a las 00:00 UTC lo enviado hasta
    las 18:00 UTC del día anterior. Cuando el suelo del filtro era `since`
    (por defecto, *ayer a medianoche*), la franja de envíos de la tarde
    anterior se descartaba: **8 de 18 novedades de esta captura real, un
    44 %**, y para siempre, porque la noche anterior no pedía ese `datestamp`
    y la siguiente lo pedía con el `since` ya por delante.

    La captura tiene `datestamp = 2026-09-24` y v1 desde el 22-sep 18:00
    hasta el 23-sep 14:12.
    """
    recorder = _Recorder(_real_page())
    fetch = await _client(recorder, now=lambda: datetime(2026, 9, 24, 22, 5, tzinfo=UTC)).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=100
    )
    ids = {item.external_id for item in fetch.items}

    # Los ocho del 22-sep por la tarde, que antes se perdían.
    del_22_por_la_tarde = {
        "2609.26874",
        "2609.26894",
        "2609.26917",
        "2609.27013",
        "2609.27025",
        "2609.27031",
        "2609.27039",
        "2609.27140",
    }
    assert del_22_por_la_tarde <= ids, (
        "se han vuelto a perder los envíos de la tarde anterior al anuncio"
    )
    assert len(fetch.items) >= 18


@pytest.mark.anyio
async def test_la_noche_siguiente_tambien_ve_el_lote_no_cero_items() -> None:
    """El corolario: con el suelo anterior, una noche cuyo `since` ya había
    pasado el lote entero cosechaba **0 de 27 registros** y lo daba por
    normal. El criterio de cierre de la tarea (`items_fetched > 0`) no lo
    habría detectado."""
    recorder = _Recorder(_real_page())
    fetch = await _client(recorder, now=lambda: datetime(2026, 9, 25, 22, 5, tzinfo=UTC)).fetch_new(
        since=datetime(2026, 9, 24, tzinfo=UTC), categories=["astro-ph.EP"], max_results=100
    )
    assert len(fetch.items) > 0


@pytest.mark.anyio
async def test_las_reediciones_antiguas_siguen_descartandose() -> None:
    """El suelo baja dos días, no desaparece: `2509.12737` tiene v1 de 2025
    y aparece en la cosecha solo porque su `datestamp` es reciente. Sin este
    contraste, «no perder nada» se cumpliría ingiriendo también las
    reediciones, gastando lecturas del Reader en papers ya analizados."""
    recorder = _Recorder(_real_page())
    fetch = await _client(recorder, now=lambda: datetime(2026, 9, 24, 22, 5, tzinfo=UTC)).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=100
    )
    assert "2509.12737" not in {item.external_id for item in fetch.items}


@pytest.mark.anyio
async def test_el_resumen_de_la_cosecha_queda_en_el_log(caplog) -> None:
    """`filtered_out` es el contador que habría hecho visible el defecto de
    arriba la primera noche, en vez de en una revisión."""
    import logging

    from nocturna.infrastructure.arxiv import oai_client as oai_client_module

    caplog.set_level(logging.INFO)
    monkey_disabled = oai_client_module._logger.disabled
    oai_client_module._logger.disabled = False
    try:
        recorder = _Recorder(_real_page())
        await _client(recorder, now=lambda: datetime(2026, 9, 24, 22, 5, tzinfo=UTC)).fetch_new(
            since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=100
        )
    finally:
        oai_client_module._logger.disabled = monkey_disabled

    resumen = [r for r in caplog.records if getattr(r, "event", None) == "arxiv.oai_harvest"]
    assert len(resumen) == 1
    campos = resumen[0].__dict__
    assert campos["items"] > 0
    assert campos["filtered_out"] > 0  # las reediciones antiguas de la captura
    assert campos["requests"] == 1
    assert campos["floor"].startswith("2026-09-21")


@pytest.mark.anyio
async def test_una_fecha_sin_zona_horaria_no_tumba_la_cosecha() -> None:
    """`parsedate_to_datetime` devuelve un `datetime` naive con offset
    `-0000`. Antes pasaba el parser y reventaba al comparar con el suelo:
    `TypeError`, cosecha entera perdida por un solo paper mal fechado."""
    bueno = _record("2609.00001", fecha="Thu, 24 Sep 2026 15:00:00 GMT")
    sin_zona = _record("2609.00002", fecha="Wed, 23 Sep 2026 11:00:00 -0000")
    otro = _record("2609.00003", fecha="Thu, 24 Sep 2026 16:00:00 GMT")
    recorder = _Recorder(_page(bueno + sin_zona + otro))
    fetch = await _client(recorder).fetch_new(
        since=datetime(2026, 9, 23, tzinfo=UTC), categories=["astro-ph.EP"], max_results=10
    )
    assert [item.external_id for item in fetch.items] == ["2609.00001", "2609.00003"]
    assert fetch.skipped == 1


@pytest.mark.anyio
async def test_el_log_de_agotamiento_nombra_el_set_no_un_start() -> None:
    """El campo se llamaba `start` en las dos vías, así que en OAI el log de
    la noche decía `"start": "physics:astro-ph:EP"`. `CALIBRACION.md` manda
    leer ese evento cada mañana; el nombre no puede significar una cosa u
    otra según la vía."""
    from nocturna.infrastructure.arxiv import oai_client as oai_client_module
    from nocturna.infrastructure.arxiv.transport import ArxivUnavailable

    # `transport.fetch_with_retry` registra con el logger que le pasa el
    # llamador, así que en esta vía el evento sale bajo `oai_client`.
    recorder = _Recorder(httpx.Response(406))
    disabled = oai_client_module._logger.disabled
    oai_client_module._logger.disabled = False
    try:
        with pytest.raises(ArxivUnavailable) as excinfo:
            await _client(recorder).fetch_new(
                since=_NOW, categories=["astro-ph.EP"], max_results=10
            )
    finally:
        oai_client_module._logger.disabled = disabled

    assert "set=physics:astro-ph:EP" in str(excinfo.value)
    assert "start=" not in str(excinfo.value)


# --- Cancelación: la invariante de hard_stop ------------------------------


@pytest.mark.anyio
async def test_cancelar_durante_el_backoff_propaga_cancelledeerror() -> None:
    """La invariante que protege el corte duro de las 04:45.

    La ingesta es la primera fase de la noche y corre dentro de la tarea que
    el vigía de `RunNight` cancela en `hard_stop`. Si una `CancelledError` se
    perdiera por el camino, la noche seguiría cosechando pasada la ventana.

    `ArxivOaiClient` tiene **dos** niveles de bucle (sets y `resumptionToken`),
    es decir más sitios donde perderla que la vía Atom, y hasta ahora ningún
    test la cubría en esta vía: se cumplía por estructura del código (no hay
    un solo `except` en `fetch_new`), y nada impedía que un `except Exception`
    añadido más adelante la rompiera sin poner un test en rojo.

    Es el análogo de `test_arxiv_client.py::...propaga_cancelledeerror`, pero
    cancelando en el **segundo set**, para cubrir los dos bucles.
    """
    import asyncio

    # Primer set: responde bien. Segundo: 500, que entra en el backoff.
    recorder = _Recorder(
        _page(_record("2609.00001", fecha="Thu, 24 Sep 2026 15:00:00 GMT")),
        httpx.Response(500),
        httpx.Response(500),
    )
    client = _client(
        recorder,
        retry_policy=RetryPolicy(max_attempts=4, base_delay_s=0.2, max_elapsed_s=5.0),
    )

    task = asyncio.ensure_future(
        client.fetch_new(
            since=datetime(2026, 9, 23, tzinfo=UTC),
            categories=["astro-ph.EP", "astro-ph.GA"],
            max_results=100,
        )
    )
    # Tiempo para que el primer set termine y el segundo entre en la espera
    # real del backoff (0.1-0.3 s con jitter sobre base 0.2).
    await asyncio.sleep(0.05)
    assert not task.done(), "la tarea debería seguir viva, dormida en el backoff"
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
