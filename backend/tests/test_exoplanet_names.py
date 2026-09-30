"""`normalize_name` del adaptador del Exoplanet Archive (T74). Sin red."""

import pytest

from nocturna.infrastructure.exoplanet_archive.names import normalize_name


@pytest.mark.parametrize("dash", ["‐", "‑", "‒", "–", "—", "−"])
def test_los_guiones_unicode_se_unifican_con_el_ascii(dash):
    assert normalize_name(f"WASP{dash}12 b") == "wasp-12 b"


def test_los_espacios_repetidos_y_de_los_extremos_se_colapsan():
    assert normalize_name("  V1298   Tau \t b  ") == "v1298 tau b"


def test_un_digito_pegado_a_la_letra_del_planeta_se_separa():
    assert normalize_name("WASP-12b") == "wasp-12 b"


@pytest.mark.parametrize(
    "raw",
    ["V1298,Tau b", "V1298~Tau b", "V1298\\,Tau b"],
)
def test_restos_de_latex_y_coma_entre_alfanumericos_pasan_a_espacio(raw):
    assert normalize_name(raw) == "v1298 tau b"


def test_una_coma_que_no_esta_entre_alfanumericos_queda_intacta():
    assert normalize_name("Foo , bar") == "foo , bar"
    assert normalize_name("Foo, bar") == "foo, bar"


def test_el_nombre_ya_normal_solo_baja_a_minusculas():
    assert normalize_name("V1298 Tau b") == "v1298 tau b"


def test_clean_name_limpia_latex_sin_pasar_a_minusculas():
    from nocturna.infrastructure.exoplanet_archive.names import clean_name

    assert clean_name("  V1298,Tau~b\\,x ") == "V1298 Tau b x"
    assert clean_name("WASP–12 b") == "WASP-12 b"
