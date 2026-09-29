"""T71.c: contrato de la salvaguarda de anfitriona
(`application/agents/reader_measurements.py`, `planet_host` y el chequeo de
anfitriona de `filter_measurements`).

Puro: sin IO, sin red, sin base de datos. Ningún test llama a Claude.

Este fichero documenta, con tests, el contrato vigente de la salvaguarda:

1. La anfitriona se compara con LÍMITE DE PALABRA contra el `abstract`
   normalizado o contra el `title` normalizado del ítem
   (`(?<![\\w-])host(?![\\w-])`, tratando `,`/`~`/`\\,` adyacentes a
   alfanumérico como espacio), nunca contra una subcadena cualquiera ni
   contra `objects` (decisión del autor, revisión 2 de T71.c: `objects` es
   salida del mismo modelo que completa `planet_name`, no una verificación
   independiente).
2. Anfitrionas genéricas se rechazan aunque aparezcan literalmente en el
   abstract o en el título: palabras sin cifra ni mayúscula
   (`planet`/`star`/`system`/`companion`/`host`), determinantes/pronombres
   genéricos solos o combinados con esas palabras (`this`/`that`/`these`/
   `our`/`its`/`their`/`the`/`a`/`an`/`new`/`we`/`here`/`i`), y prefijos de
   misión o catálogo sin número (`TESS`/`Kepler`/`K2`/`HD`/`GJ`/`TOI`/`KOI`/
   `WASP`/`HAT-P`).
3. `planet_host` hace `strip()` antes de aplicar sus expresiones regulares.
4. `planet_host` reconoce designaciones de componentes estelares
   (`"KOI-5 Ab"`, `"GJ 667 Cc"`, `"HD 41004 Bb"`, `"Kepler-16 (AB)b"`),
   además de las formas simples `<host> <letra>` y `<host><letra>` tras
   cifra.
"""

from __future__ import annotations

from nocturna.application.agents.reader_measurements import (
    DiscardReason,
    filter_measurements,
    planet_host,
)


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


# =============================================================================
# planet_host: componentes estelares
# =============================================================================


def test_planet_host_koi5_ab_extrae_koi5():
    """`"KOI-5 Ab"`: planeta `b` del componente estelar `A` de KOI-5."""
    assert planet_host("KOI-5 Ab") == "KOI-5"


def test_planet_host_gj667_cc_extrae_gj667():
    assert planet_host("GJ 667 Cc") == "GJ 667"


def test_planet_host_hd41004_bb_extrae_hd41004():
    assert planet_host("HD 41004 Bb") == "HD 41004"


def test_planet_host_kepler16_ab_sin_espacio_extrae_kepler16_parentesis_ab():
    """`"Kepler-16 (AB)b"` (sin espacio entre `")"` y `"b"`) extrae la misma
    anfitriona que la variante con espacio (`"Kepler-16 (AB) b"`, ver el
    test de regresión homónimo más abajo), para que el chequeo de
    anfitriona reconozca la misma anfitriona sea cual sea la variante
    con/sin espacio que haya devuelto el Reader."""
    assert planet_host("Kepler-16 (AB)b") == "Kepler-16 (AB)"


# =============================================================================
# planet_host: strip() antes de las expresiones regulares
# =============================================================================


def test_planet_host_con_espacio_final_hace_strip_antes_de_extraer():
    """`"TOI-700 d "` con un espacio final extrae `"TOI-700"`, igual que
    `planet_host("TOI-700 d")` sin el espacio."""
    assert planet_host("TOI-700 d ") == "TOI-700"


# =============================================================================
# planet_host: regresión -- formas que deben seguir reconociéndose (se fijan
# aquí para que una futura reescritura de planet_host no las rompa)
# =============================================================================


def test_planet_host_kepler10c_sin_espacio_sigue_extrayendo_kepler10():
    assert planet_host("Kepler-10c") == "Kepler-10"


def test_planet_host_hd209458_con_espacio_sigue_extrayendo_hd209458():
    assert planet_host("HD 209458 b") == "HD 209458"


def test_planet_host_trappist1_sigue_extrayendo_trappist1():
    assert planet_host("TRAPPIST-1 e") == "TRAPPIST-1"


