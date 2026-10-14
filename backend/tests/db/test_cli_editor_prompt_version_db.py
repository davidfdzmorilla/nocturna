"""T85.e (D7): `run-night` y `run-item` eligen `editor-v3` / `editor-v4` según `writer_enabled`.

Contra la base de test, con `FakeLLMProvider` en lugar del SDK: ninguna llamada a Claude.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import pytest
from factories import make_item
from fakes.clock import FakeClock
from fakes.llm import FakeLLMProvider
from helpers.run_night import approve_items_in_editor, db_finding_ids_by_item
from sqlalchemy import select

from nocturna import cli
from nocturna.application.agents.prompt_loader import load_prompt
from nocturna.cli import main
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.db.models import AgentCallRow
from nocturna.infrastructure.db.repositories import SqlAlchemyItemRepository
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider

FIXTURES_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "arxiv"
_WITHIN_WINDOW = datetime(2026, 1, 15, 2, 0, tzinfo=ZoneInfo("Europe/Madrid"))
READING = {
    "summary": "Resumen.",
    "objects": ["NGC 1234"],
    "claims": ["Una afirmación."],
    "interest_score": 5,
    "measurements": [],
}
POPULARIZER = {"title": "t", "level_curious": "c", "level_amateur": "a", "level_technical": "t"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, test_database_url: str) -> None:
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", test_database_url)
    monkeypatch.setattr(cli, "SystemClock", lambda *a, **k: FakeClock(_WITHIN_WINDOW))
    real = httpx.AsyncClient

    def _client(*args: object, **kwargs: object) -> httpx.AsyncClient:
        kwargs["transport"] = httpx.MockTransport(
            lambda request: httpx.Response(
                200, content=(FIXTURES_DIR / "oai" / "list_records_empty.xml").read_bytes()
            )
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", _client)


def _config(monkeypatch: pytest.MonkeyPatch, *, writer_enabled: bool) -> None:
    config = load_pipeline_config()
    config = config.model_copy(
        update={
            "solution_tensions": config.solution_tensions.model_copy(
                update={"writer_enabled": writer_enabled}
            )
        }
    )
    monkeypatch.setattr(cli, "load_pipeline_config", lambda *a, **k: config)


def _run(db_session_factory, fake: FakeLLMProvider, argv: list[str]) -> UUID:
    item = make_item()
    with unit_of_work(db_session_factory) as session:
        SqlAlchemyItemRepository(session).add_many([item])
    fake.respond(AgentRole.READER, json=READING, tokens_in=1000, tokens_out=200)
    fake.respond(AgentRole.POPULARIZER, json=POPULARIZER, tokens_in=800, tokens_out=150)
    approve_items_in_editor(
        fake,
        finding_ids_by_item=db_finding_ids_by_item(db_session_factory),
        item_ids=[item.id],
        confidence=0.8,
        reason="motivo",
        tokens_in=1200,
        tokens_out=100,
    )
    argv = [a.replace("{item}", str(item.id)) for a in argv]
    assert main(argv) == 0
    return item.id


def _editor_prompt_version(db_session_factory) -> str | None:
    session = db_session_factory()
    try:
        rows = session.execute(
            select(AgentCallRow).where(AgentCallRow.agent == AgentRole.EDITOR)
        ).scalars()
        (row,) = list(rows)
        return row.prompt_version
    finally:
        session.close()


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeLLMProvider:
    provider = FakeLLMProvider()
    monkeypatch.setattr(agent_sdk_provider, "AgentSDKProvider", lambda: provider)
    return provider


@pytest.mark.parametrize(
    ("argv", "writer_enabled", "expected"),
    [
        (["run-night"], False, "editor-v3"),
        (["run-night"], True, "editor-v4"),
        (["run-item", "{item}"], False, "editor-v3"),
        (["run-item", "{item}"], True, "editor-v4"),
    ],
)
def test_la_version_del_editor_sigue_a_writer_enabled(
    db_session_factory, fake, monkeypatch, argv, writer_enabled, expected
) -> None:
    _config(monkeypatch, writer_enabled=writer_enabled)

    _run(db_session_factory, fake, argv)

    assert _editor_prompt_version(db_session_factory) == expected
    (editor_call,) = [c for c in fake.calls if c.role is AgentRole.EDITOR]
    assert editor_call.system_prompt == load_prompt(expected)


def test_con_el_interruptor_apagado_run_night_no_llama_al_redactor_ni_carga_su_prompt(
    db_session_factory, fake, monkeypatch
) -> None:
    _config(monkeypatch, writer_enabled=False)
    loaded: list[str] = []
    real_load = cli.load_prompt

    def _spy(name: str) -> str:
        loaded.append(name)
        return real_load(name)

    monkeypatch.setattr(cli, "load_prompt", _spy)

    _run(db_session_factory, fake, ["run-night"])

    assert "writer-archive-v1" not in loaded
    assert [c for c in fake.calls if c.role is AgentRole.WRITER] == []
