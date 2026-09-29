"""Tests de `application/agents/reader_measurements.py` (T71.c).

Puro: sin IO, sin red, sin base de datos. Ningún test llama a Claude. Cubre
`evidence_in_abstract`, `planet_host` y, sobre todo, `filter_measurements`:
el orden de las cinco comprobaciones (forma, invariantes de dominio, cita
literal, anfitriona reconocible, número de `value` citado) y que cada fallo
descarta solo esa medida, sin lanzar nunca por un elemento individual.

La salvaguarda de anfitriona se valida SOLO contra el `abstract` o el
`title` del ítem, nunca contra `objects` (decisión del autor, revisión 2 de
T71.c: `objects` es salida del mismo modelo que completa `planet_name`, así
que no sirve de verificación independiente). `filter_measurements` recibe
`title` como parámetro obligatorio -- estos tests documentan la firma
`filter_measurements(raw, *, abstract, title)`.
"""

from __future__ import annotations

import nocturna.application.agents.reader_output as reader_output_module
from nocturna.application.agents.reader_measurements import (
    DiscardReason,
    evidence_in_abstract,
    filter_measurements,
    planet_host,
)

# --- evidence_in_abstract ----------------------------------------------------


def test_evidence_in_abstract_con_espacios_normalizados_se_conserva():
    abstract = "El planeta tiene una masa de\n2.8 M_jup   medida con precisión."
    evidence = "masa de 2.8 M_jup medida"

    assert evidence_in_abstract(evidence, abstract) is True


def test_evidence_in_abstract_parafraseada_no_se_conserva():
    abstract = "El planeta tiene una masa de 2.8 M_jup medida con precisión."
    evidence = "la masa medida del planeta es de 2.8 masas de Júpiter"

    assert evidence_in_abstract(evidence, abstract) is False


def test_evidence_in_abstract_con_latex_literal_incluido_pm_se_conserva():
    abstract = r"The planet has a mass of $2.8 \pm 0.5$ M$_{\rm Jup}$, measured precisely."
    evidence = r"$2.8 \pm 0.5$ M$_{\rm Jup}$"

    assert evidence_in_abstract(evidence, abstract) is True


# --- planet_host --------------------------------------------------------------


def test_planet_host_con_espacio_extrae_la_anfitriona():
    assert planet_host("V1298 Tau b") == "V1298 Tau"


def test_planet_host_sin_espacio_tras_cifra_extrae_la_anfitriona():
    assert planet_host("WASP-12b") == "WASP-12"


def test_planet_host_solo_la_letra_no_reconoce_anfitriona():
    assert planet_host("b") is None


def test_planet_host_anfitriona_sin_letra_no_reconoce_nada():
    """`"V1298 Tau"` sin letra de planeta al final no encaja en ninguna de
    las dos formas reconocidas."""
    assert planet_host("V1298 Tau") is None


# --- filter_measurements: helpers --------------------------------------------


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


_ABSTRACT = "The planet V1298 Tau b has a mass of 0.52 (+0.12/-0.14) M_jup, measured this work."


# --- filter_measurements: evidencia literal -----------------------------------