def test_planet_host_pds70_sigue_extrayendo_pds70():
    assert planet_host("PDS 70 b") == "PDS 70"


def test_planet_host_kepler16_ab_con_espacio_sigue_extrayendo_kepler16_parentesis_ab():
    assert planet_host("Kepler-16 (AB) b") == "Kepler-16 (AB)"


def test_planet_host_2mass_sigue_extrayendo_2mass_j1207_3932():
    assert planet_host("2MASS J1207-3932 b") == "2MASS J1207-3932"


def test_planet_host_ogle_sigue_extrayendo_ogle_2005_blg_390l():
    assert planet_host("OGLE-2005-BLG-390L b") == "OGLE-2005-BLG-390L"


# =============================================================================
# filter_measurements: límite de palabra contra el abstract, no subcadena
# =============================================================================


def test_kepler1_b_no_se_confunde_con_el_prefijo_de_kepler10_en_el_abstract():
    """La anfitriona completada es `"Kepler-1"` (de `"Kepler-1 b"`); el
    abstract solo menciona `"Kepler-10"`. Con límite de palabra,
    `"Kepler-1"` seguido de `"0"` (un carácter de palabra) no se reconoce:
    `HOST_NOT_FOUND`."""
    abstract = "Kepler-10 hosts a rocky world with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="Kepler-1 b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_toi21_b_no_se_confunde_con_el_prefijo_de_toi2109_en_el_abstract():
    """Mismo motivo que el test anterior, con `"TOI-21"` prefijo de
    `"TOI-2109"`."""
    abstract = "TOI-2109 is an ultra-hot Jupiter host; its planet has 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="TOI-21 b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_v1298_tau_b_con_espacio_y_abstract_con_coma_se_conserva():
    """`"V1298 Tau b"` (host con espacio) contra un abstract que escribe la
    anfitriona con coma, `"V1298,Tau"`, sin espacio. La normalización trata
    `,`/`~`/`\\,` adyacentes a alfanumérico como espacio antes de la
    comparación por límite de palabra, así que `"V1298 Tau"` (host
    normalizado) encuentra `"V1298 Tau"` (abstract normalizado)."""
    abstract = (
        "The infant star V1298,Tau hosts four transiting planets, mass 2.8 (+0.5/-0.5) M_jup."
    )
    measurement = _measurement_dict(
        planet_name="V1298 Tau b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_v1298_coma_tau_b_con_abstract_con_coma_se_conserva():
    """`"V1298,Tau b"` (host con coma, sin espacio) contra el mismo abstract
    `"V1298,Tau"`. Tras normalizar la coma como espacio en ambos lados, el
    host normalizado (`"V1298 Tau"`) coincide con el abstract normalizado."""
    abstract = (
        "The infant star V1298,Tau hosts four transiting planets, mass 2.8 (+0.5/-0.5) M_jup."
    )
    measurement = _measurement_dict(
        planet_name="V1298,Tau b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_wasp12_b_con_espacio_y_abstract_con_wasp12_se_conserva():
    """Caso base sin ambigüedad de prefijo ni de puntuación."""
    abstract = "WASP-12 is an inflated hot Jupiter host; mass 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="WASP-12 b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


# =============================================================================
# filter_measurements: anfitrionas genéricas se rechazan aunque aparezcan
# literalmente en el abstract
# =============================================================================


def test_planet_b_anfitriona_generica_se_descarta_aunque_este_en_el_abstract():
    """Host extraído: `"planet"` (todo en minúscula, sin cifra ni
    mayúscula). El abstract sí contiene literalmente la palabra "planet",
    pero se rechaza por ser genérica."""
    abstract = "The planet has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="planet b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_star_b_anfitriona_generica_se_descarta_aunque_este_en_el_abstract():
    abstract = "The star hosts a giant planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="star b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_the_planet_b_anfitriona_generica_con_articulo_se_descarta():
    """Host extraído: `"the planet"` (artículo + genérico). Los genéricos se
    reconocen con o sin artículo."""
    abstract = "As expected, the planet has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="the planet b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_system_c_anfitriona_generica_se_descarta_aunque_este_en_el_abstract():
    abstract = "The system has a third planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="system c", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


# =============================================================================
# filter_measurements: variantes genéricas nuevas -- determinantes/pronombres
# solos o combinados con una palabra genérica (T71.c, correcciones tras la
# pasada 2 de revisión)
# =============================================================================


def test_this_planet_b_anfitriona_generica_se_descarta():
    abstract = "This planet has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="This planet b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_the_new_planet_b_anfitriona_generica_se_descarta():
    abstract = "The new planet has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="The new planet b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_the_host_star_b_anfitriona_generica_se_descarta():
    abstract = "The host star has a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="The host star b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_our_planet_b_anfitriona_generica_se_descarta():
    abstract = "Our planet has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="Our planet b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_we_b_anfitriona_generica_se_descarta():
    abstract = "We report a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(planet_name="We b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup")

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_here_b_anfitriona_generica_se_descarta():
    abstract = "Here we report a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="Here b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_a_b_anfitriona_generica_se_descarta():
    abstract = "A mass of 2.8 (+0.5/-0.5) M_jup was measured this work."
    measurement = _measurement_dict(planet_name="A b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup")

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_i_b_anfitriona_generica_se_descarta():
    abstract = "I report a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(planet_name="I b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup")

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


# =============================================================================
# filter_measurements: prefijos de misión/catálogo solos, sin número, se
# rechazan como anfitriona genérica (T71.c, correcciones tras la pasada 2 de
# revisión)
# =============================================================================


def test_tess_b_prefijo_de_mision_solo_se_descarta():
    abstract = "TESS discovered a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="TESS b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_kepler_b_prefijo_de_catalogo_solo_se_descarta():
    abstract = "Kepler discovered a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="Kepler b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_k2_b_prefijo_de_catalogo_solo_se_descarta():
    abstract = "K2 discovered a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(planet_name="K2 b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup")

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_hd_b_prefijo_de_catalogo_solo_se_descarta():
    abstract = "HD hosts a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(planet_name="HD b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup")

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_gj_b_prefijo_de_catalogo_solo_se_descarta():
    abstract = "GJ hosts a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(planet_name="GJ b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup")

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_toi_b_prefijo_de_catalogo_solo_se_descarta():
    abstract = "TOI hosts a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="TOI b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_koi_b_prefijo_de_catalogo_solo_se_descarta():
    abstract = "KOI hosts a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="KOI b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_wasp_b_prefijo_de_catalogo_solo_se_descarta():
    abstract = "WASP hosts a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="WASP b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


def test_hatp_b_prefijo_de_catalogo_solo_se_descarta():
    abstract = "HAT-P hosts a planet with a mass of 2.8 (+0.5/-0.5) M_jup."
    measurement = _measurement_dict(
        planet_name="HAT-P b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert result.kept == ()
    assert len(result.discarded) == 1
    assert result.discarded[0].reason is DiscardReason.HOST_NOT_FOUND


# =============================================================================
# filter_measurements: regresión -- anfitrionas legítimas que comparten
# palabra o prefijo con una genérica/de catálogo, deben seguir conservándose
# =============================================================================


def test_barnards_star_b_anfitriona_legitima_se_conserva():
    abstract = "Barnard's Star b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="Barnard's Star b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_proxima_centauri_b_anfitriona_legitima_se_conserva():
    abstract = "Proxima Centauri b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="Proxima Centauri b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_teegardens_star_b_anfitriona_legitima_se_conserva():
    abstract = "Teegarden's Star b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="Teegarden's Star b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_51_pegasi_b_anfitriona_legitima_se_conserva():
    abstract = "51 Pegasi b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="51 Pegasi b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_fomalhaut_b_anfitriona_legitima_se_conserva():
    abstract = "Fomalhaut b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="Fomalhaut b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_beta_pictoris_b_anfitriona_legitima_se_conserva():
    abstract = "Beta Pictoris b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="Beta Pictoris b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()


def test_kepler10_b_anfitriona_legitima_se_conserva():
    abstract = "Kepler-10 b has a mass of 2.8 (+0.5/-0.5) M_jup, measured this work."
    measurement = _measurement_dict(
        planet_name="Kepler-10 b", value=2.8, evidence="2.8 (+0.5/-0.5) M_jup"
    )

    result = filter_measurements([measurement], abstract=abstract, title="")

    assert len(result.kept) == 1
    assert result.discarded == ()
