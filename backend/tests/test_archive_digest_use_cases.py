"""Casos de uso del resumen semanal (T84) con un lector falso en memoria. Sin base de datos."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from fakes.archive_source import make_solution

from nocturna.application.use_cases.archive_digest import GetWeeklyDigest, ListDigestWeeks
from nocturna.domain.archive_digest import DefaultTransition, TransitionKind, week_bounds

_TZ = ZoneInfo("Europe/Madrid")
_W40 = datetime(2026, 10, 1, 1, tzinfo=UTC)
_W41 = datetime(2026, 10, 8, 1, tzinfo=UTC)
_W41B = datetime(2026, 10, 9, 1, tzinfo=UTC)
_W43_SUN = datetime(2026, 10, 25, 22, 30, tzinfo=UTC)  # domingo 23:30 en Madrid


class FakeDigestReader:
    def __init__(self, snapshots, transitions, solutions):
        self._snapshots = snapshots
        self._transitions = transitions
        self._solutions = solutions
        self.range_calls: list[tuple[datetime, datetime]] = []

    def snapshot_weeks(self, tz):
        from nocturna.domain.archive_digest import iso_week_of

        counts: dict[str, int] = {}
        for at in self._snapshots:
            w = iso_week_of(at, tz)
            counts[w] = counts.get(w, 0) + 1
        return sorted(counts.items())

    def transitions_between(self, start, end):
        self.range_calls.append((start, end))
        return [t for t in self._transitions if start <= t.snapshot_taken_at < end]

    def solutions_by_key(self, keys):
        return {k: v for k, v in self._solutions.items() if k in set(keys)}


def _reader():
    a = make_solution("A b", "R1", default=True, mass=1.0)
    b = make_solution("A b", "R2", default=True, mass=2.0)
    n = make_solution("N b", "R1", default=True)
    ts = [
        DefaultTransition("A b", a.solution_key, b.solution_key, _W40, True),
        DefaultTransition("N b", None, n.solution_key, _W40, False),
        DefaultTransition("A b", b.solution_key, None, _W41, True),
        DefaultTransition("A b", None, b.solution_key, _W41B, True),
        DefaultTransition("N b", n.solution_key, None, _W43_SUN, True),
    ]
    sols = {s.solution_key: s for s in (a, b, n)}
    return FakeDigestReader([_W40, _W41, _W41B, _W43_SUN], ts, sols)


def test_list_digest_weeks_orden_descendente_y_recuentos_por_tipo():
    weeks = ListDigestWeeks(_reader(), _TZ)()
    assert [(w.week, w.snapshots) for w in weeks] == [
        ("2026-W43", 1),
        ("2026-W41", 2),
        ("2026-W40", 1),
    ]
    by_week = {w.week: w.counts for w in weeks}
    assert by_week["2026-W40"] == {
        TransitionKind.CHANGED: 1,
        TransitionKind.NEW_PLANET: 1,
        TransitionKind.REGAINED: 0,
        TransitionKind.LOST: 0,
    }
    assert by_week["2026-W41"][TransitionKind.LOST] == 1
    assert by_week["2026-W41"][TransitionKind.REGAINED] == 1
    assert by_week["2026-W43"][TransitionKind.LOST] == 1
    assert all(set(c) == set(TransitionKind) for c in by_week.values())


def test_list_digest_weeks_sin_snapshots_es_lista_vacia():
    assert ListDigestWeeks(FakeDigestReader([], [], {}), _TZ)() == []


def test_list_digest_weeks_pide_el_rango_exacto_de_cada_semana():
    reader = _reader()
    ListDigestWeeks(reader, _TZ)()
    assert reader.range_calls == [week_bounds(w, _TZ) for w in ("2026-W43", "2026-W41", "2026-W40")]


def test_get_weekly_digest_none_si_la_semana_no_tiene_snapshot():
    reader = _reader()
    assert GetWeeklyDigest(reader, _TZ)("2026-W42") is None
    assert reader.range_calls == []  # no consulta transiciones de una semana sin snapshot


def test_get_weekly_digest_devuelve_entradas_ordenadas_con_soluciones():
    digest = GetWeeklyDigest(_reader(), _TZ)("2026-W40")
    assert digest is not None
    assert (digest.week, digest.snapshots) == ("2026-W40", 1)
    assert [(e.kind, e.pl_name) for e in digest.entries] == [
        (TransitionKind.CHANGED, "A b"),
        (TransitionKind.NEW_PLANET, "N b"),
    ]
    changed = digest.entries[0]
    assert changed.old is not None and changed.new is not None
    assert [c.parameter.value for c in changed.parameter_changes] == ["mass"]


def test_get_weekly_digest_semana_con_dos_snapshots():
    digest = GetWeeklyDigest(_reader(), _TZ)("2026-W41")
    assert digest is not None and digest.snapshots == 2
    assert [e.kind for e in digest.entries] == [TransitionKind.REGAINED, TransitionKind.LOST]


def test_get_weekly_digest_domingo_noche_pertenece_a_su_semana_local():
    digest = GetWeeklyDigest(_reader(), _TZ)("2026-W43")
    assert digest is not None and [e.kind for e in digest.entries] == [TransitionKind.LOST]
    assert GetWeeklyDigest(_reader(), _TZ)("2026-W44") is None


@pytest.mark.parametrize("bad", ["basura", "2026-41", "2026-W5", "2027-W53", "2026-W00"])
def test_get_weekly_digest_formato_invalido_es_value_error(bad):
    with pytest.raises(ValueError):
        GetWeeklyDigest(_reader(), _TZ)(bad)
