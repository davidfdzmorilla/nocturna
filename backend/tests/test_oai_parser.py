"""Tests del parser OAI-PMH (`infrastructure/arxiv/oai.py`).

Sin red: todo sale de `tests/fixtures/arxiv/oai/`. Las fixturas capturadas
de verdad están documentadas en el README de ese directorio; las derivadas
lo dicen en un comentario XML dentro del propio fichero.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nocturna.infrastructure.arxiv.atom import ArxivFeedError
from nocturna.infrastructure.arxiv.oai import parse_oai_response

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arxiv" / "oai"


def _load(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def _envolver(registros: str) -> bytes:
    """Envuelve fragmentos `<record>` en una respuesta ListRecords mínima."""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-09-25T09:00:00Z</responseDate>
  <ListRecords>{registros}</ListRecords>
</OAI-PMH>""".encode()


_RAW_NS = 'xmlns="http://arxiv.org/OAI/arXivRaw/"'


def _registro(
    *,
    identifier: str = "oai:arXiv.org:2609.17526",
    versiones: str = '<version version="v1"><date>Mon, 15 Sep 2026 17:57:06 GMT</date></version>',
    title: str = "Un título",
    abstract: str = "Un abstract",
    categories: str = "astro-ph.EP",
    status: str = "",
    con_metadata: bool = True,
) -> str:
    metadata = (
        f"""<metadata><arXivRaw {_RAW_NS}>
        {versiones}
        <title>{title}</title>
        <abstract>{abstract}</abstract>
        <categories>{categories}</categories>
      </arXivRaw></metadata>"""
        if con_metadata
        else ""
    )
    return f"""<record>
      <header{status}><identifier>{identifier}</identifier><datestamp>2026-09-16</datestamp></header>
      {metadata}
    </record>"""


# --- Casos con datos reales ------------------------------------------------


def test_la_captura_real_se_parsea_completa() -> None:
    """27 registros reales de `physics:astro-ph:EP`, ninguno descartado."""
    page = parse_oai_response(_load("list_records_ep.xml"))
    assert len(page.entries) == 27
    assert page.skipped == 0
    assert page.resumption_token is None


def test_published_at_sale_de_la_v1_y_updated_at_de_la_ultima() -> None:
    """`2509.12737` de la captura real tiene v1 de 2025 y v2 de 2026: es una
    revisión de un paper viejo que aparece en la cosecha porque su
    `datestamp` de anuncio es reciente. `published_at` debe ser la v1, que es
    lo que permite que el filtro `published_at >= since` la descarte."""
    page = parse_oai_response(_load("list_records_ep.xml"))
    entry = next(e for e in page.entries if e.arxiv_id == "2509.12737")
    assert entry.published_at == datetime(2025, 9, 16, 6, 51, 45, tzinfo=UTC)
    assert entry.updated_at == datetime(2026, 9, 23, 7, 17, 28, tzinfo=UTC)
    assert entry.version == 2


# --- Normalización del identificador --------------------------------------


@pytest.mark.parametrize(
    ("identifier", "esperado"),
    [
        ("oai:arXiv.org:2609.17526", "2609.17526"),
        # Identificadores antiguos, con barra y sin punto.
        ("oai:arXiv.org:astro-ph/0601001", "astro-ph/0601001"),
    ],
)
def test_el_prefijo_oai_se_quita_del_identificador(identifier: str, esperado: str) -> None:
    """El invariante que impide duplicar la base: `external_id` no puede
    llevar el prefijo `oai:arXiv.org:` ni la versión."""
    page = parse_oai_response(_envolver(_registro(identifier=identifier)))
    assert page.entries[0].arxiv_id == esperado


@pytest.mark.parametrize("identifier", ["2609.17526", "oai:otro.org:123", "oai:arXiv.org:"])
def test_un_identificador_con_forma_inesperada_se_descarta(identifier: str) -> None:
    page = parse_oai_response(_envolver(_registro(identifier=identifier)))
    assert page.entries == ()
    assert page.skipped == 1


# --- Registros que no se pueden usar --------------------------------------


def test_un_registro_borrado_se_cuenta_en_skipped_sin_reventar() -> None:
    """`deletedRecord = persistent`: arXiv anuncia retiradas con el header
    marcado y sin bloque `<metadata>`."""
    page = parse_oai_response(_envolver(_registro(status=' status="deleted"', con_metadata=False)))
    assert page.entries == ()
    assert page.skipped == 1


