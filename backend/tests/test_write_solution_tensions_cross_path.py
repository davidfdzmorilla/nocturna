"""T95: `SelectSolutionTensions` no redacta lo que la vía arXiv ya cuenta.

En memoria, sin Claude. Convención del fichero: nombres en español.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from helpers.cross_path_dedup import (
    HD65216_ARXIV_ID,
    TOI2158_ARXIV_ID,
    archive_tension,
    arxiv_evaluation,
    arxiv_finding,
)
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    make_item,
    make_policy,
    make_solution_selector,
)
from helpers.solution_tension_writer import make_archive_item, make_solution_tension

from nocturna.domain.entities import FindingType, ItemStatus, MeasuredParameter
from nocturna.domain.errors import InvariantViolation

PERIOD = MeasuredParameter.PERIOD
MASS = MeasuredParameter.MASS
RADIUS = MeasuredParameter.RADIUS


def _env(arxiv_external_id: str = HD65216_ARXIV_ID):
    arxiv_item = make_item(external_id=arxiv_external_id, status=ItemStatus.READ)
    archive_item = make_archive_item(planet="HD 65216 c")
    env = Environment(items=[arxiv_item, archive_item], policy=make_policy(), now=WITHIN_WINDOW)
    return env, arxiv_item, archive_item


def _arxiv_side(env, arxiv_item, finding_type, *, planet, parameter, published=False):
    evaluation = arxiv_evaluation(arxiv_item.id, planet=planet, parameter=parameter)
    env.evaluations.add(evaluation)
    env.findings.add(
        arxiv_finding(
            finding_type,
            item_id=arxiv_item.id,
            run_id=env.run.id,
            evaluation_id=evaluation.id,
            published=published,
        )
    )
    return evaluation


def test_hd65216c_periodo_ya_publicado_por_arxiv_se_excluye_como_same_paper_arxiv() -> None:
    env, arxiv_item, archive_item = _env()
    _arxiv_side(
        env,
        arxiv_item,
        FindingType.CATALOG_TENSION,
        planet="HD 65216 c",
        parameter=PERIOD,
        published=True,
    )
    tension = archive_tension(archive_item.id)
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert pending.candidates == ()
    assert pending.skipped == ((tension.id, "same_paper_arxiv"),)


def test_mismo_paper_con_primera_medida_de_masa_y_tension_en_periodo_es_candidata() -> None:
    env, arxiv_item, archive_item = _env()
    _arxiv_side(
        env,
        arxiv_item,
        FindingType.PRIMERA_MEDIDA,
        planet="HD 65216 c",
        parameter=MASS,
        published=True,
    )
    tension = archive_tension(archive_item.id, parameter=PERIOD)
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert [c.tension.id for c in pending.candidates] == [tension.id]
    assert pending.skipped == ()


def test_mismo_paper_y_parametro_en_otro_planeta_es_candidata() -> None:
    env, arxiv_item, archive_item = _env()
    _arxiv_side(
        env,
        arxiv_item,
        FindingType.CATALOG_TENSION,
        planet="HD 65216 b",
        parameter=PERIOD,
        published=True,
    )
    tension = archive_tension(archive_item.id, planet="HD 65216 c")
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert [c.tension.id for c in pending.candidates] == [tension.id]
    assert pending.skipped == ()


def test_alta_sin_arxiv_id_es_candidata_aunque_haya_hallazgo_arxiv_del_planeta() -> None:
    env, arxiv_item, archive_item = _env()
    _arxiv_side(
        env,
        arxiv_item,
        FindingType.CATALOG_TENSION,
        planet="HD 65216 c",
        parameter=PERIOD,
        published=True,
    )
    tension = archive_tension(archive_item.id, arxiv_id=None)
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert [c.tension.id for c in pending.candidates] == [tension.id]


def test_otro_paper_con_mismo_planeta_y_parametro_es_candidata() -> None:
    env, arxiv_item, archive_item = _env()
    _arxiv_side(
        env,
        arxiv_item,
        FindingType.CATALOG_TENSION,
        planet="HD 65216 c",
        parameter=PERIOD,
        published=True,
    )
    tension = archive_tension(archive_item.id, arxiv_id="2501.99999")
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert [c.tension.id for c in pending.candidates] == [tension.id]


def test_toi2158b_radio_catalog_tension_sin_publicar_del_mismo_paper_tambien_excluye() -> None:
    env, arxiv_item, archive_item = _env(TOI2158_ARXIV_ID)
    _arxiv_side(
        env,
        arxiv_item,
        FindingType.CATALOG_TENSION,
        planet="TOI-2158 b",
        parameter=RADIUS,
        published=False,
    )
    tension = archive_tension(
        archive_item.id,
        planet="TOI-2158 b",
        parameter=RADIUS,
        arxiv_id=TOI2158_ARXIV_ID,
        sigma=3.18,
    )
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert pending.candidates == ()
    assert pending.skipped == ((tension.id, "same_paper_arxiv"),)


@pytest.mark.parametrize(
    "finding_type, parameter",
    [
        (FindingType.PRIMERA_MEDIDA, MASS),
        (FindingType.PRIMERA_MEDIDA, RADIUS),
        (FindingType.CONFIRMACION_INDEPENDIENTE, MASS),
        (FindingType.CONFIRMACION_INDEPENDIENTE, RADIUS),
    ],
)
@pytest.mark.parametrize("published", [False, True])
def test_primera_medida_y_confirmacion_del_mismo_paper_planeta_y_parametro_excluyen(
    finding_type: FindingType, parameter: MeasuredParameter, published: bool
) -> None:
    env, arxiv_item, archive_item = _env()
    _arxiv_side(
        env,
        arxiv_item,
        finding_type,
        planet="HD 65216 c",
        parameter=parameter,
        published=published,
    )
    tension = archive_tension(archive_item.id, parameter=parameter)
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert pending.candidates == ()
    assert pending.skipped == ((tension.id, "same_paper_arxiv"),)


def test_hallazgo_de_un_item_que_no_es_de_arxiv_no_cuenta_aunque_comparta_external_id() -> None:
    """Solo los `Item` `source = 'arxiv'` dan clave: un `Item` del archivo con el
    mismo `external_id` que el paper no excluye."""
    env, _arxiv_item, archive_item = _env()
    other = make_archive_item(external_id=HD65216_ARXIV_ID, planet="HD 65216 c")
    env.items.add_many([other])
    _arxiv_side(
        env,
        other,
        FindingType.CATALOG_TENSION,
        planet="HD 65216 c",
        parameter=PERIOD,
        published=True,
    )
    tension = archive_tension(archive_item.id)
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert [c.tension.id for c in pending.candidates] == [tension.id]
    assert pending.skipped == ()


def test_evaluacion_sin_hallazgo_no_excluye() -> None:
    env, arxiv_item, archive_item = _env()
    env.evaluations.add(arxiv_evaluation(arxiv_item.id, planet="HD 65216 c", parameter=PERIOD))
    tension = archive_tension(archive_item.id)
    env.solution_tensions.add(tension)

    pending = make_solution_selector(env).pending()

    assert [c.tension.id for c in pending.candidates] == [tension.id]


def test_already_written_gana_a_same_paper_arxiv() -> None:
    from nocturna.domain.entities import Finding
    from nocturna.domain.solution_tension import solution_tension_data_from

    env, arxiv_item, archive_item = _env()
    _arxiv_side(
        env,
        arxiv_item,
        FindingType.CATALOG_TENSION,
        planet="HD 65216 c",
        parameter=PERIOD,
        published=True,
    )
    tension = archive_tension(archive_item.id)
    env.solution_tensions.add(tension)
    data = solution_tension_data_from(
        tension, threshold_sigma=3.0, archive_url="https://archive.example/x"
    )
    env.findings.add(
        Finding(
            item_id=archive_item.id,
            run_id=env.run.id,
            type=FindingType.SOLUTION_TENSION,
            title="t",
            level_curious="c",
            level_amateur="a",
            level_technical="t",
            solution_tension=data,
            solution_tension_id=tension.id,
        )
    )

    pending = make_solution_selector(env).pending()

    assert pending.candidates == ()
    assert pending.skipped == ((tension.id, "already_written"),)


def test_not_eligible_conserva_su_motivo_aunque_haya_hallazgo_arxiv() -> None:
    from nocturna.domain.solution_tension import SkipReason

    env, arxiv_item, _ = _env()
    _arxiv_side(
        env,
        arxiv_item,
        FindingType.CATALOG_TENSION,
        planet="HD 65216 c",
        parameter=PERIOD,
        published=True,
    )
    skipped = make_solution_tension(
        None, planet="HD 65216 c", skip_reason=SkipReason.BELOW_THRESHOLD, sigma=1.0
    )
    env.solution_tensions.add(skipped)

    pending = make_solution_selector(env).pending()

    assert pending.skipped == ((skipped.id, "not_eligible"),)


def test_evaluacion_con_hallazgo_y_item_inexistente_propaga_invariant_violation() -> None:
    """T95, ADR 0034: un hallazgo arXiv cuyo Item no existe es un estado corrupto;
    `pending()` propaga la excepción y no deja candidatos sin filtrar."""
    env, arxiv_item, archive_item = _env()
    evaluation = arxiv_evaluation(uuid4(), planet="HD 65216 c", parameter=PERIOD)
    env.evaluations.add(evaluation)
    env.findings.add(
        arxiv_finding(
            FindingType.CATALOG_TENSION,
            item_id=arxiv_item.id,
            run_id=env.run.id,
            evaluation_id=evaluation.id,
        )
    )
    env.solution_tensions.add(archive_tension(archive_item.id))

    with pytest.raises(InvariantViolation):
        make_solution_selector(env).pending()