def test_evidencia_con_saltos_de_linea_y_espacios_distintos_se_conserva():
    abstract = "The planet V1298 Tau b\nhas a mass of  0.52 (+0.12/-0.14)   M_jup, measured."
    measurement = _measurement_dict(evidence="mass of 0.52 (+0.12/-0.14) M_jup")

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_evidencia_parafraseada_se_descarta_por_evidence_not_literal():
    measurement = _measurement_dict(evidence="una masa aproximada de media masa de Júpiter")

    result = filter_measurements([measurement], abstract=_ABSTRACT, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.EVIDENCE_NOT_LITERAL


def test_evidencia_con_latex_literal_se_conserva():
    abstract = r"TOI-2109 b is a super-Jupiter ($1.347 \pm 0.047 R_{\rm Jup}$), measured this work."
    measurement = _measurement_dict(
        planet_name="TOI-2109 b",
        parameter="radius",
        value=1.347,
        err_plus=0.047,
        err_minus=0.047,
        unit="R_jup",
        evidence=r"$1.347 \pm 0.047 R_{\rm Jup}$",
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.kept[0].evidence == r"$1.347 \pm 0.047 R_{\rm Jup}$"


# --- filter_measurements: salvaguarda de la anfitriona ------------------------


def test_planet_name_solo_la_letra_se_descarta_por_host_not_found():
    measurement = _measurement_dict(planet_name="b")

    result = filter_measurements([measurement], abstract=_ABSTRACT, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_planet_name_anfitriona_sin_letra_se_descarta_por_host_not_found():
    measurement = _measurement_dict(planet_name="V1298 Tau")

    result = filter_measurements([measurement], abstract=_ABSTRACT, title="")

    assert result.kept == ()
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_anfitriona_ausente_del_abstract_pero_presente_en_el_titulo_se_conserva():
    """El abstract no menciona la anfitriona en ninguna forma reconocible;
    el título del ítem sí trae `"V1298 Tau"`. Decisión del autor (revisión 2
    de T71.c): la salvaguarda se valida contra el abstract O el título,
    nunca contra `objects` -- este caso ejercita la vía del título."""
    abstract = (
        "Four transiting planets orbit an infant star. Mass of planet b: 0.52 (+0.12/-0.14) M_jup."
    )
    title = "The infant star V1298 Tau hosts four transiting planets"
    measurement = _measurement_dict(evidence="0.52 (+0.12/-0.14) M_jup")

    result = filter_measurements([measurement], abstract=abstract, title=title)

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_anfitriona_con_coma_espuria_en_el_abstract_se_conserva():
    """Desde la revisión de T71.c (decisión registrada en OPEN_DECISIONS:
    "Anfitriona con marcado LaTeX o comas espurias"), la comparación de la
    anfitriona trata la coma pegada a alfanuméricos como espacio, así que
    `V1298,Tau` del abstract casa con la anfitriona `V1298 Tau` sin
    necesitar el título."""
    abstract = "The infant star V1298,Tau hosts four transiting planets."
    measurement = _measurement_dict(evidence="0.52 (+0.12/-0.14) M_jup")
    abstract_with_evidence = abstract + " Mass of planet b: 0.52 (+0.12/-0.14) M_jup."

    result = filter_measurements([measurement], abstract=abstract_with_evidence, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_anfitriona_de_otro_planeta_real_no_se_conserva_aunque_comparta_prefijo():
    """`planet_name="WASP-12 b"` con un abstract que solo cita `"WASP-121 b"`
    (otro planeta real, no un genérico): decisión del autor (revisión 2 de
    T71.c) -- desde que la salvaguarda deja de validar contra `objects`, ya
    no importa que `objects` traiga `"WASP-12 b"` (el propio modelo pudo
    haberlo copiado del nombre completado, no del abstract): sin `objects`
    de por medio, y con límite de palabra, `"WASP-12"` no coincide con
    `"WASP-121"`, así que se descarta por `HOST_NOT_FOUND`."""
    abstract = "WASP-121 b is an inflated hot Jupiter with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="WASP-12 b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


# --- filter_measurements: forma inválida (MALFORMED) --------------------------


def test_elemento_string_en_vez_de_dict_se_descarta_por_malformed():
    result = filter_measurements(["esto no es una medida"], abstract=_ABSTRACT, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.MALFORMED
    assert result.discarded[0].planet_name is None
    assert result.discarded[0].evidence is None


def test_elemento_con_campo_ausente_se_descarta_por_malformed():
    incomplete = _measurement_dict()
    del incomplete["evidence"]

    result = filter_measurements([incomplete], abstract=_ABSTRACT, title="")

    assert result.kept == ()
    assert result.discarded[0].reason is DiscardReason.MALFORMED


# --- filter_measurements: no lanza por una medida individual -----------------


def test_una_medida_mala_no_impide_conservar_las_demas_del_mismo_lote():
    good = _measurement_dict()
    bad = "esto no es una medida"

    result = filter_measurements([bad, good], abstract=_ABSTRACT, title="")

    assert len(result.kept) == 1
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.MALFORMED


# --- filter_measurements: divergencia esquema/entidad simulada (monkeypatch) -


def test_divergencia_simulada_entre_esquema_y_entidad_descarta_por_invariant_sin_lanzar(
    monkeypatch,
):
    """Si `MeasurementOut` y `Measurement` divergieran (aquí, simulado: se
    parchea el mapa de `reader_output.py` para que acepte, de forma
    incorrecta, `unit="day"` con `parameter="mass"`), `filter_measurements`
    debe descartar la medida por `INVARIANT` -- capturando la
    `InvariantViolation` que lanza `Measurement(...)` -- y nunca dejar
    escapar la excepción ni invalidar el resto del lote."""
    patched = dict(reader_output_module._UNITS_BY_PARAMETER)
    patched["mass"] = patched["mass"] | {"day"}
    monkeypatch.setattr(reader_output_module, "_UNITS_BY_PARAMETER", patched)

    measurement = _measurement_dict(unit="day")

    # Verificación de la propia simulación: MeasurementOut ahora acepta esta
    # combinación incoherente (si esto fallara, el test no distinguiría nada).
    parsed = reader_output_module.parse_measurement(measurement)
    assert parsed.unit == "day"

    result = filter_measurements([measurement], abstract=_ABSTRACT, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.INVARIANT


# --- filter_measurements: invariante value > 0 (T71.c, decisión del autor, --
# --- revisión 2) -------------------------------------------------------------


def test_medida_con_value_negativo_se_descarta_sin_lanzar():
    """Decisión del autor (revisión 2 de T71.c): `value` debe ser
    estrictamente positivo (masa, radio y periodo no admiten cero ni
    negativos). Una medida cruda con `value` negativo debe descartarse --
    por `INVARIANT` (si `MeasurementOut` la deja pasar y `Measurement(...)`
    la rechaza) o por `MALFORMED` (si el esquema la rechaza antes) -- pero
    nunca debe lanzar ni invalidar el resto del lote."""
    measurement = _measurement_dict(value=-0.52, evidence="-0.52 (+0.12/-0.14) M_jup")
    abstract = "The planet V1298 Tau b has a mass of -0.52 (+0.12/-0.14) M_jup, measured this work."

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason in (DiscardReason.INVARIANT, DiscardReason.MALFORMED)
