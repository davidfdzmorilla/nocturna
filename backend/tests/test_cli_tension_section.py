"""`_format_tension_section` y `exoplanet_catalog_from_config` (T74). Sin red ni BD."""

from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from helpers.exoplanet import make_measurement, make_solution

from nocturna import cli
from nocturna.application.use_cases.compute_tensions import (
    SkippedMeasurement,
    SkipReason,
    TensionReport,
)
from nocturna.domain.entities import MeasuredParameter
from nocturna.domain.tension import TensionResult, compare
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.exoplanet_archive.catalog import ExoplanetArchiveCatalog

REPO_ROOT = Path(__file__).resolve().parents[2]
MASS = MeasuredParameter.MASS

_B_ID = uuid4()
_E_ID = uuid4()
_X_ID = uuid4()


def _result(item_id, planet, measurement_values, prior):
    paper = [make_measurement(v, e, e, planet_name=planet) for v, e in measurement_values]
    return TensionResult(
        item_id=item_id,
        planet_name=planet,
        parameter=MASS,
        comparisons=tuple(compare(m, prior) for m in paper),
    )


def _report() -> TensionReport:
    default = make_solution(0.041, 0.017, 0.017, is_default=True)
    two_defaults_other = make_solution(0.050, 0.017, 0.017, reference="B", is_default=True)
    candidate = _result(_B_ID, "V1298 Tau b", [(0.52, 0.14), (0.67, 0.16)], default)  # min 3.40
    below = _result(
        _E_ID,
        "V1298 Tau e",
        [(0.10, 0.02)],
        make_solution(0.048, 0.013, 0.013, is_default=True, planet_name="V1298 Tau e"),
    )  # sigma 2.18
    ambiguous = TensionResult(
        item_id=_X_ID,
        planet_name="Foo 1 b",
        parameter=MASS,
        comparisons=(
            compare(make_measurement(0.52, 0.14, 0.14, planet_name="Foo 1 b"), default),
            compare(make_measurement(0.52, 0.14, 0.14, planet_name="Foo 1 b"), two_defaults_other),
        ),
    )
    skipped = (
        SkippedMeasurement(_X_ID, make_measurement(0.5, 0.1, 0.1), SkipReason.NOT_USABLE),
        SkippedMeasurement(_X_ID, make_measurement(0.5, 0.1, 0.1), SkipReason.UNMATCHED),
        SkippedMeasurement(_X_ID, make_measurement(0.5, 0.1, 0.1), SkipReason.UNMATCHED),
        SkippedMeasurement(_X_ID, make_measurement(0.5, 0.1, 0.1), SkipReason.NO_PRIORS),
    )
    return TensionReport(results=(candidate, below, ambiguous), skipped=skipped)


def _format(report: TensionReport, **overrides) -> str:
    kwargs = dict(
        external_ids={_B_ID: "2609.30038", _E_ID: "2609.30039"},
        readings_count=3,
        readings_with_measurements=2,
        total_measurements=10,
        runs_with_reader_v3=4,
        threshold_sigma=3.0,
        requests_made=5,
    )
    kwargs.update(overrides)
    return cli._format_tension_section(report, **kwargs)


def test_la_seccion_es_determinista():
    assert _format(_report()) == _format(_report())


def test_la_seccion_cuenta_medidas_resultados_y_candidatos():
    text = _format(_report())

    assert "Tensiones frente al NASA Exoplanet Archive:" in text
    assert (
        "lecturas con extracción de medidas (reader-v3): 3 (con al menos una medida: 2) "
        "en 4 Runs con reader-v3"
    ) in text
    # total 10, 1 no utilizable -> 9; 2 sin emparejar -> 7 emparejadas; 1 sin previas
    assert "medidas: total=10 utilizables=9 emparejadas=7 sin previas=1" in text
    assert "resultados: 3 (con referencia por defecto: 2, sin ella: 1)" in text
    assert "candidatos (threshold_sigma=3.0): 1; por Run con reader-v3: 0.25" in text
    assert "peticiones al archivo: 5" in text


def test_la_seccion_reparte_los_sigma_de_referencia_por_tramos():
    text = _format(_report())

    # b: 3.40 (tramo 3-5); e: 2.18 (tramo 2-3); el ambiguo no tiene referencia.
    assert "sigma de referencia por tramos: <1: 0 | 1-2: 0 | 2-3: 1 | 3-5: 1 | >=5: 0" in text


def test_la_seccion_lista_una_linea_por_resultado_con_enlace():
    lines = _format(_report()).splitlines()

    b = [ln for ln in lines if "V1298 Tau b" in ln]
    e = [ln for ln in lines if "V1298 Tau e" in ln]
    x = [ln for ln in lines if "Foo 1 b" in ln]
    assert b == [
        "  2609.30038 · V1298 Tau b · mass · sigma ref=3.40 · candidato=sí · "
        "https://exoplanetarchive.ipac.caltech.edu/overview/V1298%20Tau%20b"
    ]
    assert "sigma ref=2.18" in e[0] and "candidato=no" in e[0]
    assert "sigma ref=-" in x[0] and "candidato=no" in x[0]
    assert f"  {_X_ID} · Foo 1 b" in x[0]  # sin external_id conocido, cae al UUID


def test_sin_runs_con_reader_v3_la_tasa_es_nd():
    text = _format(_report(), runs_with_reader_v3=0)

    assert "por Run con reader-v3: n/d" in text


def test_informe_vacio_no_lista_resultados_ni_falla():
    text = _format(TensionReport(results=(), skipped=()), total_measurements=0, readings_count=0)

    assert "resultados: 0 (con referencia por defecto: 0, sin ella: 0)" in text
    assert "candidatos (threshold_sigma=3.0): 0" in text
    assert "·" not in text


def test_el_umbral_impreso_cambia_los_candidatos():
    text = _format(_report(), threshold_sigma=2.0)

    assert "candidatos (threshold_sigma=2.0): 2" in text


def test_tramo_de_frontera_5_sigma_cae_en_el_ultimo():
    default = make_solution(0.041, 0.017, 0.017, is_default=True)
    strong = _result(_B_ID, "V1298 Tau b", [(2.0, 0.1)], default)
    assert strong.reference_sigma() >= 5

    text = _format(TensionReport(results=(strong,), skipped=()))

    assert "3-5: 0 | >=5: 1" in text


@pytest.mark.anyio
async def test_exoplanet_catalog_from_config_usa_los_limites_del_toml():
    config = load_pipeline_config(REPO_ROOT / "config" / "pipeline.toml")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text="pl_name\n")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        catalog, client = cli.exoplanet_catalog_from_config(http, config)
        assert isinstance(catalog, ExoplanetArchiveCatalog)
        await client.query_csv("select pl_name from pscomppars")

    archive = config.sources.exoplanet_archive
    assert str(seen[0].url).startswith(archive.tap_url)
    assert client.requests_made == 1
    assert client._max_requests == archive.max_requests_per_night
