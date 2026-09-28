"""Las dos vías de ingesta producen el MISMO `Item` para el mismo paper.

Este es el test que sostiene la afirmación central de T60.c: cambiar la vía
de ingesta de `/api/query` (Atom) a OAI-PMH **no cambia el dominio**, no
obliga a migrar nada y no puede duplicar las filas que ya hay en `items`.
Sin él, eso sería una promesa del plan; con él, es una propiedad congelada.

Las dos fixturas contienen los mismos tres papers (`2609.17526`,
`2609.17505`, `2609.17383`), capturados por las dos vías. Si alguien toca la
normalización del identificador, el formato de metadatos o el orden de las
categorías, se entera aquí.
"""

import os
from datetime import UTC, datetime
from pathlib import Path

from nocturna.domain.entities import Item
from nocturna.infrastructure.arxiv.atom import parse_feed
from nocturna.infrastructure.arxiv.mappers import entry_to_item
from nocturna.infrastructure.arxiv.oai import parse_oai_response

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "arxiv"

_FETCHED_AT = datetime(2026, 9, 25, 9, 0, tzinfo=UTC)


def _items_por_atom() -> dict[str, Item]:
    parsed = parse_feed((FIXTURES_DIR / "feed_three_entries.xml").read_bytes())
    return {
        entry.arxiv_id: entry_to_item(entry, fetched_at=_FETCHED_AT) for entry in parsed.entries
    }


def _items_por_oai() -> dict[str, Item]:
    page = parse_oai_response(
        (FIXTURES_DIR / "oai" / "list_records_three_entries.xml").read_bytes()
    )
    return {entry.arxiv_id: entry_to_item(entry, fetched_at=_FETCHED_AT) for entry in page.entries}


def test_las_dos_vias_cubren_los_mismos_papers() -> None:
    """Si las fixturas dejaran de coincidir, el resto de este fichero
    compararía conjuntos distintos y pasaría sin probar nada."""
    assert _items_por_atom().keys() == _items_por_oai().keys()
    assert len(_items_por_atom()) == 3


def test_el_external_id_es_identico_por_las_dos_vias() -> None:
    """El invariante que impediría duplicar la base entera en una noche: el
    identificador OAI (`oai:arXiv.org:2609.17526`) normalizado tiene que dar
    exactamente el mismo `external_id` que el `<id>` del Atom
    (`http://arxiv.org/abs/2609.17526v1`), sin prefijo y sin versión."""
    for arxiv_id, atom_item in _items_por_atom().items():
        oai_item = _items_por_oai()[arxiv_id]
        assert atom_item.external_id == oai_item.external_id
        assert "oai:" not in oai_item.external_id
        assert not oai_item.external_id.endswith("v1")


def test_la_source_es_arxiv_por_las_dos_vias() -> None:
    """El otro invariante de deduplicación: la unicidad en base es
    `(source, external_id)`, así que una `source` distinta por vía
    (`arxiv-oai`) reingeriría toda la base como ítems nuevos."""
    for item in list(_items_por_atom().values()) + list(_items_por_oai().values()):
        assert item.source == "arxiv"


def test_published_at_es_identico_por_las_dos_vias() -> None:
    """`published_at` sale del `<published>` del Atom y de la fecha de la
    `v1` en OAI (`arXivRaw`). Que coincidan al segundo es lo que descarta
    `oai_dc` y `arXiv` como formatos: el primero no da la categoría en
    forma corta y el segundo da `<created>` sin hora."""
    for arxiv_id, atom_item in _items_por_atom().items():
        assert atom_item.published_at == _items_por_oai()[arxiv_id].published_at


def test_las_categorias_son_identicas_por_las_dos_vias() -> None:
    """Incluido el orden, con la primaria primero: `<arxiv:primary_category>`
    en Atom y el orden del propio `<categories>` en `arXivRaw`."""
    for arxiv_id, atom_item in _items_por_atom().items():
        assert atom_item.categories == _items_por_oai()[arxiv_id].categories


def test_el_abstract_de_oai_trae_latex_crudo_donde_la_api_trae_unicode() -> None:
    """Diferencia CONOCIDA y aceptada entre las dos vías, congelada aquí para
    que nadie la tome por un bug ni la "arregle" a ciegas.

    `/api/query` normaliza algunos comandos LaTeX a Unicode antes de servir
    el Atom (`\\AA` -> `Å`, `\\mu` -> `μ`); `arXivRaw` devuelve el fuente
    tal cual, que es exactamente lo que su nombre anuncia. El contenido es el
    mismo texto: solo cambia cómo se representan esos símbolos.

    Por qué se acepta: el abstract lo consume el Reader (un LLM, que lee
    igual de bien `\\AA` que `Å`) y la web publica los tres niveles que
    escribe el Popularizer, no el abstract en crudo. Convertir LaTeX a
    Unicode por nuestra cuenta exigiría una dependencia nueva y arriesgaría
    romper fórmulas; usar el formato `arXiv` en vez de `arXivRaw` costaría la
    hora de envío. Ver `docs/OPEN_DECISIONS.md`.
    """
    atom = _items_por_atom()
    oai = _items_por_oai()

    # 2609.17505: "6708 Å" (Atom) frente a "6708 \AA" (OAI).
    assert "6708 Å" in atom["2609.17505"].abstract
    assert r"6708 \AA" in oai["2609.17505"].abstract

    # Y el resto del texto sí coincide: el prefijo común es casi todo el
    # abstract, la divergencia empieza en el primer símbolo afectado.
    prefijo_comun = os.path.commonprefix([atom["2609.17505"].abstract, oai["2609.17505"].abstract])
    assert len(prefijo_comun) > 500, (
        "la divergencia debería empezar tarde, en el primer símbolo LaTeX"
    )


def test_los_invariantes_de_deduplicacion_son_identicos_por_las_dos_vias() -> None:
    """La comparación agregada de lo que SÍ debe coincidir campo a campo.

    Se excluye `id` (un UUID nuevo por instancia, nunca comparable) y
    `fetched_at` (lo pone quien ingiere). El abstract y el título quedan
    fuera por la diferencia de LaTeX documentada arriba.
    """

    def clave(item: Item) -> tuple[str, str, tuple[str, ...], object]:
        return (item.source, item.external_id, tuple(item.categories), item.published_at)

    assert {k: clave(v) for k, v in _items_por_atom().items()} == {
        k: clave(v) for k, v in _items_por_oai().items()
    }
