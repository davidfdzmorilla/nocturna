"""T100 (D1): primer autor de la referencia frente a los autores del paper."""

import pytest

from nocturna.domain.team import reference_first_author, shares_first_author


@pytest.mark.parametrize(
    ("refname", "expected"),
    [
        ("Knudstrup et al. 2022", "Knudstrup"),
        ("del Burgo et al. 2026", "del Burgo"),
        ("Suárez Mascareño et al. 2022", "Suárez Mascareño"),
        ("Benedict & Harrison 2017", "Benedict"),
        ("Smith and Jones 2020", "Smith"),
        ("Smith 2020", "Smith"),
        ("ExoFOP-TESS TOI", None),
        ("Knudstrup et al.", None),
        ("2026AJ....1..3A", None),
        ("", None),
    ],
)
def test_reference_first_author(refname: str, expected: str | None) -> None:
    assert reference_first_author(refname) == expected


def test_aparece_entre_los_autores() -> None:
    assert shares_first_author("Knudstrup et al. 2022", "A. B. Smith, E. Knudstrup, C. Doe") is True


def test_no_aparece() -> None:
    assert shares_first_author("Knudstrup et al. 2022", "A. Smith, B. Doe") is False


def test_palabra_completa_wang_frente_a_wangsness() -> None:
    assert shares_first_author("Wang et al. 2020", "A. Smith, K. Wangsness") is False
    assert shares_first_author("Wang et al. 2020", "A. Smith, K. Wang") is True


def test_apellido_compuesto_y_acentos() -> None:
    authors = "A. Doe, R. del Burgo, A. Suarez Mascareno"
    assert shares_first_author("del Burgo et al. 2026", authors) is True
    assert shares_first_author("Suárez Mascareño et al. 2022", authors) is True


def test_tex_y_guiones() -> None:
    authors = r"J. Doe, A. Su\'arez Mascare\~no, J.-P. Garcia-Lopez"
    assert shares_first_author("Suárez Mascareño et al. 2022", authors) is True
    assert shares_first_author("García-López et al. 2021", authors) is True
    assert shares_first_author("Garcia Lopez et al. 2021", authors) is True


def test_tex_con_llaves() -> None:
    assert shares_first_author("Müller et al. 2021", r"A. Doe, B. M{\"u}ller") is True


def test_sin_autores_o_referencia_irreconocible_es_none() -> None:
    assert shares_first_author("Knudstrup et al. 2022", None) is None
    assert shares_first_author("Knudstrup et al. 2022", "") is None
    assert shares_first_author("ExoFOP-TESS TOI", "A. Smith") is None


@pytest.mark.parametrize(
    ("refname", "authors"),
    [
        ("Šubjak et al. 2022", r"A. Doe, J. \v{S}ubjak"),
        ("Šubjak et al. 2022", r"A. Doe, J. \v Subjak"),
        ("François et al. 2022", r"A. Doe, F. Fran\c{c}ois"),
        ("Novák et al. 2022", r"A. Doe, F. Nov\'ak"),
        ("García et al. 2020", r"A. Doe, A. Garc{\'\i}a"),
        ("García et al. 2020", r"A. Doe, A. Garc\'{\i}a"),
        ("Bjørn et al. 2021", r"A. Doe, B. Bj{\o}rn"),
        ("Bjørn et al. 2021", "A. Doe, B. Bjorn"),
        ("Bjorn et al. 2021", "A. Doe, B. Bjørn"),
        ("Łukasz et al. 2021", r"A. Doe, \L{}ukasz"),
        ("Łukasz et al. 2021", r"A. Doe, {\L}ukasz"),
        ("Straßer et al. 2021", r"A. Doe, Stra{\ss}er"),
        ("Strasser et al. 2021", "A. Doe, Straßer"),
        ("Åberg et al. 2021", r"A. Doe, {\AA}berg"),
        ("Hájek et al. 2021", r"A. Doe, H\'ajek"),
        ("Gąsior et al. 2021", r"A. Doe, G\k{a}sior"),
        ("Krůta et al. 2021", r"A. Doe, Kr\r{u}ta"),
        ("Dvořák et al. 2021", r"A. Doe, Dvo\v{r}\'ak"),
        ("Đorđević et al. 2021", "A. Doe, Dordevic"),
        ("Þór et al. 2021", "A. Doe, Thor"),
        ("Næss et al. 2021", r"A. Doe, N{\ae}ss"),
        ("Œ et al. 2021", r"A. Doe, {\OE}"),
    ],
)
def test_mismo_apellido_con_tex_o_letras_especiales(refname: str, authors: str) -> None:
    assert shares_first_author(refname, authors) is True


def test_tex_con_letras_distintas_sigue_siendo_otro_equipo() -> None:
    assert shares_first_author("Šubjak et al. 2022", r"A. Doe, J. \v{S}mith") is False
    assert shares_first_author("Bjørn et al. 2021", r"A. Doe, B. Bj{\o}rk") is False
