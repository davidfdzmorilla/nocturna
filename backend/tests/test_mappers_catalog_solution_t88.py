"""JSON de `CatalogSolution` (T88): cinco claves opcionales, `schema_version` 1."""

from __future__ import annotations

from datetime import date

import pytest
from helpers.exoplanet import make_solution

from nocturna.infrastructure.db.mappers import (
    _catalog_tension_from_json,
    _catalog_tension_to_json,
    _solution_from_dict,
    _solution_to_dict,
)

_NEW_KEYS = ("solution_key", "soltype", "pl_pubdate", "releasedate", "ttv_flag")


def _full_solution():
    return make_solution(
        0.041,
        0.017,
        0.017,
        solution_key="a" * 64,
        soltype="Published Confirmed",
        pl_pubdate="2026-01",
        releasedate=date(2026, 2, 3),
        ttv_flag=True,
        is_default=True,
    )


def test_round_trip_conserva_los_cinco_campos():
    sol = _full_solution()
    payload = _solution_to_dict(sol)
    assert payload["releasedate"] == "2026-02-03"
    assert _solution_from_dict(payload) == sol


def test_round_trip_con_los_cinco_campos_a_none():
    sol = make_solution(
        0.041, 0.017, 0.017, soltype=None, solution_key=None, pl_pubdate=None, releasedate=None
    )
    payload = _solution_to_dict(sol)
    assert all(payload[k] is None for k in _NEW_KEYS)
    assert _solution_from_dict(payload) == sol


def test_json_sin_las_claves_nuevas_se_lee_con_none():
    payload = _solution_to_dict(_full_solution())
    for key in _NEW_KEYS:
        del payload[key]
    sol = _solution_from_dict(payload)
    assert all(getattr(sol, k) is None for k in _NEW_KEYS)
    assert sol.is_default is True


def test_catalog_tension_sigue_en_schema_version_1_y_lee_json_antiguo():
    from helpers.exoplanet import catalog_tension_v1298_b

    tension = catalog_tension_v1298_b()
    payload = _catalog_tension_to_json(tension)
    assert payload is not None and payload["schema_version"] == 1
    assert _catalog_tension_from_json(payload) == tension
    for comparison in payload["comparisons"]:
        for key in _NEW_KEYS:
            del comparison["prior"][key]
    legacy = _catalog_tension_from_json(payload)
    assert legacy is not None
    assert all(c.prior.solution_key is None for c in legacy.comparisons)


@pytest.mark.parametrize("version", [None, 0, 2, "1", True])
def test_catalog_tension_version_desconocida_sigue_fallando(version):
    from helpers.exoplanet import catalog_tension_v1298_b

    payload = _catalog_tension_to_json(catalog_tension_v1298_b())
    assert payload is not None
    payload["schema_version"] = version
    with pytest.raises(ValueError, match="schema_version"):
        _catalog_tension_from_json(payload)
