"""T100: reglas nuevas de los hallazgos de medida (solo planetas catalogados, sin
`m sin i`, confirmación entre equipos distintos). Dominio puro."""

from dataclasses import replace
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from helpers.exoplanet import make_own_solution_rule
from helpers.measurement_findings import (
    absent_toi_6981_b,
    chakraborty_b,
    hip67522_b,
    present_without_reference,
)
from helpers.tension_writer import v1298_evaluation

from nocturna.domain.entities import ArchiveStatus, MeasuredParameter
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.measurement_findings import (
    catalog_tension_skip_reason,
    confirmation_eligible,
    first_measurement_eligible,
    first_measurement_from,
    independent_confirmation_from,
    minimum_mass_in,
)

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
PAPER_ID = "2609.35979"
RULE = make_own_solution_rule()
URL = "https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20b"


def _confirmation_kwargs(authors: str | None) -> dict:
    return {
        "item_published_at": PUBLISHED,
        "now": NOW,
        "max_sigma": 2.0,
        "window_days": 30,
        "item_external_id": PAPER_ID,
        "own_rule": RULE,
        "item_authors": authors,
    }


def _knudstrup_case():
    """Caso M15 reconstruido: la referencia es «Knudstrup et al. 2022»."""
    return hip67522_b(uuid4(), reference=chakraborty_b(reference="Knudstrup et al. 2022"))


# --- primera_medida: solo `no_comparable_solution` ------------------------------


def test_planeta_ausente_ya_no_es_primera_medida_y_la_factoria_lanza() -> None:
    ev = absent_toi_6981_b(uuid4())
    assert ev.archive_planet_name is None
    assert not first_measurement_eligible(ev)
    with pytest.raises(InvariantViolation):
        first_measurement_from(ev, archive_url=None)


def test_planeta_presente_sin_solucion_comparable_sigue_siendo_primera_medida() -> None:
    ev = present_without_reference(uuid4())
    assert first_measurement_eligible(ev)
    first = first_measurement_from(ev, archive_url="https://a/x")
    assert first.archive_status == ArchiveStatus.NO_COMPARABLE_SOLUTION


# --- confirmación: primer autor distinto -----------------------------------------


def test_confirmacion_falsa_si_el_primer_autor_de_la_referencia_firma_el_paper() -> None:
    ev = _knudstrup_case()
    authors = "A. Smith, E. Knudstrup, B. Doe"
    assert not confirmation_eligible(ev, **_confirmation_kwargs(authors))
    with pytest.raises(InvariantViolation):
        independent_confirmation_from(ev, archive_url=URL, **_confirmation_kwargs(authors))


def test_confirmacion_falsa_sin_autores() -> None:
    assert not confirmation_eligible(_knudstrup_case(), **_confirmation_kwargs(None))


def test_confirmacion_falsa_con_referencia_no_reconocible() -> None:
    ev = hip67522_b(uuid4(), reference=chakraborty_b(reference="ExoFOP-TESS TOI"))
    assert not confirmation_eligible(ev, **_confirmation_kwargs("A. Smith"))


def test_confirmacion_cierta_con_otro_equipo() -> None:
    ev = _knudstrup_case()
    kwargs = _confirmation_kwargs("A. Smith, B. Doe")
    assert confirmation_eligible(ev, **kwargs)
    assert independent_confirmation_from(ev, archive_url=URL, **kwargs).reference.refname == (
        "Knudstrup et al. 2022"
    )


# --- masa mínima ---------------------------------------------------------------


def _with_evidence(ev, evidence: str):
    measurements = tuple(replace(m, evidence=evidence) for m in ev.measurements)
    return replace(ev, measurements=measurements)


def test_minimum_mass_in_mira_las_medidas_de_masa_de_la_evaluacion() -> None:
    ev = _with_evidence(hip67522_b(uuid4()), "Msin(i) = 25.0 +- 7.6 M_earth")
    assert minimum_mass_in(ev, "Msin(i) = 25.0 +- 7.6 M_earth")
    clean = hip67522_b(uuid4())
    assert not minimum_mass_in(clean, "Un abstract sin masa mínima.")


def test_minimum_mass_in_nunca_en_radio() -> None:
    ev = _with_evidence(absent_toi_6981_b(uuid4()), "Msin(i) = 2.4 R_earth")
    assert ev.parameter == MeasuredParameter.RADIUS
    assert not minimum_mass_in(ev, "Msin(i) = 2.4 R_earth")


def test_skip_reason_minimum_mass_va_justo_despues_de_not_evaluated() -> None:
    ev = _with_evidence(v1298_evaluation(uuid4()), "Msin(i) = 0.5 M_jup")
    common = {
        "item_external_id": "2609.30038",
        "item_published_at": datetime(2026, 9, 30, tzinfo=UTC),
        "own_rule": RULE,
        "item_abstract": "Msin(i) = 0.5 M_jup",
    }
    # Con umbral inalcanzable sería `below_threshold`, pero `minimum_mass` va antes.
    assert catalog_tension_skip_reason(ev, threshold_sigma=1000.0, **common) == "minimum_mass"
    assert catalog_tension_skip_reason(ev, threshold_sigma=3.0, **common) == "minimum_mass"
    # Sin masa mínima en el abstract, la misma evaluación vuelve a su motivo normal.
    plain = {**common, "item_abstract": ""}
    clean = v1298_evaluation(uuid4())
    assert catalog_tension_skip_reason(clean, threshold_sigma=3.0, **plain) is None
    assert catalog_tension_skip_reason(clean, threshold_sigma=1000.0, **plain) == "below_threshold"
