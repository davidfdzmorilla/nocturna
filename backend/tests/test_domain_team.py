"""T100 (D1): primer autor de la referencia frente a los autores del paper."""

import re

import pytest

from nocturna.domain.team import _normalize, reference_first_author, shares_first_author


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


@pytest.mark.parametrize(
    ("refname", "authors"),
    [
        ("Müller et al. 2020", "A. Mueller, B. Doe"),
        ("Mueller et al. 2020", "A. Muller, B. Doe"),
        ("Muller et al. 2020", "A. Müller, B. Doe"),
        ("Djokovic et al. 2020", "A. Đokovic, B. Doe"),
    ],
)
def test_pliegue_de_digrafos_en_palabras_largas(refname: str, authors: str) -> None:
    assert shares_first_author(refname, authors) is True


def test_pliegue_no_afecta_a_palabras_cortas() -> None:
    assert shares_first_author("Xue et al. 2020", "A. Xu, B. Doe") is False


# --- T103: pliegue de transliteraciones, batería ampliada -------------------

_CASAN_AMBOS_SENTIDOS = [
    ("Müller et al. 2021", "Mueller"),
    ("Müller et al. 2021", "Muller"),
    ("Müller et al. 2021", 'M\\"uller'),
    ("Müller et al. 2021", 'M\\"{u}ller'),
    ("Mueller et al. 2021", "Müller"),
    ("Mueller et al. 2021", 'M{\\"u}ller'),
    ("Mueller et al. 2021", "Muller"),
    ("Muller et al. 2021", "Mueller"),
    ("Schröder et al. 2021", "Schroeder"),
    ("Schröder et al. 2021", "Schroder"),
    ("Schroeder et al. 2021", "Schroder"),
    ("Jäger et al. 2021", "Jaeger"),
    ("Kühn et al. 2021", "Kuehn"),
    ("Bjørn et al. 2021", "Bjoern"),
    ("Åberg et al. 2021", "Aaberg"),
    ("Næss et al. 2021", "Naess"),
    ("Đorđević et al. 2021", "\\DJ{}or\\dj{}evi\\'c"),
    ("Đorđević et al. 2021", "{\\dj}or{\\dj}evi\\'c"),
    ("Đorđević et al. 2021", "Djordjevic"),
    ("Đorđević et al. 2021", "Dordevic"),
    ("Djordjević et al. 2021", "Dordevic"),
    ("de Groeve et al. 2021", "de Gröve"),
]


@pytest.mark.parametrize(("refname", "authors"), _CASAN_AMBOS_SENTIDOS)
def test_t103_casan_en_un_sentido(refname: str, authors: str) -> None:
    assert shares_first_author(refname, f"A. Doe, {authors}") is True


@pytest.mark.parametrize(
    ("refname", "authors"),
    # Un refname que empieza por `\\` o `{` no se reconoce: lo cubre el test de límite.
    [case for case in _CASAN_AMBOS_SENTIDOS if case[1][0].isalpha()],
)
def test_t103_casan_en_el_sentido_inverso(refname: str, authors: str) -> None:
    # La referencia es la variante de `authors` y los autores la de `refname`.
    surname = reference_first_author(refname)
    assert surname is not None
    assert shares_first_author(f"{authors} et al. 2021", f"A. Doe, {surname}") is True


def test_t103_eszett_mayuscula_con_strasser() -> None:
    assert shares_first_author("STRAẞER et al. 2021", "A. Doe, Strasser") is True
    assert shares_first_author("Strasser et al. 2021", "A. Doe, STRAẞER") is True


@pytest.mark.parametrize(
    ("refname", "authors"),
    [
        ("Xue et al. 2020", "A. Xu"),
        ("Xu et al. 2020", "A. Xue"),
        ("Yue et al. 2020", "A. Yu"),
        ("Yuen et al. 2020", "A. Yun"),
        ("Chae et al. 2020", "A. Cha"),
        ("Müller et al. 2020", "A. Miller"),
        ("Müller et al. 2020", "A. Möller"),
        ("Mueller et al. 2020", "A. Mullen"),
        ("Wang et al. 2020", "A. Wangsness"),
        ("Knudstrup et al. 2020", "A. Smith"),
        ("Knudstrup et al. 2020", "A. Doe"),
    ],
)
def test_t103_no_casan_otro_apellido(refname: str, authors: str) -> None:
    assert shares_first_author(refname, authors) is False


