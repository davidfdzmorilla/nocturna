"""Tests de `tests/experiments/reader_attribution.py` (T71.b, paso 4 del plan).

Ningún test de este fichero llama a Claude, sale a la red real, ni toca
PostgreSQL -- skill `testing-without-claude`. Los agentes se doblan con
`FakeLLMProvider` (`tests/fakes/llm.py`); `run_attribution` corre contra un
`AgentRunner` real con un `BudgetGuard` real sobre repositorios en memoria
(`tests/fakes/work.py`), mismo patrón que `tests/test_agent_runner.py`; el
cruce con el NASA Exoplanet Archive (`sigma_rows`) se sirve con
`httpx.MockTransport`, mismo patrón que `tests/test_exoplanet_viability_script.py`
(`_index_csv`/`_archive_client_for_ps`).

No se toca `tests/experiments/reader_attribution.py` ni
`tests/manual/test_reader_attribution_smoke.py` desde aquí: si un test
revela un fallo del módulo experimental, se queda en rojo, con el fallo
descrito en su docstring, para que el orquestador lo asigne al backend.

## Secciones

1. Esquema (`parse_experimental_output`/`ExperimentalReaderOutput`).
2. `evidence_in_abstract`.
3. `to_catalog_measurement`.
4. `evaluate` contra la fixture `tests/fixtures/t71b/abstracts.json`.
5. `sigma_rows` con `httpx.MockTransport`.
6. `run_attribution` con `FakeLLMProvider` + `BudgetGuard` real.
7. Subproceso: qué hay realmente en `sys.modules` tras importar el módulo.
8. Fixture de abstracts: forma mínima esperada.
"""

from __future__ import annotations

import json
import math
import subprocess
import sys
import textwrap
from datetime import UTC, datetime, time
from pathlib import Path

import httpx
import pytest
from experiments import reader_attribution as ra
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from fakes.work import (
    InMemoryAgentCallRepository,
    InMemoryItemRepository,
    InMemoryReadingRepository,
    InMemoryRunRepository,
    make_work_factory,
)

from nocturna.application.agents.parsing import InvalidAgentOutput
from nocturna.application.agents.runner import AgentRunner
from nocturna.application.budget import BudgetGuard, BudgetPolicy
from nocturna.domain.entities import Item, Run
from nocturna.domain.errors import LLMRateLimited
from nocturna.domain.llm import AgentRole

_TESTS_DIR = Path(__file__).resolve().parent
_WITHIN_WINDOW = datetime(2026, 1, 1, 2, 0, 0, tzinfo=UTC)

# Mismas constantes que `scripts/exoplanet_viability.py` (T71), declaradas
# aquí a propósito -- no importadas -- para que un cambio accidental de una
# de las dos copias se note en rojo (mismo criterio que
# `tests/test_exoplanet_viability_script.py`).
_M_JUP_IN_M_EARTH = 317.83
_R_JUP_IN_R_EARTH = 11.209


# ---------------------------------------------------------------------------
# Helpers de payload (sección 1) y de MeasurementOut (secciones 3-4)
# ---------------------------------------------------------------------------


def _valid_measurement(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "planet_name": "Planet X",
        "parameter": "mass",
        "value": 2.8,
        "err_plus": 0.5,
        "err_minus": 0.5,
        "unit": "M_jup",
        "limit": "none",
        "origin": "this_work",
        "evidence": "a literal quote",
    }
    base.update(overrides)
    return base


def _valid_payload(measurements: list[dict[str, object]] | None = None) -> dict[str, object]:
    return {
        "summary": "Resumen de prueba en español.",
        "objects": ["Planet X"],
        "claims": ["Un resultado concreto."],
        "interest_score": 4,
        "measurements": measurements if measurements is not None else [_valid_measurement()],
    }


def _measurement(**overrides: object) -> ra.MeasurementOut:
    base: dict[str, object] = {
        "planet_name": "Planet X",
        "parameter": "mass",
        "value": 2.8,
        "err_plus": 0.5,
        "err_minus": 0.5,
        "unit": "M_jup",
        "limit": "none",
        "origin": "this_work",
        "evidence": "quote",
    }
    base.update(overrides)
    return ra.MeasurementOut(**base)


