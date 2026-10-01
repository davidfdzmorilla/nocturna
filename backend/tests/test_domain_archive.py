"""Dominio puro de T81: `solution_key`, `collapse_duplicates`, `diff_snapshot`."""

import ast
import random
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from nocturna.domain.archive import (
    ArchiveParameterValue,
    ArchiveSolution,
    DefaultChange,
    collapse_duplicates,
    diff_snapshot,
)
from nocturna.domain.errors import InvariantViolation

GOLDEN_KEY = "7c8e61d2ce6b1a783db30f2778e516b6c0f638f5f0606527f013c05880e1db9a"
GOLDEN_CANON = (
    "V1298 Tau b|2019AJ....158...79D|Published Confirmed|||||10.22|0.55|-0.59|0|"
    "24.13861|0.00102|-0.0009|0"
)


def sol(**kw) -> ArchiveSolution:
    base = {
        "pl_name": "V1298 Tau b",
        "hostname": "V1298 Tau",
        "pl_refname": "<a href=https://ui.adsabs.harvard.edu/abs/2019AJ....158...79D/abstract>x</a>",
        "ref_key": "2019AJ....158...79D",
        "ref_text": "David et al. 2019",
        "arxiv_id": None,
        "soltype": "Published Confirmed",
        "releasedate": date(2019, 6, 27),
        "pl_pubdate": "2019-08",
        "is_default": False,
        "mass": ArchiveParameterValue(),
        "radius": ArchiveParameterValue(10.22, 0.55, -0.59, 0),
        "period": ArchiveParameterValue(24.13861, 0.00102, -0.0009, 0),
        "pl_bmassprov": None,
        "st_rad": ArchiveParameterValue(1.314, 0.052, -0.064),
        "st_mass": ArchiveParameterValue(1.099, 0.049, -0.049),
        "discoverymethod": "Transit",
        "ttv_flag": True,
        "pl_controv_flag": False,
    }
    base.update(kw)
    return ArchiveSolution(**base)


# ---------------------------------------------------------------- solution_key


def test_clave_dorada_v1_exacta():
    assert sol().solution_key == GOLDEN_KEY


def test_la_cadena_canonica_documentada_produce_la_clave_dorada():
    import hashlib

    assert hashlib.sha256(GOLDEN_CANON.encode()).hexdigest() == GOLDEN_KEY


@pytest.mark.parametrize("v", [1.50, 1.5, float("1.5e0")])
def test_representaciones_equivalentes_de_un_float_dan_la_misma_clave(v):
    a = sol(mass=ArchiveParameterValue(1.5, 0.1, -0.1, 0))
    b = sol(mass=ArchiveParameterValue(v, 0.1, -0.1, 0))
    assert a.solution_key == b.solution_key


def test_none_y_cadena_vacia_dan_la_misma_clave():
    a = sol(soltype=None)
    b = sol(soltype="")
    assert a.solution_key == b.solution_key


def test_menos_cero_y_cero_dan_la_misma_clave():
    a = sol(mass=ArchiveParameterValue(-0.0, 0.0, -0.0, 0))
    b = sol(mass=ArchiveParameterValue(0.0, -0.0, 0.0, 0))
    assert a.solution_key == b.solution_key


def test_cambiar_soltype_cambia_la_clave():
    assert sol(soltype="Other").solution_key != GOLDEN_KEY


@pytest.mark.parametrize("param", ["mass", "radius", "period"])
@pytest.mark.parametrize("field", ["value", "err1", "err2", "lim"])
def test_cambiar_cualquiera_de_los_12_valores_cambia_la_clave(param, field):
    current = getattr(sol(), param)
    new_val = 1 if field == "lim" and current.lim != 1 else (7.0 if field != "lim" else -1)
    changed = replace(current, **{field: new_val})
    assert sol(**{param: changed}).solution_key != GOLDEN_KEY


def test_cambiar_pl_name_o_ref_key_cambia_la_clave():
    assert sol(pl_name="V1298 Tau c").solution_key != GOLDEN_KEY
    assert sol(ref_key="otra").solution_key != GOLDEN_KEY


