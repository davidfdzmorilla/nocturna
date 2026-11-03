"""T100: los autores del paper llegan de arXiv (OAI arXivRaw y Atom) hasta el `Item`.

Sin red: fixtures locales y XML sintético.
"""

from datetime import UTC, datetime
from pathlib import Path

from nocturna.infrastructure.arxiv.atom import parse_feed
from nocturna.infrastructure.arxiv.mappers import entry_to_item
from nocturna.infrastructure.arxiv.oai import parse_oai_response

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arxiv"
_RAW_NS = 'xmlns="http://arxiv.org/OAI/arXivRaw/"'


def _oai(authors_xml: str) -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/">
  <responseDate>2026-09-25T09:00:00Z</responseDate>
  <ListRecords><record>
    <header><identifier>oai:arXiv.org:2609.17526</identifier><datestamp>2026-09-16</datestamp></header>
    <metadata><arXivRaw {_RAW_NS}>
      <version version="v1"><date>Mon, 15 Sep 2026 17:57:06 GMT</date></version>
      <title>Un título</title>
      {authors_xml}
      <abstract>Un abstract</abstract>
      <categories>astro-ph.EP</categories>
    </arXivRaw></metadata>
  </record></ListRecords>
</OAI-PMH>""".encode()


def test_oai_authors_con_tex_y_espacios_normalizados() -> None:
    page = parse_oai_response(
        _oai("<authors>A. Su\\'arez Mascare\\~no,\n   B.   Doe\n and C. Roe</authors>")
    )

    (entry,) = page.entries
    assert entry.authors == "A. Su\\'arez Mascare\\~no, B. Doe and C. Roe"


def test_oai_sin_la_etiqueta_authors_es_none_y_no_lanza() -> None:
    page = parse_oai_response(_oai(""))

    (entry,) = page.entries
    assert page.skipped == 0
    assert entry.authors is None


def test_oai_authors_vacio_es_none() -> None:
    (entry,) = parse_oai_response(_oai("<authors>   </authors>")).entries
    assert entry.authors is None


def test_oai_real_trae_autores_en_la_fixture_de_tres_entradas() -> None:
    page = parse_oai_response(
        (FIXTURES_DIR / "oai" / "list_records_three_entries.xml").read_bytes()
    )

    assert all(e.authors for e in page.entries)
    assert "Nikku Madhusudhan" in {e.authors for e in page.entries if "Sairam" in e.authors}.pop()


def test_atom_une_los_autores_con_coma() -> None:
    parsed = parse_feed((FIXTURES_DIR / "feed_page_1.xml").read_bytes())

    first = parsed.entries[0]
    assert first.authors is not None
    assert first.authors.startswith("Evgenii R. Redkin, Eduard I. Vorobyov, Paola Pinilla")
    assert first.authors.endswith("Konstanze Zwintz")


def test_el_mapper_copia_los_autores_al_item() -> None:
    (entry,) = parse_oai_response(_oai("<authors>A. Smith, B. Doe</authors>")).entries

    item = entry_to_item(entry, fetched_at=datetime(2026, 9, 25, tzinfo=UTC))

    assert item.authors == "A. Smith, B. Doe"
