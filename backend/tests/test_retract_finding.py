"""T100: `RetractFinding`. En memoria; sin LLM ni `BudgetGuard`."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest
from fakes.clock import FakeClock
from fakes.work import InMemoryFindingRepository

from nocturna.application.use_cases.retract_finding import RetractFinding
from nocturna.domain.entities import Finding, FindingType
from nocturna.domain.errors import FindingNotFound, InvalidTransition, InvariantViolation

NOW = datetime(2026, 11, 3, 10, 0, tzinfo=UTC)
PUBLISHED = datetime(2026, 10, 2, 8, 0, tzinfo=UTC)


def _finding(*, published: bool = True) -> Finding:
    finding = Finding(
        item_id=uuid4(),
        run_id=uuid4(),
        type=FindingType.PAPER_EXPLAINED,
        title="Titular",
        level_curious="c",
        level_amateur="a",
        level_technical="t",
    )
    if published:
        finding.publish(confidence=0.8, at=PUBLISHED)
    return finding


def _use_case(repo: InMemoryFindingRepository) -> RetractFinding:
    return RetractFinding(findings=repo, clock=FakeClock(NOW))


def test_retira_un_publicado_y_lo_guarda() -> None:
    repo = InMemoryFindingRepository()
    finding = _finding()
    repo.add(finding)

    result = _use_case(repo)(finding.id, note="Era una masa mínima.", dry_run=False)

    stored = repo.get(finding.id)
    assert stored is not None
    assert result.retracted_at == NOW and stored.retracted_at == NOW
    assert stored.retraction_note == "Era una masa mínima."
    assert stored.published_at == PUBLISHED


def test_dry_run_valida_pero_no_escribe() -> None:
    repo = InMemoryFindingRepository()
    finding = _finding()
    repo.add(finding)

    result = _use_case(repo)(finding.id, note="nota", dry_run=True)

    assert result.retracted_at == NOW, "informa de lo que haría"
    stored = repo.get(finding.id)
    assert stored is not None and stored.retracted_at is None and stored.retraction_note is None


def test_un_id_inexistente_falla_con_error_claro() -> None:
    repo = InMemoryFindingRepository()
    missing = uuid4()

    with pytest.raises(FindingNotFound, match=str(missing)):
        _use_case(repo)(missing, note="nota", dry_run=False)


def test_un_no_publicado_no_se_retira() -> None:
    repo = InMemoryFindingRepository()
    finding = _finding(published=False)
    repo.add(finding)

    with pytest.raises(InvalidTransition):
        _use_case(repo)(finding.id, note="nota", dry_run=False)
    with pytest.raises(InvalidTransition):
        _use_case(repo)(finding.id, note="nota", dry_run=True)

    stored = repo.get(finding.id)
    assert stored is not None and stored.retracted_at is None


def test_ya_retirado_no_se_retira_otra_vez() -> None:
    repo = InMemoryFindingRepository()
    finding = _finding()
    repo.add(finding)
    use_case = _use_case(repo)
    use_case(finding.id, note="primera", dry_run=False)

    with pytest.raises(InvalidTransition):
        use_case(finding.id, note="segunda", dry_run=False)

    stored = repo.get(finding.id)
    assert stored is not None and stored.retraction_note == "primera"


def test_la_nota_vacia_se_rechaza() -> None:
    repo = InMemoryFindingRepository()
    finding = _finding()
    repo.add(finding)

    with pytest.raises(InvariantViolation, match="retraction_note"):
        _use_case(repo)(finding.id, note="  ", dry_run=False)