def test_t103_limite_documentado_baer_bar_no_cubierto() -> None:
    assert shares_first_author("Baer et al. 2020", "A. Doe, B. Bär") is False


def test_t103_limite_documentado_falso_aceptado_rafael_rafal() -> None:
    assert shares_first_author("Rafael et al. 2020", "A. Doe, Rafał") is True


@pytest.mark.parametrize(
    "name",
    [
        "Müller", "Mueller", "Schröder", "Jäger", "Kühn", "Bjørn", "Åberg", "Næss",
        "Đorđević", "Strasser", "Xue", "Yuen", "Chae", "Wang", "Œ", "Ng",
        'M\\"uller', 'M\\"{u}ller', "Bj{\\o}rn", "N{\\ae}ss",
        "Stra{\\ss}er", "de Groeve",
    ],
)  # fmt: skip
def test_t103_reflexividad(name: str) -> None:
    assert shares_first_author(f"{name} et al. 2020", f"A. Doe, {name}") is True


def test_t103_none_intacto() -> None:
    assert shares_first_author("Müller et al. 2021", None) is None
    assert shares_first_author("Müller et al. 2021", "") is None
    assert shares_first_author("Müller et al. 2021", "   ") is None
    assert shares_first_author("sin año", "A. Doe") is None
    assert shares_first_author("ExoFOP-TESS TOI", "Mueller") is None


def _antiguo(refname: str, authors: str | None) -> bool | None:
    """Comparación previa a T103: solo `_normalize`, sin pliegue."""
    if authors is None:
        return None
    surname = reference_first_author(refname)
    if surname is None:
        return None
    wanted = _normalize(surname)
    normalized = _normalize(authors)
    if not wanted or not normalized:
        return None
    return re.search(rf"(?<![a-z0-9]){re.escape(wanted)}(?![a-z0-9])", normalized) is not None


def test_t103_propiedad_solo_false_a_true_y_none_no_cambia() -> None:
    nombres = [
        "Müller", "Mueller", "Muller", "Miller", "Möller", "Mullen", "Schröder",
        "Schroeder", "Schroder", "Jäger", "Jaeger", "Kühn", "Kuehn", "Bjørn",
        "Bjoern", "Bjorn", "Åberg", "Aaberg", "Næss", "Naess", "Đorđević",
        "Djordjevic", "Dordevic", "Strasser", "Straßer", "Xue", "Xu", "Yue", "Yu",
        "Yuen", "Yun", "Chae", "Cha", "Wang", "Wangsness", "Knudstrup", "Smith",
        "Doe", "Baer", "Bär", "Rafael", "Rafał", "Aerts", "Arts", "de Groeve",
        "de Gröve", 'M\\"uller', "{\\dj}or{\\dj}evi\\'c", "Œ", "Ng", "van der Berg",
    ]  # fmt: skip
    for ref in nombres:
        for aut in nombres:
            refname = f"{ref} et al. 2020"
            for authors in (f"A. Doe, {aut}", aut, f"{aut}, A. Doe; B. {aut}"):
                antes = _antiguo(refname, authors)
                ahora = shares_first_author(refname, authors)
                assert (antes is None) == (ahora is None), (refname, authors)
                assert not (antes is True and ahora is False), (refname, authors)


@pytest.mark.parametrize("name", ["{\\AA}berg", "\\DJ{}or\\dj{}evi\\'c", "{\\dj}or{\\dj}evi\\'c"])
def test_t103_limite_refname_que_empieza_por_tex_es_none(name: str) -> None:
    # `_REFERENCE_RE` exige que el apellido empiece por letra; los refname del
    # archivo son texto plano, el TeX solo aparece en los autores de arXiv.
    assert shares_first_author(f"{name} et al. 2020", f"A. Doe, {name}") is None