@pytest.mark.parametrize(
    "changes",
    [
        {"hostname": "otro"},
        {"releasedate": date(2030, 1, 1)},
        {"st_rad": ArchiveParameterValue(9.0, 9.0, -9.0)},
        {"st_mass": ArchiveParameterValue(9.0, 9.0, -9.0)},
        {"pl_pubdate": "1999-01"},
        {"is_default": True},
        {"discoverymethod": "RV"},
        {"ttv_flag": None},
        {"pl_controv_flag": True},
        {"pl_bmassprov": "Msini"},
        {"arxiv_id": "1234.56789"},
        {"ref_text": "otro texto"},
    ],
)
def test_campos_no_incluidos_no_cambian_la_clave(changes):
    assert sol(**changes).solution_key == GOLDEN_KEY


def test_mismo_bibcode_con_distinto_refstr_y_espacios_da_la_misma_clave():
    from nocturna.infrastructure.exoplanet_archive.mappers import reference_key

    a = reference_key(
        "<a refstr=A href=https://ui.adsabs.harvard.edu/abs/"
        "2019AJ....158...79D/abstract target=ref>David 2019</a>"
    )
    b = reference_key(
        "<a  refstr=B_OTRO   href=https://ui.adsabs.harvard.edu/abs/"
        "2019AJ....158...79D/abstract target=ref>  David   et al. 2019 </a>"
    )
    assert a == b == "2019AJ....158...79D"
    assert sol(ref_key=a).solution_key == sol(ref_key=b).solution_key == GOLDEN_KEY


# ---------------------------------------------------------- collapse_duplicates


def test_collapse_fusiona_y_cuenta():
    a = sol()
    b = sol(hostname="dup")
    c = sol(pl_name="X b", ref_key="r")
    rows, merged = collapse_duplicates([a, b, c])
    assert merged == 1
    assert len(rows) == 2


def test_collapse_sin_duplicados_devuelve_todo_y_cero():
    rows, merged = collapse_duplicates([sol(), sol(ref_key="otra")])
    assert (len(rows), merged) == (2, 0)


def test_collapse_is_default_es_el_or_del_grupo():
    rows, _ = collapse_duplicates(
        [
            sol(is_default=False, releasedate=date(2025, 1, 1)),
            sol(is_default=True, releasedate=date(2020, 1, 1)),
        ]
    )
    assert rows[0].is_default is True


def test_collapse_elige_default_primero_y_luego_releasedate_mas_reciente():
    old_default = sol(is_default=True, releasedate=date(2020, 1, 1), hostname="old_default")
    new_nondefault = sol(is_default=False, releasedate=date(2025, 1, 1), hostname="new")
    rows, _ = collapse_duplicates([new_nondefault, old_default])
    assert rows[0].hostname == "old_default"

    a = sol(releasedate=date(2020, 1, 1), hostname="a")
    b = sol(releasedate=date(2025, 1, 1), hostname="b")
    rows, _ = collapse_duplicates([a, b])
    assert rows[0].hostname == "b"


def test_collapse_es_independiente_del_orden_de_entrada():
    base = [
        sol(hostname="a", releasedate=date(2020, 1, 1), is_default=True),
        sol(hostname="b", releasedate=date(2025, 1, 1)),
        sol(hostname="c", releasedate=date(2025, 1, 1), pl_refname="zzz"),
        sol(pl_name="Y", ref_key="k", hostname="d"),
        sol(pl_name="Y", ref_key="k", hostname="e"),
        sol(pl_name="A", ref_key="q"),
    ]
    expected = collapse_duplicates(base)
    rng = random.Random(1)
    for _ in range(20):
        shuffled = base[:]
        rng.shuffle(shuffled)
        assert collapse_duplicates(shuffled) == expected


# --------------------------------------------------------------- diff_snapshot


def s(name, ref, default=False):
    return sol(pl_name=name, ref_key=ref, is_default=default)


def test_diff_completo_altas_bajas_y_reactivacion():
    a, b, c = s("P", "a"), s("P", "b"), s("P", "c")
    d = diff_snapshot(
        [a, c],
        active={b.solution_key: "P", a.solution_key: "P"},
        removed=[c.solution_key],
        previous_defaults={},
        removal_scope=None,
    )
    assert d.added == ()
    assert d.reactivated == (c.solution_key,)
    assert d.removed == (b.solution_key,)


