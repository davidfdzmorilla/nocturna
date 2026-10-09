"""Reglas puras del resumen semanal del archivo (T84)."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes.archive_source import make_solution

from nocturna.application.archive_digest_text import format_archive_value
from nocturna.domain.archive import ArchiveParameterValue
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

_AT = datetime(2026, 10, 9, 3, 0, tzinfo=UTC)
_MADRID = ZoneInfo("Europe/Madrid")


def _t(old: str | None, new: str | None, seen: bool = True, name: str = "P b"):
    return DefaultTransition(name, old, new, _AT, seen)


def test_clasificacion():
    assert classify_transition(_t("a", "b")) == TransitionKind.CHANGED
    assert classify_transition(_t(None, "b", False)) == TransitionKind.NEW_PLANET
    assert classify_transition(_t(None, "b", True)) == TransitionKind.REGAINED
    assert classify_transition(_t("a", None)) == TransitionKind.LOST


def test_parameter_changes_ignora_formato_numerico_y_detecta_procedencia():
    a = make_solution("P b", "A", mass=1.5)
    b = make_solution("P b", "B", mass=1.50)
    assert parameter_changes(a, b) == ()
    c = make_solution("P b", "C", mass=2.0)
    (change,) = parameter_changes(a, c)
    assert change.parameter == MeasuredParameter.MASS
    from dataclasses import replace

    d = replace(b, pl_bmassprov="Msini")
    (prov,) = parameter_changes(a, d)
    assert prov.new_mass_provenance == "Msini"


def test_digest_orden_determinista():
    a = make_solution("B b", "A", default=True)
    b = make_solution("B b", "B", default=True, mass=3.0)
    sols = {a.solution_key: a, b.solution_key: b}
    ts = [
        _t(a.solution_key, None, name="Z b"),
        _t(None, b.solution_key, False, name="A b"),
        _t(a.solution_key, b.solution_key, name="M b"),
    ]
    sols_all = {**sols}
    digest = build_weekly_digest("2026-W41", 1, ts, sols_all)
    assert [e.kind for e in digest.entries] == [
        TransitionKind.CHANGED,
        TransitionKind.NEW_PLANET,
        TransitionKind.LOST,
    ]


def test_semanas_iso():
    assert iso_week_of(datetime(2026, 10, 9, 12, tzinfo=UTC), _MADRID) == "2026-W41"
    # domingo 23:30 UTC = lunes en Madrid
    assert iso_week_of(datetime(2026, 10, 11, 23, 30, tzinfo=UTC), _MADRID) == "2026-W42"
    assert iso_week_of(datetime(2020, 12, 31, 12, tzinfo=UTC), _MADRID) == "2020-W53"
    start, end = week_bounds("2026-W43", _MADRID)  # cambio de hora el 25-oct
    assert (end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() == 7 * 24 * 3600 + 3600
    week_bounds("2020-W53", _MADRID)
    for bad in ("2025-W53", "2026-41", "2026-W5", "x", "2026-W00"):
        with pytest.raises(ValueError):
            week_bounds(bad, _MADRID)


def test_formato_de_valor():
    p = ArchiveParameterValue(value=5.2, err1=0.3, err2=-0.3, lim=0)
    assert format_archive_value(p, MeasuredParameter.MASS) == "5,2 ± 0,3 M⊕"
    q = ArchiveParameterValue(value=5.2, err1=0.4, err2=-0.3, lim=1)
    assert format_archive_value(q, MeasuredParameter.RADIUS) == "< 5,2 R⊕"
    assert format_archive_value(None, MeasuredParameter.PERIOD) == "sin valor"
