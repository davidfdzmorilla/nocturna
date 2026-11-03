"""T100: el Reader descarta (en Python) la medida que es una masa mínima `m sin i`,
sin reintentar el ítem. Calcado de 2610.08922 (TOI-2211 b y c), con el
`FakeLLMProvider`: ninguna llamada real a Claude."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fakes.llm import FakeLLMProvider
from test_read_item_measures import (
    _make_environment,
    _make_item,
    _make_read_item,
    _valid_v3_json,
)

from nocturna.application.agents.reader_measurements import DiscardReason, filter_measurements
from nocturna.application.use_cases import read_item as read_item_module
from nocturna.application.use_cases.read_item import ReadOutcome
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio

_PAPER = next(
    p
    for p in json.loads(
        (Path(__file__).parent / "fixtures" / "t100" / "abstracts.json").read_text(encoding="utf-8")
    )
    if p["external_id"] == "2610.08922"
)


def test_filter_measurements_descarta_solo_la_masa_minima() -> None:
    result = filter_measurements(
        _PAPER["measurements"], abstract=_PAPER["abstract"], title=_PAPER["title"]
    )

    kept = {(m.planet_name, m.parameter.value) for m in result.kept}
    assert ("TOI-2211 b", "mass") in kept and ("TOI-2211 b", "radius") in kept
    assert ("TOI-2211 c", "mass") not in kept
    (discard,) = result.discarded
    assert discard.reason is DiscardReason.MINIMUM_MASS
    assert discard.reason.value == "minimum_mass"
    assert discard.planet_name == "TOI-2211 c"


async def test_read_item_guarda_b_descarta_c_y_no_reintenta(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(read_item_module._logger, "disabled", False)
    item = _make_item(
        external_id="2610.08922",
        title=_PAPER["title"],
        abstract=_PAPER["abstract"],
        categories=["astro-ph.EP"],
    )
    env = _make_environment(item=item)
    fake = FakeLLMProvider()
    fake.respond(
        AgentRole.READER,
        json=_valid_v3_json(objects=["TOI-2211"], measurements=_PAPER["measurements"]),
        tokens_in=1200,
        tokens_out=300,
    )
    read_item = _make_read_item(work=env.work, provider=fake)

    with caplog.at_level(logging.WARNING, logger="nocturna.application.use_cases.read_item"):
        result = await read_item(item)

    assert result.outcome is ReadOutcome.READ
    assert result.attempts == 1
    assert len(fake.calls) == 1, "una sola llamada: la masa mínima no reintenta el ítem"
    saved = {(m.planet_name, m.parameter.value) for m in result.reading.measurements}
    assert saved == {("TOI-2211 b", "mass"), ("TOI-2211 b", "radius")}
    discards = [r for r in caplog.records if r.getMessage() == "reader.measurement_discarded"]
    assert [r.reason for r in discards] == ["minimum_mass"]