def test_diff_alta_nueva():
    a = s("P", "a")
    d = diff_snapshot([a], active={}, removed=[], previous_defaults={}, removal_scope=None)
    assert d.added == (a.solution_key,)
    assert d.removed == d.reactivated == ()


def test_diff_completo_da_de_baja_cualquier_planeta():
    old = s("Z", "z")
    d = diff_snapshot(
        [], active={old.solution_key: "Z"}, removed=[], previous_defaults={}, removal_scope=None
    )
    assert d.removed == (old.solution_key,)


def test_diff_incremental_solo_da_bajas_dentro_del_alcance():
    inside, outside = s("In", "a"), s("Out", "b")
    d = diff_snapshot(
        [],
        active={inside.solution_key: "In", outside.solution_key: "Out"},
        removed=[],
        previous_defaults={},
        removal_scope=frozenset({"In"}),
    )
    assert d.removed == (inside.solution_key,)


def test_diff_incremental_lost_defaults_solo_dentro_del_alcance():
    d = diff_snapshot(
        [],
        active={},
        removed=[],
        previous_defaults={"In": "k1", "Out": "k2"},
        removal_scope=frozenset({"In"}),
    )
    assert d.lost_defaults == ("In",)
    assert d.default_changes == ()


def test_diff_cambio_de_default_con_old_informado():
    old, new = s("P", "a", True), s("P", "b", True)
    d = diff_snapshot(
        [new],
        active={old.solution_key: "P"},
        removed=[],
        previous_defaults={"P": old.solution_key},
        removal_scope=None,
    )
    assert d.default_changes == (DefaultChange("P", old.solution_key, new.solution_key),)


def test_diff_default_de_planeta_nuevo_con_old_none():
    other, new = s("Q", "q", True), s("P", "b", True)
    d = diff_snapshot(
        [other, new],
        active={other.solution_key: "Q"},
        removed=[],
        previous_defaults={"Q": other.solution_key},
        removal_scope=None,
    )
    assert d.default_changes == (DefaultChange("P", None, new.solution_key),)


def test_diff_default_sin_cambio_no_genera_cambio():
    a = s("P", "a", True)
    d = diff_snapshot(
        [a],
        active={a.solution_key: "P"},
        removed=[],
        previous_defaults={"P": a.solution_key},
        removal_scope=None,
    )
    assert d.default_changes == () and d.lost_defaults == ()


def test_diff_primer_snapshot_no_tiene_default_changes_ni_lost():
    a = s("P", "a", True)
    d = diff_snapshot([a], active={}, removed=[], previous_defaults={}, removal_scope=None)
    assert d.default_changes == () and d.lost_defaults == ()
    assert d.added == (a.solution_key,)


def test_diff_planeta_que_pierde_default_va_a_lost_defaults():
    a, other = s("P", "a", False), s("Q", "q", True)
    d = diff_snapshot(
        [a, other],
        active={a.solution_key: "P", other.solution_key: "Q"},
        removed=[],
        previous_defaults={"P": a.solution_key, "Q": other.solution_key},
        removal_scope=None,
    )
    assert d.lost_defaults == ("P",)
    assert d.default_changes == ()


def test_diff_dos_defaults_para_un_planeta_es_violacion_de_invariante():
    with pytest.raises(InvariantViolation):
        diff_snapshot(
            [s("P", "a", True), s("P", "b", True)],
            active={},
            removed=[],
            previous_defaults={},
            removal_scope=None,
        )


# ---------------------------------------------------------------------- pureza


def test_domain_archive_solo_importa_stdlib_y_nocturna_domain():
    import sys

    path = Path(__file__).resolve().parents[1] / "src" / "nocturna" / "domain" / "archive.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    stdlib = sys.stdlib_module_names
    bad = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods = [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            mods = [node.module or ""]
        else:
            continue
        for m in mods:
            top = m.split(".")[0]
            if top in stdlib or m == "nocturna.domain" or m.startswith("nocturna.domain."):
                continue
            bad.append(m)
    assert bad == []
