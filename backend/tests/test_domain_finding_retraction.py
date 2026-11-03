"""`Finding.retract` y `Item.authors` (T100). Sin base de datos ni Claude."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from nocturna.domain.entities import Finding, FindingType, Item
from nocturna.domain.errors import InvalidTransition, InvariantViolation

_PUBLISHED = datetime(2026, 3, 1, tzinfo=UTC)
_RETRACTED = datetime(2026, 3, 5, tzinfo=UTC)


def _finding(**overrides: object) -> Finding:
    defaults: dict[str, object] = {
        "item_id": uuid4(),
        "run_id": uuid4(),
        "type": FindingType.PAPER_EXPLAINED,
        "title": "t",
        "level_curious": "c",
        "level_amateur": "a",
        "level_technical": "x",
    }
    defaults.update(overrides)
    return Finding(**defaults)


def _published() -> Finding:
    finding = _finding()
    finding.publish(confidence=0.8, at=_PUBLISHED)
    return finding


def test_retract_de_un_publicado_conserva_published_at():
    finding = _published()
    finding.retract(_RETRACTED, "El autor retiró el paper.")
    assert finding.retracted_at == _RETRACTED
    assert finding.retraction_note == "El autor retiró el paper."
    assert finding.published_at == _PUBLISHED


def test_retract_dos_veces_es_transicion_invalida():
    finding = _published()
    finding.retract(_RETRACTED, "nota")
    with pytest.raises(InvalidTransition):
        finding.retract(_RETRACTED, "otra nota")


def test_retract_de_un_no_publicado_es_transicion_invalida():
    with pytest.raises(InvalidTransition):
        _finding().retract(_RETRACTED, "nota")


@pytest.mark.parametrize("note", ["", "   ", "\n\t"])
def test_retract_con_nota_vacia_viola_invariante(note):
    finding = _published()
    with pytest.raises(InvariantViolation):
        finding.retract(_RETRACTED, note)
    assert finding.retracted_at is None
    assert finding.retraction_note is None


def test_constructor_exige_los_dos_campos_juntos():
    with pytest.raises(InvariantViolation):
        _finding(retracted_at=_RETRACTED)
    with pytest.raises(InvariantViolation):
        _finding(retraction_note="nota")


def test_constructor_retirado_exige_publicado():
    with pytest.raises(InvariantViolation):
        _finding(retracted_at=_RETRACTED, retraction_note="nota")


def test_constructor_nota_en_blanco_viola_invariante():
    with pytest.raises(InvariantViolation):
        _finding(
            confidence=0.5, published_at=_PUBLISHED, retracted_at=_RETRACTED, retraction_note=" "
        )


def test_item_authors_por_defecto_es_none():
    item = Item(
        source="arxiv",
        external_id="2601.00001",
        title="t",
        abstract="a",
        categories=["astro-ph.EP"],
        published_at=_PUBLISHED,
        fetched_at=_PUBLISHED,
    )
    assert item.authors is None
