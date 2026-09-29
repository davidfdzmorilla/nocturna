"""`filter_measurements` sobre las salidas REALES del humo de T71.b (T71.c).

Fixtures en `tests/fixtures/t71c/` (`output_text` crudo real, prompt
`reader-measures-exp1`, ver el README de esa carpeta) y los abstracts
originales en `tests/fixtures/t71b/abstracts.json`. Ningún test llama a
Claude ni a la red: los datos ya están grabados.

Confirma, con datos reales y no inventados, el comportamiento que motivó la
salvaguarda de la anfitriona en `reader-v3.md`: el modelo, con el prompt
experimental precursor, devolvió `planet_name` como solo la letra
(`"b"`/`"e"`) para 2609.30038 -- las 7 medidas se descartan por
`HOST_NOT_FOUND` -- y la variante derivada a mano (con el nombre completo)
demuestra que, si el modelo sí completa la anfitriona, la salvaguarda las
deja pasar.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nocturna.application.agents.reader_measurements import DiscardReason, filter_measurements
from nocturna.application.agents.reader_output import parse_reader_v3_output

_T71C_DIR = Path(__file__).parent / "fixtures" / "t71c"
_T71B_ABSTRACTS_PATH = Path(__file__).parent / "fixtures" / "t71b" / "abstracts.json"


def _load_abstracts() -> dict[str, str]:
    records = json.loads(_T71B_ABSTRACTS_PATH.read_text())
    return {record["external_id"]: record["abstract"] for record in records}


def _load_titles() -> dict[str, str]:
    records = json.loads(_T71B_ABSTRACTS_PATH.read_text())
    return {record["external_id"]: record["title"] for record in records}


_ABSTRACTS = _load_abstracts()
_TITLES = _load_titles()


def _filter_fixture(filename: str, *, external_id: str):
    text = (_T71C_DIR / filename).read_text()
    parsed = parse_reader_v3_output(text)
    return filter_measurements(
        parsed.measurements, abstract=_ABSTRACTS[external_id], title=_TITLES[external_id]
    )


def test_2609_17025_sin_medidas_en_el_paper_no_conserva_nada():
    """El paper es sobre relaciones masa-radio de una población, no mide un
    planeta concreto: el propio modelo ya devolvió `measurements: []`."""
    result = _filter_fixture("2609.17025.reader-measures-exp1.json", external_id="2609.17025")

    assert result.kept == ()
    assert result.discarded == ()


def test_2609_20748_conserva_la_medida_de_rx_j0534_b():
    result = _filter_fixture("2609.20748.reader-measures-exp1.json", external_id="2609.20748")

    assert len(result.kept) == 1
    assert result.discarded == ()
    kept = result.kept[0]
    assert kept.planet_name == "RX J0534.0-0221 b"
    assert kept.parameter.value == "mass"


def test_2609_26894_conserva_las_tres_medidas_literature_de_toi_2109_b():
    result = _filter_fixture("2609.26894.reader-measures-exp1.json", external_id="2609.26894")

    assert len(result.kept) == 3
    assert result.discarded == ()
    assert {m.parameter.value for m in result.kept} == {"radius", "mass", "period"}
    assert all(m.planet_name == "TOI-2109 b" for m in result.kept)
    assert all(m.origin.value == "literature" for m in result.kept)


def test_2609_30038_real_con_nombres_de_solo_la_letra_descarta_las_siete_por_host_not_found():
    result = _filter_fixture("2609.30038.reader-measures-exp1.json", external_id="2609.30038")

    assert result.kept == ()
    assert len(result.discarded) == 7
    assert all(d.reason is DiscardReason.HOST_NOT_FOUND for d in result.discarded)


def test_2609_30038_derivada_con_nombres_completos_conserva_las_siete():
    """Variante DERIVADA A MANO (ver README de la fixture): mismos datos,
    solo `planet_name` completado con la anfitriona. Demuestra que la
    salvaguarda deja pasar la medida cuando el modelo sí sigue la
    instrucción de `reader-v3.md`."""
    result = _filter_fixture(
        "2609.30038.reader-measures-exp1.derived-fullname.json", external_id="2609.30038"
    )

    assert len(result.kept) == 7
    assert result.discarded == ()
    assert {m.planet_name for m in result.kept} == {"V1298 Tau b", "V1298 Tau e"}


@pytest.mark.parametrize(
    "filename",
    [
        "2609.17025.reader-measures-exp1.json",
        "2609.20748.reader-measures-exp1.json",
        "2609.26894.reader-measures-exp1.json",
        "2609.30038.reader-measures-exp1.json",
        "2609.30038.reader-measures-exp1.derived-fullname.json",
    ],
)
def test_las_cinco_fixtures_existen_y_parsean_como_reader_v3_output(filename):
    """Control mínimo de que las fixtures son de verdad salida real (o
    derivada, en el quinto caso) del Reader v3, no un JSON inventado a mano
    para el test."""
    text = (_T71C_DIR / filename).read_text()

    parsed = parse_reader_v3_output(text)

    assert isinstance(parsed.measurements, list)
