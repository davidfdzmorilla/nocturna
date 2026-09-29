"""T71.c: `evidence` debe contener la representación numérica de `value`
(comparación tolerante: ceros finales/iniciales equivalentes), no solo ser
una cita literal del abstract.

Puro: sin IO, sin red, sin base de datos. Ningún test llama a Claude.

`DiscardReason.VALUE_NOT_IN_EVIDENCE` es el motivo de descarte para "la
evidencia no cita el número del valor" -- distinto de `EVIDENCE_NOT_LITERAL`
(que solo comprueba que `evidence` es subcadena literal del abstract, con
independencia de qué número contenga). El tercer bloque (regresión con las
fixtures reales de T71.c) fija que los recuentos de conservadas no cambian:
en los datos reales, la evidencia sí cita el número.
"""

from __future__ import annotations

import json
from pathlib import Path

from nocturna.application.agents.reader_measurements import filter_measurements
from nocturna.application.agents.reader_output import parse_reader_v3_output

_T71C_DIR = Path(__file__).parent / "fixtures" / "t71c"
_T71B_ABSTRACTS_PATH = Path(__file__).parent / "fixtures" / "t71b" / "abstracts.json"


def _measurement_dict(**overrides: object) -> dict:
    payload = {
        "planet_name": "V1298 Tau b",
        "parameter": "mass",
        "value": 0.52,
        "err_plus": 0.12,
        "err_minus": 0.14,
        "unit": "M_jup",
        "limit": "none",
        "origin": "this_work",
        "evidence": "0.52 (+0.12/-0.14) M_jup",
    }
    payload.update(overrides)
    return payload


_ABSTRACT = (
    "The planet V1298 Tau b's mass was estimated via SED fitting to be consistent with a "
    "sub-Jupiter scenario, based on atmospheric modelling of the system."
)


# =============================================================================
# evidence literal del abstract, pero sin el número de 'value'
# =============================================================================


def test_evidencia_literal_sin_el_numero_del_value_se_descarta_por_value_not_in_evidence():
    """`evidence` ("mass was estimated via SED fitting") es cita literal del
    abstract -- pasa `evidence_in_abstract` -- pero no contiene "0.52" en
    ninguna forma: se descarta con `DiscardReason.VALUE_NOT_IN_EVIDENCE`."""
    measurement = _measurement_dict(evidence="mass was estimated via SED fitting")

    result = filter_measurements([measurement], abstract=_ABSTRACT, title="")

    assert result.kept == (), (
        "la medida debería descartarse: 'evidence' no cita el número de 'value' (0.52) "
        "en ninguna forma reconocible"
    )
    assert len(result.discarded) == 1
    assert result.discarded[0].reason.value == "value_not_in_evidence"


def test_discard_reason_tiene_el_miembro_value_not_in_evidence():
    """Constata directamente la presencia del motivo en el enum."""
    from nocturna.application.agents.reader_measurements import DiscardReason

    assert hasattr(DiscardReason, "VALUE_NOT_IN_EVIDENCE")


# =============================================================================
# Regresión: los recuentos de conservadas de las fixtures reales de T71.c no
# cambian con esta comprobación -- en los datos reales, la evidencia sí cita
# el número
# =============================================================================


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


def test_2609_20748_sigue_conservando_una_medida_con_value_en_evidence():
    result = _filter_fixture("2609.20748.reader-measures-exp1.json", external_id="2609.20748")

    assert len(result.kept) == 1


def test_2609_26894_sigue_conservando_tres_medidas_con_value_en_evidence():
    result = _filter_fixture("2609.26894.reader-measures-exp1.json", external_id="2609.26894")

    assert len(result.kept) == 3


def test_2609_30038_derivada_sigue_conservando_siete_medidas_con_value_en_evidence():
    result = _filter_fixture(
        "2609.30038.reader-measures-exp1.derived-fullname.json", external_id="2609.30038"
    )

    assert len(result.kept) == 7
