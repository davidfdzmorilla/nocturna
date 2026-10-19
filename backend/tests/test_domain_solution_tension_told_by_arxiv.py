"""T95: regla pura `told_by_arxiv` y constante `SAME_PAPER_ARXIV` (sin IO)."""

from __future__ import annotations

from helpers.cross_path_dedup import HD65216_ARXIV_ID, archive_tension

from nocturna.domain.entities import MeasuredParameter
from nocturna.domain.solution_tension import SAME_PAPER_ARXIV, SkipReason, told_by_arxiv

PERIOD = MeasuredParameter.PERIOD
MASS = MeasuredParameter.MASS


def test_la_constante_es_un_motivo_de_seleccion_y_no_del_enum_skip_reason() -> None:
    assert SAME_PAPER_ARXIV == "same_paper_arxiv"
    assert SAME_PAPER_ARXIV not in {r.value for r in SkipReason}


def test_cierto_si_la_clave_paper_planeta_parametro_esta_en_el_conjunto() -> None:
    tension = archive_tension(None)
    assert told_by_arxiv(tension, frozenset({(HD65216_ARXIV_ID, "HD 65216 c", PERIOD)}))


def test_falso_con_otro_parametro_planeta_o_paper_y_con_conjunto_vacio() -> None:
    tension = archive_tension(None)
    assert not told_by_arxiv(tension, frozenset())
    assert not told_by_arxiv(tension, frozenset({(HD65216_ARXIV_ID, "HD 65216 c", MASS)}))
    assert not told_by_arxiv(tension, frozenset({(HD65216_ARXIV_ID, "HD 65216 b", PERIOD)}))
    assert not told_by_arxiv(tension, frozenset({("2501.00001", "HD 65216 c", PERIOD)}))


def test_falso_si_la_alta_no_tiene_arxiv_id() -> None:
    tension = archive_tension(None, arxiv_id=None)
    assert not told_by_arxiv(tension, frozenset({(HD65216_ARXIV_ID, "HD 65216 c", PERIOD)}))