# ===========================================================================
# 1. Esquema: parse_experimental_output / ExperimentalReaderOutput
# ===========================================================================


def test_payload_valido_completo_parsea():
    output = ra.parse_experimental_output(json.dumps(_valid_payload()))
    assert output.summary == "Resumen de prueba en español."
    assert output.interest_score == 4
    assert len(output.measurements) == 1
    assert output.measurements[0].planet_name == "Planet X"


def test_payload_valido_dentro_de_un_fence_json_parsea():
    text = "```json\n" + json.dumps(_valid_payload()) + "\n```"
    output = ra.parse_experimental_output(text)
    assert output.measurements[0].value == 2.8


def test_measurements_vacio_es_valido():
    output = ra.parse_experimental_output(json.dumps(_valid_payload(measurements=[])))
    assert output.measurements == []


def test_parameter_fuera_del_enum_es_invalido():
    payload = _valid_payload([_valid_measurement(parameter="luminosity")])
    with pytest.raises(InvalidAgentOutput):
        ra.parse_experimental_output(json.dumps(payload))


def test_unit_incoherente_mass_con_r_jup_es_invalido():
    payload = _valid_payload([_valid_measurement(parameter="mass", unit="R_jup")])
    with pytest.raises(InvalidAgentOutput):
        ra.parse_experimental_output(json.dumps(payload))


def test_error_negativo_es_invalido():
    payload = _valid_payload([_valid_measurement(err_plus=-0.1)])
    with pytest.raises(InvalidAgentOutput):
        ra.parse_experimental_output(json.dumps(payload))


def test_planet_name_en_blanco_es_invalido():
    payload = _valid_payload([_valid_measurement(planet_name="   ")])
    with pytest.raises(InvalidAgentOutput):
        ra.parse_experimental_output(json.dumps(payload))


def test_evidence_en_blanco_es_invalido():
    payload = _valid_payload([_valid_measurement(evidence="   ")])
    with pytest.raises(InvalidAgentOutput):
        ra.parse_experimental_output(json.dumps(payload))


def test_value_como_cadena_es_invalido_por_strict():
    payload = _valid_payload([_valid_measurement(value="5.02")])
    with pytest.raises(InvalidAgentOutput):
        ra.parse_experimental_output(json.dumps(payload))


def test_falta_measurements_es_invalido():
    payload = _valid_payload()
    del payload["measurements"]
    with pytest.raises(InvalidAgentOutput):
        ra.parse_experimental_output(json.dumps(payload))


# ===========================================================================
# 2. evidence_in_abstract
# ===========================================================================


def test_evidence_in_abstract_cita_literal_con_espacios_distintos_es_true():
    abstract = "Foo   bar\nbaz  qux."
    evidence = "Foo bar baz qux."
    assert ra.evidence_in_abstract(evidence, abstract) is True


def test_evidence_in_abstract_cita_inventada_es_false():
    abstract = "Foo   bar\nbaz  qux."
    evidence = "this text never appears in the abstract"
    assert ra.evidence_in_abstract(evidence, abstract) is False


# ===========================================================================
# 3. to_catalog_measurement
# ===========================================================================


def test_to_catalog_measurement_convierte_masa_m_jup_a_m_earth():
    measurement = _measurement(
        parameter="mass", unit="M_jup", value=2.0, err_plus=0.3, err_minus=0.2
    )
    result = ra.to_catalog_measurement(measurement)
    assert result is not None
    assert result.parameter == "mass_earth"
    assert math.isclose(result.value, 2.0 * _M_JUP_IN_M_EARTH)
    assert math.isclose(result.err_plus, 0.3 * _M_JUP_IN_M_EARTH)
    assert math.isclose(result.err_minus, 0.2 * _M_JUP_IN_M_EARTH)


def test_to_catalog_measurement_convierte_radio_r_jup_a_r_earth():
    measurement = _measurement(
        parameter="radius", unit="R_jup", value=1.3, err_plus=0.05, err_minus=0.04
    )
    result = ra.to_catalog_measurement(measurement)
    assert result is not None
    assert result.parameter == "radius_earth"
    assert math.isclose(result.value, 1.3 * _R_JUP_IN_R_EARTH)
    assert math.isclose(result.err_plus, 0.05 * _R_JUP_IN_R_EARTH)
    assert math.isclose(result.err_minus, 0.04 * _R_JUP_IN_R_EARTH)


