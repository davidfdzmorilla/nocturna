"""Tests del parser Atom de arXiv (`infrastructure/arxiv/atom.py`).

Todas las fixtures son respuestas reales capturadas y guardadas en
`tests/fixtures/arxiv/` (ver el `README.md` de ese directorio para la
consulta exacta de cada una). Ningún test sale a la red.
"""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nocturna.infrastructure.arxiv.atom import ArxivFeedError, parse_feed

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "arxiv"


def _load(name: str) -> bytes:
    return (FIXTURES_DIR / name).read_bytes()


def test_las_tres_entradas_se_parsean_con_metadatos_correctos():
    parsed = parse_feed(_load("feed_three_entries.xml"))

    assert len(parsed.entries) == 3
    assert parsed.skipped == 0
    assert parsed.total_results == 113100

    first = parsed.entries[0]
    assert first.arxiv_id == "2609.17526"
    assert first.version == 1


def test_titulo_y_abstract_llegan_con_espacios_normalizados():
    parsed = parse_feed(_load("feed_three_entries.xml"))
    first = parsed.entries[0]

    assert first.title == (
        "Secular evolution of viscous and self-gravitating protoplanetary discs with magnetic winds"
    )
    assert first.abstract.startswith(
        "Traditionally, the disc is believed to evolve under the influence of turbulent viscosity"
    )
    # arXiv rellena a 80 columnas con saltos de línea; _normalize_whitespace
    # los colapsa. Si algún día se cuela un salto de línea o un espacio de
    # relleno sin colapsar, estas dos aserciones lo detectan.
    for entry in parsed.entries:
        assert "\n" not in entry.title
        assert "\n" not in entry.abstract
        assert "  " not in entry.title
        assert "  " not in entry.abstract
        assert entry.title == entry.title.strip()
        assert entry.abstract == entry.abstract.strip()


def test_categorias_traen_la_primaria_en_primera_posicion():
    parsed = parse_feed(_load("feed_three_entries.xml"))

    # Única categoría: primaria y término coinciden.
    assert parsed.entries[0].categories == ("astro-ph.EP",)
    # Dos categorías: la primaria (astro-ph.EP) va primero aunque en el XML
    # el <category> de astro-ph.EP también aparezca listado.
    assert parsed.entries[1].categories == ("astro-ph.EP", "astro-ph.SR")
    # La entrada de AGN: primaria astro-ph.HE, con astro-ph.GA como segunda.
    assert parsed.entries[2].categories == ("astro-ph.HE", "astro-ph.GA")


def test_published_at_y_updated_at_son_aware():
    parsed = parse_feed(_load("feed_three_entries.xml"))

    for entry in parsed.entries:
        assert entry.published_at.tzinfo is not None
        assert entry.updated_at.tzinfo is not None


def test_entrada_sin_revision_tiene_published_igual_a_updated():
    parsed = parse_feed(_load("feed_three_entries.xml"))
    first = parsed.entries[0]

    assert first.published_at == first.updated_at
    assert first.published_at == datetime(2026, 9, 15, 17, 57, 6, tzinfo=UTC)


def test_revision_tiene_published_distinto_de_updated_y_arxiv_id_sin_sufijo_version():
    parsed = parse_feed(_load("feed_revised_entries.xml"))

    revised = next(entry for entry in parsed.entries if entry.arxiv_id == "2602.11270")

    assert revised.version == 2
    assert "v2" not in revised.arxiv_id
    assert revised.published_at != revised.updated_at
    assert revised.published_at == datetime(2026, 2, 11, 19, 0, 3, tzinfo=UTC)
    assert revised.updated_at == datetime(2026, 9, 15, 17, 22, 15, tzinfo=UTC)


def test_feed_vacio_no_tiene_entradas():
    parsed = parse_feed(_load("feed_empty.xml"))

    assert parsed.entries == ()
    assert parsed.total_results == 0
    assert parsed.skipped == 0


def test_feed_de_error_lanza_arxivfeederror_aunque_llegue_con_http_200():
    with pytest.raises(ArxivFeedError):
        parse_feed(_load("feed_error.xml"))


def test_entrada_incompleta_se_descarta_y_se_cuenta_sin_tumbar_el_resto():
    parsed = parse_feed(_load("feed_malformed_entry.xml"))

    assert len(parsed.entries) == 2
    assert parsed.skipped == 1
    # Las dos entradas que sobreviven son la primera y la tercera del feed
    # original: la segunda (sin <summary>) es la que se descarta.
    assert [entry.arxiv_id for entry in parsed.entries] == ["2609.17526", "2609.17383"]
