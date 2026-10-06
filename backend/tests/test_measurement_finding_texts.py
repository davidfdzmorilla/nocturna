"""T89: plantillas deterministas de los findings de medidas. Sin red ni Claude."""

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from helpers.exoplanet import make_own_solution_rule
from helpers.measurement_findings import (
    hip67522_b,
    present_without_reference,
    toi_6981_b,
)

from nocturna.application.measurement_finding_texts import (
    _UNIT_LABEL,
    format_number,
    format_sigma,
    render_confirmacion_independiente,
    render_primera_medida,
)
from nocturna.domain.entities import (
    MEASUREMENT_FINDING_PARAMETERS,
    UNITS_BY_PARAMETER,
    ArchiveStatus,
    ConfirmationReference,
    FirstMeasurement,
    IndependentConfirmation,
    MeasuredParameter,
    MeasurementUnit,
    PaperMeasurement,
)
from nocturna.domain.measurement_findings import (
    first_measurement_from,
    independent_confirmation_from,
)

NOW = datetime(2026, 10, 5, 3, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)
URL = "https://exoplanetarchive.ipac.caltech.edu/overview/HIP%2067522%20b"


def _confirmation():
    return independent_confirmation_from(
        hip67522_b(uuid4()),
        item_published_at=PUBLISHED,
        now=NOW,
        max_sigma=2.0,
        window_days=30,
        archive_url=URL,
        item_external_id="2609.35979",
        own_rule=make_own_solution_rule(),
    )


@pytest.mark.parametrize(
    ("value", "text"),
    [(2.4, "2,4"), (0.1, "0,1"), (25.0, "25,0"), (1e-05, "0,00001"), (317.83, "317,83")],
)
def test_numeros_con_coma_decimal_y_sin_notacion_cientifica(value, text):
    assert format_number(value) == text


def test_sigma_con_dos_decimales():
    assert format_sigma(1.42424) == "1,42"
    assert format_sigma(0.7476) == "0,75"


def test_primera_medida_planeta_ausente_toi_6981_b():
    fm = first_measurement_from(toi_6981_b(uuid4()), archive_url=None)

    texts = render_primera_medida(fm, arxiv_id="2609.37597")

    assert texts.title == "Primera medida del radio de TOI-6981 b: 2,4 ± 0,1 R⊕"
    assert texts.level_curious == (
        "Un artículo nuevo en arXiv mide por primera vez el radio del planeta TOI-6981 b: "
        "unas 2,4 veces el de la Tierra. "
        "El catálogo de exoplanetas de la NASA todavía no incluye este planeta."
    )
    assert texts.level_amateur == (
        "El artículo arXiv:2609.37597 da para TOI-6981 b el radio 2,4 ± 0,1 R⊕. "
        "El planeta no figura en el NASA Exoplanet Archive. "
        "Es el resultado de un solo trabajo: aún no hay otra medida independiente que lo "
        "contraste."
    )
    assert texts.level_technical == (
        "Medidas extraídas del abstract de arXiv:2609.37597 (TOI-6981 b, radio): "
        "2,4 ± 0,1 R⊕. "
        "Estado en el NASA Exoplanet Archive: planeta no encontrado (servicio de alias: "
        "System Not Found). "
        "Comparación automática con el histórico del archivo; no sustituye a la lectura del "
        "artículo."
    )
    assert "Ficha" not in texts.level_technical, "sin archive_url no hay enlace"