def test_to_catalog_measurement_day_no_cambia():
    measurement = _measurement(
        parameter="period", unit="day", value=10.0, err_plus=1.0, err_minus=1.0
    )
    result = ra.to_catalog_measurement(measurement)
    assert result is not None
    assert result.parameter == "period_days"
    assert result.value == 10.0
    assert result.err_plus == 1.0
    assert result.err_minus == 1.0


def test_to_catalog_measurement_none_si_limit_distinto_de_none():
    measurement = _measurement(
        parameter="mass", unit="M_jup", value=3.0, limit="upper", err_plus=None, err_minus=None
    )
    assert ra.to_catalog_measurement(measurement) is None


def test_to_catalog_measurement_none_si_falta_un_error():
    measurement = _measurement(
        parameter="mass", unit="M_jup", value=3.0, limit="none", err_plus=0.1, err_minus=None
    )
    assert ra.to_catalog_measurement(measurement) is None


def test_to_catalog_measurement_none_si_origin_es_literature():
    measurement = _measurement(
        parameter="mass",
        unit="M_jup",
        value=3.0,
        limit="none",
        err_plus=0.1,
        err_minus=0.1,
        origin="literature",
    )
    assert ra.to_catalog_measurement(measurement) is None


# ===========================================================================
# 4. evaluate contra la fixture tests/fixtures/t71b/abstracts.json
# ===========================================================================

_ABSTRACTS_BY_ID = {a["external_id"]: a["abstract"] for a in ra.load_abstracts()}


