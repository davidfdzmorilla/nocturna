"""T83: la confirmación independiente exige que la referencia sea ajena al paper.

Dominio puro, sin BD ni red. `confirmation_eligible` e
`independent_confirmation_from` reclasifican la referencia de T88 con
`classify_solution`, así que también protegen las evaluaciones `evaluated`
guardadas antes de T83 (cuya referencia podía ser el propio paper citado por
bibcode de revista).
"""

from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from helpers.exoplanet import make_measurement, make_own_solution_rule
from helpers.measurement_findings import chakraborty_b, hip67522_b

from nocturna.domain.entities import MeasurementUnit
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.measurement_findings import (
    confirmation_eligible,
    independent_confirmation_from,
)
from nocturna.domain.own_solution import SolutionProvenance, classify_solution
from nocturna.domain.tension import EvaluationStatus, TensionEvaluation, TensionResult, compare

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
HIP_PAPER = "2609.35979"
RULE = make_own_solution_rule()
URL = "https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20b"
M_E = MeasurementUnit.M_EARTH


def _kwargs(external_id=HIP_PAPER):
    return {
        "item_published_at": PUBLISHED,
        "now": NOW,
        "max_sigma": 2.0,
        "window_days": 30,
        "item_external_id": external_id,
        "own_rule": RULE,
    }


def _journal_reference(value, **over):
    """Solución con bibcode de revista (sin `arxiv_id`) de HIP 67522 b, reciente."""
    base = replace(
        chakraborty_b(),
        arxiv_id=None,
        value=value,
        err_plus=7.6,
        err_minus=7.8,
        pl_pubdate="2026-10",
        releasedate=date(2026, 10, 3),
    )
    return replace(base, **over) if over else base


def _evaluation(reference, external_item_id=None):
    return hip67522_b(external_item_id or uuid4(), reference=reference)


def test_referencia_con_arxiv_id_distinto_es_elegible_hip_67522_b():
    ev = hip67522_b(uuid4())

    assert ev.result.reference().arxiv_id == "2606.18045"
    assert confirmation_eligible(ev, **_kwargs())


def test_confirmacion_de_hip_67522_b_se_construye_con_los_nuevos_parametros():
    ev = hip67522_b(uuid4())

    ic = independent_confirmation_from(ev, archive_url=URL, **_kwargs())

    assert ic.reference.arxiv_id == "2606.18045"
    assert ic.sigmas == pytest.approx((1.4242, 0.7476), abs=5e-5)


def test_referencia_con_arxiv_id_igual_al_del_paper_no_es_elegible():
    ev = _evaluation(replace(chakraborty_b(), arxiv_id=HIP_PAPER))

    assert not confirmation_eligible(ev, **_kwargs())


def test_referencia_own_value_match_no_es_elegible():
    ev = _evaluation(_journal_reference(25.04))

    reference = ev.result.reference()
    assert (
        classify_solution(
            reference,
            external_id=HIP_PAPER,
            published_at=PUBLISHED,
            measurements=ev.measurements,
            rule=RULE,
        )
        == SolutionProvenance.OWN_VALUE_MATCH
    )
    assert not confirmation_eligible(ev, **_kwargs())


def test_referencia_ambigua_no_es_elegible():
    ev = _evaluation(_journal_reference(40.0))

    assert (
        classify_solution(
            ev.result.reference(),
            external_id=HIP_PAPER,
            published_at=PUBLISHED,
            measurements=ev.measurements,
            rule=RULE,
        )
        == SolutionProvenance.AMBIGUOUS
    )
    assert not confirmation_eligible(ev, **_kwargs())


def test_mismos_valores_con_arxiv_id_ajeno_si_son_elegibles_control_de_la_ambigua():
    ev = _evaluation(_journal_reference(40.0, arxiv_id="2606.18045"))

    assert confirmation_eligible(ev, **_kwargs())


def test_referencia_sin_arxiv_id_pero_con_pl_pubdate_antiguo_es_independiente_y_elegible():
    ev = _evaluation(_journal_reference(25.04, pl_pubdate="2024-09"))

    assert confirmation_eligible(ev, **_kwargs())


def test_evaluacion_guardada_antes_de_t83_cuya_referencia_es_la_propia_por_valor_no_es_elegible():
    """Caso real del hueco: `awaiting_reference` que se reevaluó contra la versión
    de revista del propio paper (σ ≈ 0) y quedó `evaluated` sin `own_solution_key`."""
    paper = make_measurement(13.8, 1.0, 1.0, planet_name="HIP 67522 b", unit=M_E)
    own = replace(
        chakraborty_b(),
        arxiv_id=None,
        pl_pubdate="2026-10",
        releasedate=date(2026, 10, 3),
    )
    result = TensionResult(
        item_id=uuid4(),
        planet_name="HIP 67522 b",
        parameter=paper.parameter,
        comparisons=(compare(paper, own),),
    )
    ev = TensionEvaluation(
        reading_id=uuid4(),
        item_id=result.item_id,
        planet_name="HIP 67522 b",
        parameter=paper.parameter,
        measurements=(paper,),
        status=EvaluationStatus.EVALUATED,
        evaluated_at=NOW,
        archive_planet_name="HIP 67522 b",
        result=result,
        own_solution_key=None,
    )

    assert ev.result.comparisons[0].sigma == 0.0
    assert not confirmation_eligible(ev, **_kwargs("2606.18045"))


@pytest.mark.parametrize("value", [25.04, 40.0], ids=["own_value_match", "ambigua"])
def test_la_factoria_rechaza_una_referencia_no_independiente(value):
    ev = _evaluation(_journal_reference(value))

    with pytest.raises(InvariantViolation):
        independent_confirmation_from(ev, archive_url=URL, **_kwargs())


def test_la_elegibilidad_depende_del_external_id_que_se_pasa():
    """La misma evaluación es de otro paper si el `external_id` es el de la referencia."""
    ev = hip67522_b(uuid4())

    assert confirmation_eligible(ev, **_kwargs(HIP_PAPER))
    assert not confirmation_eligible(ev, **_kwargs("2606.18045"))


def test_los_parametros_nuevos_son_obligatorios():
    ev = hip67522_b(uuid4())

    with pytest.raises(TypeError):
        confirmation_eligible(  # type: ignore[call-arg]
            ev, item_published_at=PUBLISHED, now=NOW, max_sigma=2.0, window_days=30
        )