def test_primera_medida_planeta_presente_sin_solucion_comparable():
    ev = present_without_reference(uuid4())
    fm = first_measurement_from(ev, archive_url="https://archive.test/Foo-1")

    texts = render_primera_medida(fm, arxiv_id="2610.00001")

    assert texts.title == "Primera medida del radio de Foo-1 b: 3,1 +0,2 / −0,3 R⊕"
    assert texts.level_curious.endswith(
        "El catálogo de exoplanetas de la NASA ya incluye el planeta, pero sin ninguna medida "
        "previa del radio publicada y confirmada con la que compararla."
    )
    assert (
        "El NASA Exoplanet Archive tiene el planeta (Foo-1 b), pero ninguna solución publicada y "
        "confirmada con error en ambos sentidos para este parámetro."
    ) in texts.level_amateur
    assert (
        "planeta Foo-1 b presente; sin solución Published Confirmed con error bilateral, sin "
        "cota superior y sin solución del propio trabajo. Ficha: https://archive.test/Foo-1. "
        "Comparación automática"
    ) in texts.level_technical


def test_primera_medida_masa_en_unidades_de_jupiter_convierte_en_el_nivel_curioso():
    fm = FirstMeasurement(
        paper_planet_name="X b",
        archive_planet_name=None,
        parameter=MeasuredParameter.MASS,
        archive_status=ArchiveStatus.ABSENT,
        measurements=(PaperMeasurement(0.5, 0.1, 0.1, MeasurementUnit.M_JUP),),
    )

    texts = render_primera_medida(fm, arxiv_id="2610.00002")

    assert texts.title == "Primera medida de la masa de X b: 0,5 ± 0,1 M♃"
    assert "unas 159 veces la de la Tierra" in texts.level_curious


def test_varias_medidas_se_listan_en_amateur_y_technical_y_el_titulo_usa_la_primera():
    fm = FirstMeasurement(
        paper_planet_name="X b",
        archive_planet_name=None,
        parameter=MeasuredParameter.RADIUS,
        archive_status=ArchiveStatus.ABSENT,
        measurements=(
            PaperMeasurement(2.4, 0.1, 0.1, MeasurementUnit.R_EARTH),
            PaperMeasurement(2.6, 0.3, 0.2, MeasurementUnit.R_EARTH),
        ),
    )

    texts = render_primera_medida(fm, arxiv_id="2610.00003")

    assert texts.title.endswith("2,4 ± 0,1 R⊕")
    assert "2,4 ± 0,1 R⊕; 2,6 +0,3 / −0,2 R⊕" in texts.level_amateur
    assert "2,4 ± 0,1 R⊕; 2,6 +0,3 / −0,2 R⊕" in texts.level_technical


def test_confirmacion_independiente_hip_67522_b():
    texts = render_confirmacion_independiente(_confirmation(), arxiv_id="2609.35979")

    assert texts.title == "Confirmación independiente de la masa de HIP 67522 b"
    assert texts.level_curious == (
        "Dos trabajos distintos han medido la masa del planeta HIP 67522 b y llegan a valores "
        "compatibles dentro de sus márgenes de error: unas 25,0 veces la de la Tierra frente a "
        "unas 13,8. "
        "Que dos medidas independientes coincidan da más confianza en el resultado."
    )
    assert texts.level_amateur == (
        "El artículo arXiv:2609.35979 mide la masa de HIP 67522 b: "
        "25,0 +7,6 / −7,8 M⊕; 23,1 +15,4 / −12,4 M⊕. "
        "La referencia del NASA Exoplanet Archive, Chakraborty et al. 2026, da 13,8 ± 1,0 M⊕. "
        "La diferencia mayor es de 1,42 σ, dentro del margen de 2,0 σ: las medidas son "
        "compatibles."
    )
    assert texts.level_technical == (
        "Comparación de HIP 67522 b (masa) entre arXiv:2609.35979 y la solución del NASA "
        "Exoplanet Archive Chakraborty et al. 2026 (entrada en el archivo: 2026-10-01, "
        "arXiv:2606.18045). σ por medida: 1,42; 0,75, con σ = |x_p − x_r| / √(e_p² + e_r²) "
        "usando el error del lado que mira al otro valor. "
        "Criterio: todas las medidas a ≤ 2,0 σ y la más reciente de las dos entradas en los "
        "últimos 30 días. "
        "Independencia comprobada solo como artículos distintos; no se comprueba si los métodos "
        f"o los equipos son distintos. Ficha: {URL}."
    )


