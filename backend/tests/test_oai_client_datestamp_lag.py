"""T93: el suelo del filtro fino de `ArxivOaiClient` cubre el desfase real.

Medido en datos reales (H2): cada semana faltaban en la base los envíos del
jueves >= 18:00 UTC y del viernes >= 18:00 UTC. arXiv anuncia esos lotes con
un `datestamp` que llega a varios días de la fecha de envío (fin de semana),
así que el suelo debe bajar 7 días respecto a `from_`, no 2.

Sin red: `httpx.MockTransport` y un limitador sin esperas.
"""

from datetime import UTC, datetime

import pytest
from test_oai_client import _client, _page, _record, _Recorder

_CAT = ["astro-ph.EP"]


@pytest.mark.anyio
async def test_caso_2026_10_14_con_since_desde_la_marca_cosecha_el_lote_del_12() -> None:
    """since = 2026-10-09 (lo que da `default_since` con marca 2026-10-09 22:05).

    NOTA: con el código actual (lag 2) este test PASA: floor = 10-07 y los
    registros son posteriores. El fallo de este caso vive en
    `test_ingest_default_since.py` (la marca de hace 4 días se ignoraba y se
    pedía solo desde ayer). Aquí queda fijado el contrato de la petición.
    """
    a = _record("2610.10001", fecha="Thu, 8 Oct 2026 19:00:00 GMT")
    b = _record("2610.10002", fecha="Fri, 9 Oct 2026 10:00:00 GMT")
    recorder = _Recorder(_page(a, b))

    fetch = await _client(
        recorder, now=lambda: datetime(2026, 10, 13, 22, 5, tzinfo=UTC)
    ).fetch_new(since=datetime(2026, 10, 9, tzinfo=UTC), categories=_CAT, max_results=100)

    assert recorder.requests[0].url.params["from"] == "2026-10-09"
    assert {i.external_id for i in fetch.items} == {"2610.10001", "2610.10002"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("now", "since"),
    [
        (datetime(2026, 10, 5, 22, 5, tzinfo=UTC), datetime(2026, 10, 4, tzinfo=UTC)),
        (datetime(2026, 10, 6, 22, 5, tzinfo=UTC), datetime(2026, 10, 5, tzinfo=UTC)),
    ],
    ids=["noche-del-lunes-5", "noche-del-martes-6"],
)
async def test_envios_de_jueves_y_viernes_por_la_tarde_se_conservan(
    now: datetime, since: datetime
) -> None:
    jueves = _record("2610.20001", fecha="Thu, 1 Oct 2026 19:00:00 GMT")
    viernes = _record("2610.20002", fecha="Fri, 2 Oct 2026 10:00:00 GMT")
    recorder = _Recorder(_page(jueves, viernes))

    fetch = await _client(recorder, now=lambda: now).fetch_new(
        since=since, categories=_CAT, max_results=100
    )

    assert {i.external_id for i in fetch.items} == {"2610.20001", "2610.20002"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "now",
    [datetime(2026, 10, 6, 22, 5, tzinfo=UTC), datetime(2026, 10, 7, 22, 5, tzinfo=UTC)],
    ids=["noche-del-6", "noche-del-7"],
)
async def test_envio_del_viernes_20h_y_del_sabado_se_conservan_en_las_noches_6_y_7(
    now: datetime,
) -> None:
    viernes = _record("2610.30001", fecha="Fri, 2 Oct 2026 20:00:00 GMT")
    sabado = _record("2610.30002", fecha="Sat, 3 Oct 2026 12:00:00 GMT")
    recorder = _Recorder(_page(viernes, sabado))
    since = datetime(now.year, now.month, now.day - 1, tzinfo=UTC)

    fetch = await _client(recorder, now=lambda: now).fetch_new(
        since=since, categories=_CAT, max_results=100
    )

    assert {i.external_id for i in fetch.items} == {"2610.30001", "2610.30002"}


@pytest.mark.anyio
async def test_el_suelo_sigue_descartando_reediciones_de_hace_meses() -> None:
    nuevo = _record("2610.40001", fecha="Mon, 5 Oct 2026 15:00:00 GMT")
    vieja = _record("2509.12737", fecha="Tue, 16 Sep 2025 06:51:45 GMT")
    recorder = _Recorder(_page(nuevo, vieja))

    fetch = await _client(recorder, now=lambda: datetime(2026, 10, 6, 22, 5, tzinfo=UTC)).fetch_new(
        since=datetime(2026, 10, 5, tzinfo=UTC), categories=_CAT, max_results=100
    )

    assert [i.external_id for i in fetch.items] == ["2610.40001"]
