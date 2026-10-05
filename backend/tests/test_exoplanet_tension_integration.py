"""ComputeTensions + ExoplanetArchiveCatalog sin red (T74, T88).

Datos reales: `ps_v1298tau.csv` (filas de `ps` de V1298 Tau b y e, grabadas
del archivo, sembradas en un `ArchiveRepository` en memoria con
`soltype="Published Confirmed"` y una `releasedate` de captura, ver
`helpers.archive.archive_solutions_from_fixture`) y las medidas del paper
2609.30038 extraidas por el Reader
(`tests/fixtures/t71c/2609.30038.reader-measures-exp1.derived-fullname.json`).
El paper NO tiene fila propia en el archivo, asi que la exclusion de la
solucion propia se prueba con una fila sintetica (marcada).

Valores esperados calculados a mano con la formula del ADR 0015,
sigma = |x_p - x_i| / sqrt(e_p^2 + e_i^2), en M_earth (1 M_jup = 317.83 M_earth),
error del lado que mira al otro valor. Referencia: unica previa default
(Livingston 2026, b: 13.1 +-5.3; e: 15.3 +-4.2). El paper queda siempre por
encima, asi que e_p = err_minus (x317.83) y e_i = err_plus de la previa:

  b: 0.52(-0.14) 3.396 | 0.65(-0.18) 3.368 | 0.67(-0.16) 3.909 | 0.78(-0.20) 3.681
     minimo 3.3677 -> candidato a 3 sigma
  e: 0.40(-0.13) 2.693 | 0.66(-0.18) 3.390 | 0.71(-0.20) 3.302
     minimo 2.6927 -> NO candidato
  Suarez Mascareno 2022 (no es referencia): b 0.038..0.534 ; e 1.248..2.296
"""

import dataclasses
from datetime import UTC, datetime

import pytest
from fakes.clock import FakeClock
from helpers.archive import (
    fixture_text,
    load_t71c_measurements,
    make_archive,
    make_catalog,
    v1298_archive_solutions,
    wasp12_archive_solutions,
)
from helpers.exoplanet import make_item, make_measurement, make_period_rule, make_reading

from nocturna.application.use_cases.compute_tensions import ComputeTensions
from nocturna.domain.archive import ArchiveParameterValue
from nocturna.domain.entities import MeasuredParameter
from nocturna.domain.tension import EvaluationStatus

pytestmark = pytest.mark.anyio

MASS = MeasuredParameter.MASS
PAPER = "2609.30038"
MEASURES = "2609.30038.reader-measures-exp1.derived-fullname.json"
THRESHOLD = 3.0


def _by_planet(report):
    return {e.result.planet_name: e.result for e in report.evaluations if e.result is not None}


async def _run(archive=None, external_id=PAPER):
    catalog, client, seen = make_catalog(archive=archive)
    item = make_item(external_id)
    reading = make_reading(item.id, load_t71c_measurements(MEASURES))
    compute = ComputeTensions(
        catalog,
        threshold_sigma=THRESHOLD,
        period_rule=make_period_rule(),
        clock=FakeClock(datetime(2026, 10, 2, tzinfo=UTC)),
    )
    report = await compute([(item, reading)])
    return report, client, seen


async def test_v1298_b_es_candidato_y_e_no_a_tres_sigma():
    report, _, _ = await _run()

    results = _by_planet(report)
    assert set(results) == {"V1298 Tau b", "V1298 Tau e"}
    assert report.skipped == ()
    assert results["V1298 Tau b"].is_candidate(THRESHOLD) is True
    assert results["V1298 Tau e"].is_candidate(THRESHOLD) is False


async def test_sigma_de_referencia_calculada_a_mano():
    report, _, _ = await _run()

    results = _by_planet(report)
    assert results["V1298 Tau b"].reference_sigma() == pytest.approx(3.3677, abs=1e-3)
    assert results["V1298 Tau e"].reference_sigma() == pytest.approx(2.6927, abs=1e-3)


async def test_sigmas_individuales_frente_a_la_referencia():
    report, _, _ = await _run()

    def sigmas(planet):
        result = _by_planet(report)[planet]
        return [c.sigma for c in result.comparisons if c.prior.is_default]

    assert sigmas("V1298 Tau b") == pytest.approx([3.396, 3.368, 3.909, 3.681], abs=1e-3)
    assert sigmas("V1298 Tau e") == pytest.approx([2.693, 3.390, 3.302], abs=1e-3)