def test_evaluate_20748_correcto_es_ok():
    abstract = _ABSTRACTS_BY_ID["2609.20748"]
    measurement = _measurement(
        planet_name="RX J0534.0-0221 b",
        parameter="mass",
        value=2.8,
        err_plus=0.5,
        err_minus=0.5,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.20748", [measurement], abstract)
    assert result.passed is True


def test_evaluate_20748_masa_this_work_en_twa7b_es_fallo():
    abstract = _ABSTRACTS_BY_ID["2609.20748"]
    measurement = _measurement(
        planet_name="TWA 7 b",
        parameter="mass",
        value=1.0,
        err_plus=0.1,
        err_minus=0.1,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.20748", [measurement], abstract)
    assert result.passed is False
    assert any(
        row.status == "FALLO" and "objeto de comparación" in row.reason for row in result.rows
    )


def test_evaluate_17025_con_258_m_earth_como_masa_es_fallo():
    """Segundo límite del régimen saturniano (el primero, 54, tiene su propio
    test): cualquiera de los dos valores trampa atribuido como masa de un
    planeta concreto debe ser FALLO."""
    abstract = _ABSTRACTS_BY_ID["2609.17025"]
    measurement = _measurement(
        planet_name="Some Planet",
        parameter="mass",
        value=258.0,
        err_plus=11.0,
        err_minus=11.0,
        unit="M_earth",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.17025", [measurement], abstract)
    assert result.passed is False
    assert any(row.status == "FALLO" for row in result.rows)


def test_evaluate_17025_con_54_m_earth_como_masa_es_fallo():
    abstract = _ABSTRACTS_BY_ID["2609.17025"]
    measurement = _measurement(
        planet_name="Some Planet",
        parameter="mass",
        value=54.0,
        err_plus=3.0,
        err_minus=3.0,
        unit="M_earth",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.17025", [measurement], abstract)
    assert result.passed is False
    assert any(row.status == "FALLO" for row in result.rows)


def _toi2109_measurements(abstract: str, *, origin: str) -> list[ra.MeasurementOut]:
    return [
        _measurement(
            planet_name="TOI-2109 b",
            parameter="radius",
            value=1.347,
            err_plus=0.047,
            err_minus=0.047,
            unit="R_jup",
            limit="none",
            origin=origin,
            evidence=abstract[:80],
        ),
        _measurement(
            planet_name="TOI-2109 b",
            parameter="mass",
            value=5.02,
            err_plus=0.75,
            err_minus=0.75,
            unit="M_jup",
            limit="none",
            origin=origin,
            evidence=abstract[:80],
        ),
        _measurement(
            planet_name="TOI-2109 b",
            parameter="period",
            value=0.67247479,
            err_plus=0.00000028,
            err_minus=0.00000028,
            unit="day",
            limit="none",
            origin=origin,
            evidence=abstract[:80],
        ),
    ]


def test_evaluate_26894_correcto_origin_literature_sin_wasp12b_es_ok():
    abstract = _ABSTRACTS_BY_ID["2609.26894"]
    measurements = _toi2109_measurements(abstract, origin="literature")
    result = ra.evaluate("2609.26894", measurements, abstract)
    assert result.passed is True


def test_evaluate_26894_origin_this_work_es_aviso_no_fallo():
    abstract = _ABSTRACTS_BY_ID["2609.26894"]
    measurements = _toi2109_measurements(abstract, origin="this_work")
    result = ra.evaluate("2609.26894", measurements, abstract)
    assert result.passed is True
    assert any(row.status == "AVISO" for row in result.rows)
    assert not any(row.status == "FALLO" for row in result.rows)


def test_evaluate_26894_medida_en_wasp12b_es_fallo():
    abstract = _ABSTRACTS_BY_ID["2609.26894"]
    measurement = _measurement(
        planet_name="WASP-12 b",
        parameter="mass",
        value=5.02,
        err_plus=0.75,
        err_minus=0.75,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.26894", [measurement], abstract)
    assert result.passed is False
    assert any(row.status == "FALLO" and "WASP-12 b" in row.reason for row in result.rows)


def test_evaluate_30038_b_y_e_con_valores_de_sus_conjuntos_es_ok():
    abstract = _ABSTRACTS_BY_ID["2609.30038"]
    measurement_b = _measurement(
        planet_name="V1298 Tau b",
        parameter="mass",
        value=0.67,
        err_plus=0.16,
        err_minus=0.16,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    measurement_e = _measurement(
        planet_name="V1298 Tau e",
        parameter="mass",
        value=0.66,
        err_plus=0.17,
        err_minus=0.18,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.30038", [measurement_b, measurement_e], abstract)
    assert result.passed is True


def test_evaluate_30038_valor_de_b_asignado_a_e_es_fallo():
    abstract = _ABSTRACTS_BY_ID["2609.30038"]
    measurement = _measurement(
        planet_name="V1298 Tau e",
        parameter="mass",
        value=0.67,
        err_plus=0.16,
        err_minus=0.16,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.30038", [measurement], abstract)
    assert result.passed is False
    assert any(row.status == "FALLO" and "asignado a e" in row.reason for row in result.rows)


def test_evaluate_30038_cifra_para_c_es_fallo():
    abstract = _ABSTRACTS_BY_ID["2609.30038"]
    measurement = _measurement(
        planet_name="V1298 Tau c",
        parameter="mass",
        value=0.3,
        err_plus=0.05,
        err_minus=0.05,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.30038", [measurement], abstract)
    assert result.passed is False
    assert any(row.status == "FALLO" and "V1298 Tau c/d" in row.expected for row in result.rows)


def test_evaluate_30038_periodo_sin_error_como_medida_es_fallo():
    abstract = _ABSTRACTS_BY_ID["2609.30038"]
    measurement = _measurement(
        planet_name="V1298 Tau b",
        parameter="period",
        value=24.1,
        err_plus=None,
        err_minus=None,
        unit="day",
        limit="none",
        origin="this_work",
        evidence=abstract[:80],
    )
    result = ra.evaluate("2609.30038", [measurement], abstract)
    assert result.passed is False
    assert any(
        row.status == "FALLO" and "periodo citado sin error" in row.reason for row in result.rows
    )


def test_evaluate_evidence_no_literal_es_fallo():
    abstract = _ABSTRACTS_BY_ID["2609.30038"]
    measurement = _measurement(
        planet_name="V1298 Tau b",
        parameter="mass",
        value=0.67,
        err_plus=0.16,
        err_minus=0.16,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence="this exact sentence does not appear anywhere in the abstract",
    )
    result = ra.evaluate("2609.30038", [measurement], abstract)
    assert result.passed is False
    assert any(row.status == "FALLO" and "cita literal" in row.reason for row in result.rows)


def test_evaluate_sin_medidas_es_fallo_sin_salida():
    """`measurements=[]` representa "sin salida utilizable del Reader" (el
    caso de un `AttemptRecord` con `outcome != "ok"`, ver `run_attribution`):
    para un abstract que sí tiene una medida esperada, una lista vacía debe
    evaluarse como FALLO, nunca como OK por omisión."""
    abstract = _ABSTRACTS_BY_ID["2609.20748"]
    result = ra.evaluate("2609.20748", [], abstract)
    assert result.passed is False
    assert any(
        row.status == "FALLO" and "no se encontró la medida esperada" in row.reason
        for row in result.rows
    )


# ===========================================================================
# 5. sigma_rows con httpx.MockTransport
# ===========================================================================


def _index_csv(rows: list[tuple[str, str, str, str, str, str]]) -> bytes:
    header = "pl_name,hostname,hd_name,hip_name,tic_id,sy_pnum\n"
    body = "".join(",".join(row) + "\n" for row in rows)
    return (header + body).encode()


_PS_HEADER = (
    "pl_name,hostname,default_flag,pl_refname,pl_bmasse,pl_bmasseerr1,pl_bmasseerr2,"
    "pl_bmasselim,pl_rade,pl_radeerr1,pl_radeerr2,pl_radelim,pl_orbper,pl_orbpererr1,"
    "pl_orbpererr2,pl_orbperlim\n"
)


def _archive_client(index_csv: bytes, ps_csv: bytes, *, max_requests: int) -> object:
    def handle(request: httpx.Request) -> httpx.Response:
        query = request.url.params.get("query", "")
        content = index_csv if "pscomppars" in query else ps_csv
        return httpx.Response(200, content=content, headers={"content-type": "text/csv"})

    http = httpx.Client(transport=httpx.MockTransport(handle))
    ev = ra._load_exoplanet_viability_module()
    return ev.ArchiveClient(http, max_requests=max_requests, spacing_s=0.0, sleep=lambda s: None)


def _hand_sigma(
    paper_value: float,
    paper_err_plus: float,
    paper_err_minus: float,
    prior_value: float,
    prior_err_plus: float,
    prior_err_minus: float,
) -> float:
    """Réplica independiente de la fórmula provisional de `sigma()`
    (`scripts/exoplanet_viability.py`), para comprobar `sigma_rows` sin
    depender de la propia implementación que se está probando."""
    if paper_value == prior_value:
        return 0.0
    if paper_value > prior_value:
        paper_err = paper_err_minus
        prior_err = prior_err_plus
    else:
        paper_err = paper_err_plus
        prior_err = prior_err_minus
    distance = abs(paper_value - prior_value)
    return distance / math.sqrt(paper_err**2 + prior_err**2)


def test_sigma_rows_planeta_emparejado_con_dos_soluciones_sigma_calculado_a_mano():
    index_csv = _index_csv([("V1298 Tau b", "V1298 Tau", "", "", "", "1")])
    ps_csv = (
        _PS_HEADER
        + "V1298 Tau b,V1298 Tau,1,Livingston et al. 2026,200,10,-8,0,,,,0,,,,0\n"
        + "V1298 Tau b,V1298 Tau,0,Livingston et al. 2026b,230,15,-12,0,,,,0,,,,0\n"
    ).encode()
    client = _archive_client(index_csv, ps_csv, max_requests=3)

    measurement = _measurement(
        planet_name="V1298 Tau b",
        parameter="mass",
        value=0.67,
        err_plus=0.16,
        err_minus=0.16,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence="quote",
    )
    attributed = {"2609.30038": [measurement]}

    rows = ra.sigma_rows(attributed, client)

    assert client.requests_made <= 3
    assert len(rows) == 2
    assert {row.refname for row in rows} == {
        "Livingston et al. 2026",
        "Livingston et al. 2026b",
    }

    catalog_value = 0.67 * _M_JUP_IN_M_EARTH
    catalog_err_plus = 0.16 * _M_JUP_IN_M_EARTH
    catalog_err_minus = 0.16 * _M_JUP_IN_M_EARTH
    expected_by_refname = {
        "Livingston et al. 2026": _hand_sigma(
            catalog_value, catalog_err_plus, catalog_err_minus, 200.0, 10.0, 8.0
        ),
        "Livingston et al. 2026b": _hand_sigma(
            catalog_value, catalog_err_plus, catalog_err_minus, 230.0, 15.0, 12.0
        ),
    }
    for row in rows:
        assert row.external_id == "2609.30038"
        assert row.planet_name == "V1298 Tau b"
        assert row.parameter == "mass"
        assert row.paper_value == 0.67
        assert row.paper_unit == "M_jup"
        assert math.isclose(row.sigma, expected_by_refname[row.refname], rel_tol=1e-9)


def test_sigma_rows_rx_j0534_no_emparejado():
    index_csv = _index_csv([])  # RX J0534.0-0221 b no está en el archivo (planeta nuevo)
    ps_csv = _PS_HEADER.encode()
    client = _archive_client(index_csv, ps_csv, max_requests=3)

    measurement = _measurement(
        planet_name="RX J0534.0-0221 b",
        parameter="mass",
        value=2.8,
        err_plus=0.5,
        err_minus=0.5,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence="quote",
    )
    attributed = {"2609.20748": [measurement]}

    rows = ra.sigma_rows(attributed, client)

    assert rows == []
    assert client.requests_made <= 3


def test_sigma_rows_nunca_mas_de_3_peticiones_con_planeta_emparejado_y_no_emparejado():
    index_csv = _index_csv([("V1298 Tau b", "V1298 Tau", "", "", "", "1")])
    ps_csv = (
        _PS_HEADER + "V1298 Tau b,V1298 Tau,1,Livingston et al. 2026,200,10,-8,0,,,,0,,,,0\n"
    ).encode()
    client = _archive_client(index_csv, ps_csv, max_requests=3)

    matched = _measurement(
        planet_name="V1298 Tau b",
        parameter="mass",
        value=0.67,
        err_plus=0.16,
        err_minus=0.16,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence="quote",
    )
    unmatched = _measurement(
        planet_name="RX J0534.0-0221 b",
        parameter="mass",
        value=2.8,
        err_plus=0.5,
        err_minus=0.5,
        unit="M_jup",
        limit="none",
        origin="this_work",
        evidence="quote",
    )
    attributed = {"2609.30038": [matched], "2609.20748": [unmatched]}

    rows = ra.sigma_rows(attributed, client)

    assert client.requests_made <= 3
    assert len(rows) == 1
    assert rows[0].planet_name == "V1298 Tau b"


# ===========================================================================
# 6. run_attribution con FakeLLMProvider + BudgetGuard real
# ===========================================================================


def _policy(**overrides: object) -> BudgetPolicy:
    defaults: dict[str, object] = {
        "nightly_tokens": 100_000,
        "editor_reserve_tokens": 0,
        "max_items_per_night": 10,
        "max_turns_per_agent": 1,
        "max_editor_calls_per_night": 2,
        "max_calls_per_item": 5,
        "item_timeout_s": 180,
        "editor_timeout_s": 300,
        "run_timeout_s": 16_200,
        "window_start": time(0, 0),
        "window_hard_stop": time(4, 45),
        "weekly_reset_weekday": 0,
        "weekly_reset_hour": 0,
        "reset_day_multiplier": 1.0,
    }
    defaults.update(overrides)
    return BudgetPolicy(**defaults)


def _make_run(**overrides: object) -> Run:
    defaults: dict[str, object] = {"started_at": _WITHIN_WINDOW, "budget_tokens": 100_000}
    defaults.update(overrides)
    return Run(**defaults)


def _make_environment(
    *, policy: BudgetPolicy | None = None, run: Run | None = None
) -> tuple[object, InMemoryAgentCallRepository]:
    resolved_policy = policy or _policy()
    resolved_run = run or _make_run(budget_tokens=resolved_policy.nightly_tokens)
    runs = InMemoryRunRepository(resolved_run)
    items = InMemoryItemRepository()
    readings = InMemoryReadingRepository()
    agent_calls = InMemoryAgentCallRepository()
    guard = BudgetGuard(
        run_id=resolved_run.id,
        policy=resolved_policy,
        runs=runs,
        agent_calls=agent_calls,
        clock=FakeClock(_WITHIN_WINDOW),
    )
    work = make_work_factory(
        guard=guard, runs=runs, items=items, readings=readings, agent_calls=agent_calls
    )
    return work, agent_calls


def _make_experiment_runner(
    *, work: object, provider: FakeLLMProvider, max_attempts: int, estimated_tokens: int = 500
) -> AgentRunner:
    return AgentRunner(
        work=work,
        provider=provider,
        role=AgentRole.READER,
        model="claude-sonnet-test",
        system_prompt="prompt experimental de prueba",
        prompt_version=ra.EXPERIMENT_PROMPT_VERSION,
        estimated_tokens=estimated_tokens,
        max_turns=1,
        max_attempts=max_attempts,
    )


def _items(n: int) -> list[Item]:
    abstracts = ra.load_abstracts()[:n]
    return [
        Item(
            source="arxiv",
            external_id=a["external_id"],
            title=a["title"],
            abstract=a["abstract"],
            categories=["astro-ph.EP"],
            published_at=_WITHIN_WINDOW,
            fetched_at=_WITHIN_WINDOW,
        )
        for a in abstracts
    ]


@pytest.mark.anyio
async def test_run_attribution_json_invalido_y_luego_valido_da_dos_intentos_y_ok():
    work, _agent_calls = _make_environment()
    provider = FakeLLMProvider()
    provider.respond(AgentRole.READER, raw="not a json body at all", tokens_in=100, tokens_out=10)
    provider.respond(AgentRole.READER, json=_valid_payload(), tokens_in=200, tokens_out=50)
    runner = _make_experiment_runner(work=work, provider=provider, max_attempts=2)

    records = await ra.run_attribution(runner, _items(1))

    assert len(records) == 1
    assert records[0].outcome == "ok"
    assert records[0].attempts == 2
    assert len(provider.calls) == 2


@pytest.mark.anyio
async def test_run_attribution_dos_invalidos_da_invalid_output_sin_tercera_llamada():
    work, _agent_calls = _make_environment()
    provider = FakeLLMProvider()
    provider.respond(AgentRole.READER, raw="still not json", tokens_in=100, tokens_out=10)
    provider.respond(AgentRole.READER, raw="also not json", tokens_in=100, tokens_out=10)
    runner = _make_experiment_runner(work=work, provider=provider, max_attempts=2)

    records = await ra.run_attribution(runner, _items(1))

    assert len(records) == 1
    assert records[0].outcome == "invalid_output"
    assert records[0].attempts == 2
    assert len(provider.calls) == 2


@pytest.mark.anyio
async def test_run_attribution_presupuesto_agotado_el_tercer_abstract_no_se_ejecuta():
    policy = _policy(nightly_tokens=12_000, editor_reserve_tokens=0)
    run = _make_run(budget_tokens=12_000)
    work, _agent_calls = _make_environment(policy=policy, run=run)
    provider = FakeLLMProvider()
    provider.respond(AgentRole.READER, json=_valid_payload(), tokens_in=5_000, tokens_out=1_000)
    provider.respond(AgentRole.READER, json=_valid_payload(), tokens_in=5_000, tokens_out=1_000)
    runner = _make_experiment_runner(
        work=work, provider=provider, max_attempts=1, estimated_tokens=500
    )

    records = await ra.run_attribution(runner, _items(3))

    assert len(records) == 3
    assert records[0].outcome == "ok"
    assert records[1].outcome == "ok"
    assert records[2].outcome == "not_run"
    # El motivo ahora lleva el nombre de la subclase concreta de
    # `BudgetDenied` (`type(exc).__name__`, ver `reader_attribution.py`):
    # agotar `nightly_tokens` deniega con `DenyReason.BUDGET_EXHAUSTED`,
    # que `BudgetGuard.authorize` traduce a `BudgetExceeded`
    # (`application/budget.py::_EXCEPTION_BY_REASON`).
    assert records[2].note == "no ejecutado: presupuesto (BudgetExceeded)"
    # exactamente las llamadas autorizadas: dos, ni una más.
    assert len(provider.calls) == 2


@pytest.mark.anyio
async def test_run_attribution_rate_limited_deja_de_llamar_al_resto():
    work, _agent_calls = _make_environment()
    provider = FakeLLMProvider()
    provider.fail(
        AgentRole.READER, error=LLMRateLimited("límite de tasa", tokens_in=1_000, tokens_out=100)
    )
    runner = _make_experiment_runner(work=work, provider=provider, max_attempts=2)

    records = await ra.run_attribution(runner, _items(2))

    assert len(records) == 2
    assert records[0].outcome == "rate_limited"
    assert records[0].tokens_spent == 1_100
    assert records[1].outcome == "not_run"
    assert "límite de tasa" in records[1].note
    assert len(provider.calls) == 1


@pytest.mark.anyio
async def test_run_attribution_registra_agentcall_con_prompt_version_experimental():
    work, agent_calls = _make_environment()
    provider = FakeLLMProvider()
    provider.respond(AgentRole.READER, json=_valid_payload(), tokens_in=200, tokens_out=50)
    runner = _make_experiment_runner(work=work, provider=provider, max_attempts=1)

    records = await ra.run_attribution(runner, _items(1))

    assert records[0].outcome == "ok"
    assert len(agent_calls.calls) == 1
    assert agent_calls.calls[0].prompt_version == "reader-measures-exp1"
    assert agent_calls.calls[0].prompt_version == ra.EXPERIMENT_PROMPT_VERSION


# ===========================================================================
# 7. Subproceso: qué hay en sys.modules tras importar el módulo experimental
# ===========================================================================

# `experiments.reader_attribution` importa `nocturna.application.agents.runner`
# (para `AgentRunner`/`AttemptOutcome`) y `nocturna.application.use_cases.read_item`
# (para `ReadItem._build_prompt`) -- ninguno de los dos importa
# `infrastructure/` (ver docstring de `runner.py`, sección "Qué NO hace este
# runner"), así que el requisito fuerte SÍ se cumple aquí: ni
# `claude_agent_sdk` ni `nocturna.infrastructure.llm` acaban en `sys.modules`
# solo por importar el módulo experimental. Comprobado en subproceso
# interprete-limpio, mismo patrón que `tests/test_api_no_claude_import.py`.
_SUBPROCESS_SCRIPT = textwrap.dedent(
    """
    import sys
    from pathlib import Path

    TESTS_DIR = Path(sys.argv[1])
    sys.path.insert(0, str(TESTS_DIR))

    import experiments.reader_attribution  # noqa: F401 -- solo importar

    forbidden_prefixes = ("claude_agent_sdk", "nocturna.infrastructure.llm")
    leaked = sorted(
        name
        for name in sys.modules
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    )
    if leaked:
        print(f"módulos prohibidos en sys.modules: {leaked}", file=sys.stderr)
        sys.exit(1)
    print("OK")
    """
)


def test_importar_experiments_reader_attribution_no_mete_claude_agent_sdk_en_sys_modules():
    result = subprocess.run(
        [sys.executable, "-c", _SUBPROCESS_SCRIPT, str(_TESTS_DIR)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, (
        f"subproceso falló (code={result.returncode})\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
    assert "OK" in result.stdout
    assert "Traceback" not in result.stderr


# ===========================================================================
# 8. Fixture de abstracts: forma mínima esperada
# ===========================================================================


def test_fixture_abstracts_tiene_los_cuatro_external_id_y_abstracts_no_vacios():
    abstracts = ra.load_abstracts()
    external_ids = {a["external_id"] for a in abstracts}
    assert external_ids == {"2609.17025", "2609.20748", "2609.26894", "2609.30038"}
    for abstract in abstracts:
        assert abstract["abstract"].strip() != ""
        assert abstract["title"].strip() != ""