def test_los_textos_no_afirman_metodo_ni_equipo_distintos():
    texts = render_confirmacion_independiente(_confirmation(), arxiv_id="2609.35979")
    joined = " ".join([texts.title, texts.level_curious, texts.level_amateur])
    assert "método" not in joined and "equipo" not in joined
    assert "no se comprueba si los métodos o los equipos son distintos" in texts.level_technical


def test_es_determinista():
    a = render_confirmacion_independiente(_confirmation(), arxiv_id="2609.35979")
    b = render_confirmacion_independiente(_confirmation(), arxiv_id="2609.35979")
    assert a == b


def test_primera_medida_radio_en_unidades_de_jupiter():
    fm = FirstMeasurement(
        paper_planet_name="X b",
        archive_planet_name=None,
        parameter=MeasuredParameter.RADIUS,
        archive_status=ArchiveStatus.ABSENT,
        measurements=(PaperMeasurement(0.969, 0.017, 0.017, MeasurementUnit.R_JUP),),
    )

    texts = render_primera_medida(fm, arxiv_id="2610.00004")

    assert texts.title == "Primera medida del radio de X b: 0,969 ± 0,017 R♃"
    assert "R♃" in texts.level_amateur
    assert "R♃" in texts.level_technical


def _jupiter_confirmation(parameter: MeasuredParameter, unit: MeasurementUnit):
    return IndependentConfirmation(
        paper_planet_name="X b",
        archive_planet_name="X b",
        parameter=parameter,
        archive_url="https://archive.test/X",
        measurements=(PaperMeasurement(0.5, 0.1, 0.1, unit),),
        reference=ConfirmationReference(
            refname="Ref et al. 2026",
            arxiv_id=None,
            value=0.55,
            err_plus=0.05,
            err_minus=0.05,
            unit=unit,
            releasedate=date(2026, 10, 1),
        ),
        sigmas=(0.4,),
        max_sigma=2.0,
        window_days=30,
        paper_published_at=PUBLISHED,
    )


def test_confirmacion_con_medida_y_referencia_en_unidades_de_jupiter():
    confirmation = _jupiter_confirmation(MeasuredParameter.MASS, MeasurementUnit.M_JUP)

    texts = render_confirmacion_independiente(confirmation, arxiv_id="2610.00005")

    assert "0,5 ± 0,1 M♃" in texts.level_amateur
    assert "da 0,55 ± 0,05 M♃." in texts.level_amateur


def test_cada_unidad_tiene_etiqueta_legible():
    for unit in MeasurementUnit:
        label = _UNIT_LABEL[unit]
        assert "_" not in label
        assert label.lower() != unit.value.lower()


_FINDING_UNITS = [
    (parameter, unit)
    for parameter in MEASUREMENT_FINDING_PARAMETERS
    for unit in sorted(UNITS_BY_PARAMETER[parameter], key=lambda u: u.value)
]


@pytest.mark.parametrize(("parameter", "unit"), _FINDING_UNITS)
def test_ningun_texto_filtra_el_identificador_interno_de_la_unidad(parameter, unit):
    fm = FirstMeasurement(
        paper_planet_name="X b",
        archive_planet_name=None,
        parameter=parameter,
        archive_status=ArchiveStatus.ABSENT,
        measurements=(PaperMeasurement(0.5, 0.1, 0.1, unit),),
    )
    rendered = [
        render_primera_medida(fm, arxiv_id="2610.00006"),
        render_confirmacion_independiente(
            _jupiter_confirmation(parameter, unit), arxiv_id="2610.00006"
        ),
    ]
    for texts in rendered:
        for text in (texts.title, texts.level_curious, texts.level_amateur, texts.level_technical):
            lowered = text.lower()
            assert unit.value not in text
            assert "_jup" not in lowered and "_earth" not in lowered
            assert "M_" not in text and "R_" not in text and "_M" not in text and "_R" not in text