def test_un_registro_sin_v1_se_descarta() -> None:
    """Sin fecha de envío no se puede decidir si es novedad."""
    versiones = '<version version="v2"><date>Wed, 23 Sep 2026 07:17:28 GMT</date></version>'
    page = parse_oai_response(_envolver(_registro(versiones=versiones)))
    assert page.entries == ()
    assert page.skipped == 1


def test_una_fecha_rfc2822_ilegible_se_descarta_sin_excepcion() -> None:
    versiones = '<version version="v1"><date>no es una fecha</date></version>'
    page = parse_oai_response(_envolver(_registro(versiones=versiones)))
    assert page.entries == ()
    assert page.skipped == 1


def test_un_registro_sin_categorias_se_descarta() -> None:
    page = parse_oai_response(_envolver(_registro(categories="")))
    assert page.entries == ()
    assert page.skipped == 1


def test_un_registro_roto_no_tumba_los_demas() -> None:
    """La garantía que hace que una noche no se pierda por un paper con un
    campo raro: se descarta ese y siguen los otros."""
    bueno = _registro(identifier="oai:arXiv.org:2609.00001")
    roto = _registro(identifier="oai:arXiv.org:2609.00002", categories="")
    page = parse_oai_response(_envolver(bueno + roto))
    assert [e.arxiv_id for e in page.entries] == ["2609.00001"]
    assert page.skipped == 1


# --- Errores de protocolo -------------------------------------------------


def test_norecordsmatch_es_una_pagina_vacia_no_un_error() -> None:
    """Un rango de fechas sin novedades es una respuesta legítima. Tratarlo
    como fallo dejaría la cola sin ítems y culparía a arXiv de nada."""
    page = parse_oai_response(_load("list_records_empty.xml"))
    assert page.entries == ()
    assert page.skipped == 0
    assert page.resumption_token is None


def test_badargument_es_un_error_con_su_codigo_en_el_mensaje() -> None:
    """Captura real: arXiv responde `badArgument` con "start date too early"
    cuando el `from` es anterior a su `earliestDatestamp`."""
    with pytest.raises(ArxivFeedError) as excinfo:
        parse_oai_response(_load("error_bad_argument.xml"))
    assert "badArgument" in str(excinfo.value)


def test_una_respuesta_sin_listrecords_ni_error_es_un_error() -> None:
    payload = b"""<?xml version="1.0"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><responseDate/></OAI-PMH>"""
    with pytest.raises(ArxivFeedError):
        parse_oai_response(payload)


def test_un_xml_invalido_es_un_error_de_feed() -> None:
    with pytest.raises(ArxivFeedError) as excinfo:
        parse_oai_response(b"no soy xml")
    assert "XML" in str(excinfo.value)


# --- resumptionToken ------------------------------------------------------


def test_un_resumption_token_con_contenido_se_devuelve() -> None:
    registros = _registro() + "<resumptionToken>abc123</resumptionToken>"
    page = parse_oai_response(_envolver(registros))
    assert page.resumption_token == "abc123"


@pytest.mark.parametrize(
    "token_xml",
    [
        "<resumptionToken></resumptionToken>",
        "<resumptionToken/>",
        # Solo en este caso `.text` NO es None, así que es el único que
        # ejercita la rama `return token or None` de `oai.py`. Sin él, el
        # mutante que quita esa guarda sobrevive a la suite entera: los dos
        # casos de arriba cortan antes, en `element.text is None`.
        "<resumptionToken>   </resumptionToken>",
    ],
)
def test_un_resumption_token_vacio_significa_ultima_pagina(token_xml: str) -> None:
    """arXiv cierra la cadena con el elemento presente pero vacío."""
    page = parse_oai_response(_envolver(_registro() + token_xml))
    assert page.resumption_token is None


# --- Normalización de texto ----------------------------------------------


def test_el_relleno_a_80_columnas_se_colapsa_en_titulo_y_abstract() -> None:
    page = parse_oai_response(
        _envolver(_registro(title="Un\n  título\n  partido", abstract="Un\n\n  abstract"))
    )
    assert page.entries[0].title == "Un título partido"
    assert page.entries[0].abstract == "Un abstract"


def test_las_categorias_conservan_el_orden_y_no_se_repiten() -> None:
    """`<categories>` viene con la primaria primero, que es el mismo orden
    que produce `atom.py` a partir de `<arxiv:primary_category>`."""
    page = parse_oai_response(
        _envolver(_registro(categories="astro-ph.IM astro-ph.EP astro-ph.IM"))
    )
    assert page.entries[0].categories == ("astro-ph.IM", "astro-ph.EP")