async def test_el_producto_medidas_por_previas_y_la_unica_referencia():
    report, _, _ = await _run()

    b = _by_planet(report)["V1298 Tau b"]
    e = _by_planet(report)["V1298 Tau e"]
    assert len(b.comparisons) == 4 * 2  # 4 medidas x (Suárez, Livingston)
    assert len(e.comparisons) == 3 * 3  # 3 medidas x (Suárez, Finociety, Livingston)
    assert {c.prior.reference for c in b.comparisons if c.prior.is_default} == {
        "Livingston et al. 2026"
    }


async def test_las_previas_que_no_son_referencia_no_deciden():
    report, _, _ = await _run()

    b = _by_planet(report)["V1298 Tau b"]
    suarez = [c.sigma for c in b.comparisons if "Suárez" in c.prior.reference]
    assert max(suarez) == pytest.approx(0.534, abs=1e-3)  # todas < 3 y aun asi es candidato


async def test_sin_alias_no_sale_ninguna_peticion():
    _, client, seen = await _run()

    # Soluciones e índice salen de la base; los nombres del Reader ya están en el índice.
    assert client.requests_made == 0
    assert seen == []


async def test_sin_fila_propia_en_el_archivo_no_se_excluye_nada():
    report, _, _ = await _run()

    b = _by_planet(report)["V1298 Tau b"]
    assert {c.prior.reference for c in b.comparisons} == {
        "Suárez Mascareño et al. 2022",
        "Livingston et al. 2026",
    }


def _archivo_con_fila_propia_sintetica():
    """SINTETICO: añade a V1298 Tau b una fila 'del propio paper' (arxiv_id
    2609.30038, default) con un valor casi igual al medido (0.52 M_jup =
    165.3 M_earth), que dejaria sigma ~ 0 si no se excluyera."""
    base = next(s for s in v1298_archive_solutions() if s.pl_name == "V1298 Tau b" and s.is_default)
    own = dataclasses.replace(
        base,
        pl_refname="<a href=https://ui.adsabs.harvard.edu/abs/2026arXiv2609.30038X/abstract> "
        "Propio et al. 2026 </a>",
        ref_key="2026arXiv2609.30038X",
        ref_text="Propio et al. 2026",
        arxiv_id=PAPER,
        mass=ArchiveParameterValue(value=165.3, err1=20.0, err2=-20.0, lim=0),
        radius=ArchiveParameterValue(),
        period=ArchiveParameterValue(),
        pl_bmassprov="Mass",
    )
    return make_archive(v1298_archive_solutions() + wasp12_archive_solutions() + [own]), own


async def test_la_solucion_del_propio_paper_se_excluye_por_arxiv_id():
    archive, own = _archivo_con_fila_propia_sintetica()
    report, _, _ = await _run(archive)

    b = _by_planet(report)["V1298 Tau b"]
    assert "Propio et al. 2026" not in {c.prior.reference for c in b.comparisons}
    assert b.reference_sigma() == pytest.approx(3.3677, abs=1e-3)
    assert b.is_candidate(THRESHOLD) is True


async def test_con_otro_external_id_la_fila_sintetica_si_cuenta_como_previa():
    """Control del test anterior: la exclusion depende de la igualdad de ids."""
    archive, _ = _archivo_con_fila_propia_sintetica()
    report, _, _ = await _run(archive, external_id="2609.99999")

    b = _by_planet(report)["V1298 Tau b"]
    propias = [c for c in b.comparisons if c.prior.reference == "Propio et al. 2026"]
    assert len(propias) == 4
    assert min(c.sigma for c in propias) < 0.1
    assert all(c.prior.arxiv_id == PAPER for c in propias)


async def test_planeta_ausente_del_archivo_espera_referencia_tras_consultar_el_alias():
    item = make_item()
    reading = make_reading(item.id, (make_measurement(0.5, 0.1, 0.1, planet_name="Foo 1 b"),))
    body = '{"manifest": {"lookup_status": "System Not Found"}}'
    catalog, client, seen = make_catalog(_alias_only(body))
    compute = ComputeTensions(
        catalog,
        threshold_sigma=THRESHOLD,
        period_rule=make_period_rule(),
        clock=FakeClock(datetime(2026, 10, 2, tzinfo=UTC)),
    )

    report = await compute([(item, reading)])

    (evaluation,) = report.evaluations
    assert evaluation.status == EvaluationStatus.AWAITING_REFERENCE
    assert evaluation.archive_planet_name is None
    assert client.requests_made == 1 and len(seen) == 1


def _alias_only(body):
    from helpers.archive import alias_handler

    return alias_handler(alias_body=body)


def test_los_datos_del_fixture_son_los_que_la_prueba_asume():
    """Guarda: si alguien regraba el fixture, los esperados a mano cambian."""
    text = fixture_text("ps_v1298tau.csv")

    assert "13.10000000,5.30000000,-5.30000000" in text
    assert "15.30000000,4.20000000,-4.20000000" in text
