"""Reglas puras del resumen semanal del archivo (T84): casos de la batería del tester.

Complementa `test_domain_archive_digest.py` (humo) sin repetirlo. Sin base de datos.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fakes.archive_source import make_solution

from nocturna.domain.archive import ArchiveParameterValue as V
from nocturna.domain.archive_digest import (
    DefaultTransition,
    TransitionKind,
    build_weekly_digest,
    classify_transition,
    iso_week_of,
    parameter_changes,
    week_bounds,
)
from nocturna.domain.entities import MeasuredParameter
from nocturna.domain.errors import InvariantViolation

_MADRID = ZoneInfo("Europe/Madrid")
_AT = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)


def _base():
    return make_solution("P b", "A", mass=1.0)


def _with(sol, **fields):
    return replace(sol, **fields)


def _tr(old, new, *, name="P b", at=_AT, seen=True):
    return DefaultTransition(name, old, new, at, seen)


# --- classify_transition -------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "seen", "expected"),
    [
        ("a", "b", True, TransitionKind.CHANGED),
        ("a", "b", False, TransitionKind.CHANGED),
        (None, "b", False, TransitionKind.NEW_PLANET),
        (None, "b", True, TransitionKind.REGAINED),
        ("a", None, True, TransitionKind.LOST),
        ("a", None, False, TransitionKind.LOST),
    ],
)
def test_classify_transition_cubre_los_cuatro_tipos(old, new, seen, expected):
    assert classify_transition(_tr(old, new, seen=seen)) == expected


def test_classify_transition_sin_claves_es_una_incoherencia():
    with pytest.raises(InvariantViolation):
        classify_transition(_tr(None, None))


# --- parameter_changes ---------------------------------------------------


def test_parameter_changes_valor_distinto():
    old = _base()
    new = _with(old, mass=V(value=2.0, err1=0.1, err2=-0.1, lim=0))
    (change,) = parameter_changes(old, new)
    assert change.parameter == MeasuredParameter.MASS
    assert change.old.value == 1.0 and change.new.value == 2.0


def test_parameter_changes_solo_errores_distintos():
    old = _base()
    new = _with(old, mass=V(value=old.mass.value, err1=0.5, err2=old.mass.err2, lim=old.mass.lim))
    (change,) = parameter_changes(old, new)
    assert change.parameter == MeasuredParameter.MASS
    assert change.old.err1 == 0.1 and change.new.err1 == 0.5
    new2 = _with(old, mass=V(value=old.mass.value, err1=old.mass.err1, err2=-0.7, lim=0))
    assert [c.parameter for c in parameter_changes(old, new2)] == [MeasuredParameter.MASS]


def test_parameter_changes_un_parametro_aparece():
    old = _with(_base(), radius=V())
    new = _with(old, radius=V(value=1.2, err1=0.1, err2=-0.1, lim=0))
    (change,) = parameter_changes(old, new)
    assert change.parameter == MeasuredParameter.RADIUS
    assert change.old.value is None and change.new.value == 1.2


def test_parameter_changes_un_parametro_desaparece():
    new = _with(_base(), period=V())
    old = _with(new, period=V(value=3.3, err1=None, err2=None, lim=None))
    (change,) = parameter_changes(old, new)
    assert change.parameter == MeasuredParameter.PERIOD
    assert change.old.value == 3.3 and change.new.value is None


def test_parameter_changes_limite_distinto_con_mismo_valor():
    old = _base()
    new = _with(old, mass=V(value=old.mass.value, err1=old.mass.err1, err2=old.mass.err2, lim=1))
    (change,) = parameter_changes(old, new)
    assert change.parameter == MeasuredParameter.MASS
    assert change.old.lim == 0 and change.new.lim == 1


def test_parameter_changes_msini_a_mass_con_mismo_valor_informa_procedencias():
    old = _with(_base(), pl_bmassprov="Msini")
    new = _with(old, pl_bmassprov="Mass")
    (change,) = parameter_changes(old, new)
    assert change.parameter == MeasuredParameter.MASS
    assert (change.old_mass_provenance, change.new_mass_provenance) == ("Msini", "Mass")
    assert change.old == change.new  # el valor no cambia; solo la procedencia


def test_parameter_changes_radio_y_periodo_no_llevan_procedencia():
    old = _base()
    new = _with(
        old,
        radius=V(value=9.0, err1=0.1, err2=-0.1, lim=0),
        period=V(value=9.0, err1=None, err2=None, lim=None),
    )
    changes = parameter_changes(old, new)
    assert [c.parameter for c in changes] == [MeasuredParameter.RADIUS, MeasuredParameter.PERIOD]
    assert all(c.old_mass_provenance is None and c.new_mass_provenance is None for c in changes)


def test_parameter_changes_sin_cambios_devuelve_tupla_vacia():
    old = _base()
    assert parameter_changes(old, _with(old, ref_key="otra", ref_text="otra")) == ()
    assert parameter_changes(old, old) == ()


def test_parameter_changes_1_5_y_1_50_no_cuentan_como_cambio():
    old = _with(_base(), mass=V(value=float("1.5"), err1=0.1, err2=-0.1, lim=0))
    new = _with(old, mass=V(value=float("1.50"), err1=0.10, err2=-0.10, lim=0))
    assert parameter_changes(old, new) == ()


def test_parameter_changes_canoniza_entero_y_cero_con_signo():
    """Misma canonización que `solution_key`: 2 == 2.0 y -0.0 == 0.0."""
    old = _with(_base(), mass=V(value=2, err1=0.0, err2=-0.0, lim=0))
    new = _with(old, mass=V(value=2.0, err1=-0.0, err2=0.0, lim=0))
    assert parameter_changes(old, new) == ()


def test_parameter_changes_varios_parametros_salen_en_orden_masa_radio_periodo():
    old = _base()
    new = _with(
        old,
        mass=V(value=5.0, err1=0.1, err2=-0.1, lim=0),
        radius=V(value=5.0, err1=0.1, err2=-0.1, lim=0),
        period=V(value=5.0),
    )
    assert [c.parameter.value for c in parameter_changes(old, new)] == [
        "mass",
        "radius",
        "period",
    ]


# --- build_weekly_digest -------------------------------------------------


def test_digest_orden_tipo_planeta_fecha_con_dos_transiciones_del_mismo_planeta():
    a = make_solution("Z b", "A", default=True, mass=1.0)
    b = make_solution("Z b", "B", default=True, mass=2.0)
    sols = {a.solution_key: a, b.solution_key: b}
    ak, bk = a.solution_key, b.solution_key
    d1, d2, d3 = (_AT + timedelta(days=i) for i in range(3))
    transitions = [  # entrada deliberadamente desordenada
        _tr(ak, None, name="A b", at=d1),
        _tr(None, bk, name="M b", at=d1, seen=True),  # REGAINED
        _tr(None, bk, name="B b", at=d1, seen=False),  # NEW_PLANET
        _tr(bk, ak, name="Z b", at=d3),  # CHANGED, segunda de la semana
        _tr(ak, bk, name="Z b", at=d2),  # CHANGED, primera
        _tr(ak, bk, name="C b", at=d3),  # CHANGED, otro planeta
        _tr(None, bk, name="A b", at=d2, seen=False),  # NEW_PLANET
    ]
    digest = build_weekly_digest("2026-W41", 3, transitions, sols)
    got = [(e.kind, e.pl_name, e.snapshot_taken_at) for e in digest.entries]
    assert got == [
        (TransitionKind.CHANGED, "C b", d3),
        (TransitionKind.CHANGED, "Z b", d2),
        (TransitionKind.CHANGED, "Z b", d3),
        (TransitionKind.NEW_PLANET, "A b", d2),
        (TransitionKind.NEW_PLANET, "B b", d1),
        (TransitionKind.REGAINED, "M b", d1),
        (TransitionKind.LOST, "A b", d1),
    ]
    assert digest.week == "2026-W41" and digest.snapshots == 3


def test_digest_es_independiente_del_orden_de_entrada():
    a = make_solution("Z b", "A", default=True, mass=1.0)
    b = make_solution("Z b", "B", default=True, mass=2.0)
    sols = {a.solution_key: a, b.solution_key: b}
    ts = [
        _tr(a.solution_key, b.solution_key, name="Z b"),
        _tr(None, b.solution_key, name="A b", seen=False),
        _tr(b.solution_key, None, name="B b"),
    ]
    assert build_weekly_digest("2026-W41", 1, ts, sols) == build_weekly_digest(
        "2026-W41", 1, list(reversed(ts)), sols
    )


def test_digest_changed_lleva_cambios_y_el_resto_no():
    a = make_solution("Z b", "A", default=True, mass=1.0)
    b = make_solution("Z b", "B", default=True, mass=2.0)
    sols = {a.solution_key: a, b.solution_key: b}
    digest = build_weekly_digest(
        "2026-W41",
        1,
        [_tr(a.solution_key, b.solution_key), _tr(None, b.solution_key, name="N b", seen=False)],
        sols,
    )
    changed, new = digest.entries
    assert [c.parameter for c in changed.parameter_changes] == [MeasuredParameter.MASS]
    assert new.old is None and new.new == b and new.parameter_changes == ()


@pytest.mark.parametrize("missing", ["old", "new"])
def test_digest_con_clave_ausente_es_invariant_violation(missing):
    a = make_solution("Z b", "A", default=True)
    sols = {a.solution_key: a}
    ghost = "f" * 64
    t = _tr(ghost, a.solution_key) if missing == "old" else _tr(a.solution_key, ghost)
    with pytest.raises(InvariantViolation):
        build_weekly_digest("2026-W41", 1, [t], sols)


def test_digest_vacio():
    digest = build_weekly_digest("2026-W41", 2, [], {})
    assert digest.entries == () and digest.snapshots == 2


# --- semanas ISO ---------------------------------------------------------


def test_week_bounds_cambio_de_hora_de_otono_dura_una_hora_mas():
    start, end = week_bounds("2026-W43", _MADRID)  # el domingo 25-oct se retrasa el reloj
    assert start.isoformat() == "2026-10-19T00:00:00+02:00"
    assert end.isoformat() == "2026-10-26T00:00:00+01:00"
    assert end - start == timedelta(days=7)  # aritmética de reloj de pared
    assert end.astimezone(UTC) - start.astimezone(UTC) == timedelta(days=7, hours=1)


def test_week_bounds_cambio_de_hora_de_primavera_dura_una_hora_menos():
    start, end = week_bounds("2026-W13", _MADRID)  # 29-mar se adelanta el reloj
    assert end.astimezone(UTC) - start.astimezone(UTC) == timedelta(days=7) - timedelta(hours=1)


def test_instantes_en_el_borde_del_cambio_de_hora_caen_en_la_semana_correcta():
    start, end = week_bounds("2026-W43", _MADRID)
    last = datetime(2026, 10, 25, 22, 59, 59, tzinfo=UTC)  # 23:59:59 CET del domingo
    first_next = datetime(2026, 10, 25, 23, 0, tzinfo=UTC)  # lunes 00:00 CET
    assert start <= last < end and iso_week_of(last, _MADRID) == "2026-W43"
    assert not (first_next < end) and iso_week_of(first_next, _MADRID) == "2026-W44"
    assert first_next == end
    # la hora repetida (02:30 CEST y 02:30 CET) sigue en la misma semana
    for hour_utc in (0, 1):
        at = datetime(2026, 10, 25, hour_utc, 30, tzinfo=UTC)
        assert iso_week_of(at, _MADRID) == "2026-W43" and start <= at < end


def test_semana_53_de_2026():
    assert iso_week_of(datetime(2026, 12, 31, 12, tzinfo=UTC), _MADRID) == "2026-W53"
    assert iso_week_of(datetime(2027, 1, 3, 12, tzinfo=UTC), _MADRID) == "2026-W53"
    assert iso_week_of(datetime(2027, 1, 4, 12, tzinfo=UTC), _MADRID) == "2027-W01"
    start, end = week_bounds("2026-W53", _MADRID)
    assert start.date().isoformat() == "2026-12-28" and end.date().isoformat() == "2027-01-04"
    assert iso_week_of(start, _MADRID) == "2026-W53"


def test_la_semana_iso_de_enero_puede_pertenecer_al_anio_anterior():
    assert iso_week_of(datetime(2027, 1, 1, 12, tzinfo=UTC), _MADRID) == "2026-W53"


def test_iso_week_de_un_datetime_naive_es_un_error():
    with pytest.raises(ValueError):
        iso_week_of(datetime(2026, 10, 9, 12), _MADRID)


@pytest.mark.parametrize(
    "bad",
    ["", "x", "2026", "2026-41", "2026-W5", "2026-W041", "26-W41", "2026W41", "2026-w41",
     " 2026-W41", "2026-W41 ", "2026-W00", "2026-W54", "2027-W53", "2025-W53"],
)  # fmt: skip
def test_week_bounds_formato_o_semana_invalidos_son_value_error(bad):
    with pytest.raises(ValueError):
        week_bounds(bad, _MADRID)


def test_week_bounds_rechaza_salto_de_linea_final():
    with pytest.raises(ValueError):
        week_bounds("2026-W41\n", _MADRID)


def test_week_bounds_ida_y_vuelta_con_iso_week_of():
    for week in ("2026-W01", "2026-W20", "2026-W41", "2026-W52", "2026-W53"):
        start, end = week_bounds(week, _MADRID)
        assert iso_week_of(start, _MADRID) == week
        assert iso_week_of(end - timedelta(seconds=1), _MADRID) == week
        assert iso_week_of(end, _MADRID) != week
