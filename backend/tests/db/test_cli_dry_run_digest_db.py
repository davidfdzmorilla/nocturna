"""T84.r: la subsección «Resumen semanal» de `run-night --dry-run` no escribe ni gasta.

Siembra el histórico del archivo (W40) y, con el redactor apagado y encendido, comprueba que
el ensayo lista la semana, no cambia ninguna tabla y no toca SDK, `AgentRunner`,
`BudgetGuard.authorize` ni `WriteDigestText`. Fichero en español.
"""

from __future__ import annotations

import pytest
from factories import aware, make_run
from helpers.archive_digest import digest_text
from test_cli_dry_run_no_writes_db import (  # noqa: F401 - fixtures autouse reutilizadas
    ARGS,
    _fast_waits,
    _route,
    _snapshot,
    _use_test_database,
    _via_api,
)
from test_cli_run_night_digest_db import _seed_week_40

from nocturna import cli
from nocturna.application.agents.runner import AgentRunner
from nocturna.application.budget import BudgetGuard
from nocturna.application.use_cases.write_archive_digest import WriteDigestText
from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.db.repositories import (
    SqlAlchemyArchiveDigestTextRepository,
    SqlAlchemyRunRepository,
)
from nocturna.infrastructure.db.session import unit_of_work
from nocturna.infrastructure.llm import agent_sdk_provider

HEADER = "Resumen semanal pendiente del redactor"


def _forbid_any_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object):
        raise AssertionError("el --dry-run no debe gastar ni llamar a un agente")

    monkeypatch.setattr(agent_sdk_provider, "AgentSDKProvider", _boom)
    monkeypatch.setattr(AgentRunner, "run", _boom)
    monkeypatch.setattr(BudgetGuard, "authorize", _boom)
    monkeypatch.setattr(WriteDigestText, "__call__", _boom)


def _with_switch(monkeypatch: pytest.MonkeyPatch, enabled: bool) -> None:
    real = cli.load_pipeline_config

    def _patched(*args: object, **kwargs: object):
        config = real(*args, **kwargs)
        digest = config.archive_digest.model_copy(
            update={"writer_enabled": enabled, "since_week": "2026-W40"}
        )
        return config.model_copy(update={"archive_digest": digest})

    monkeypatch.setattr(cli, "load_pipeline_config", _patched)


@pytest.mark.parametrize("enabled", [False, True])
def test_dry_run_lista_la_semana_sin_escribir_ni_gastar(
    monkeypatch, db_session_factory, capsys, enabled: bool
) -> None:
    _seed_week_40(db_session_factory)
    _with_switch(monkeypatch, enabled)
    _route(monkeypatch)
    before = _snapshot(db_session_factory)
    _forbid_any_spend(monkeypatch)

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    section = out.split(HEADER, 1)[1]
    assert "elegible · 2026-W40" in section
    assert ("desactivado (writer_enabled = false)" in section) is (not enabled)
    assert _snapshot(db_session_factory) == before
    assert "[dry-run: no se escribió nada en la base]" in out


def test_dry_run_con_la_semana_ya_fallida_la_excluye_con_su_motivo(
    monkeypatch, db_session_factory, capsys
) -> None:
    _seed_week_40(db_session_factory)
    with unit_of_work(db_session_factory) as session:
        run = make_run(status=RunStatus.COMPLETED, started_at=aware(0), finished_at=aware(5))
        SqlAlchemyRunRepository(session).add(run)
        SqlAlchemyArchiveDigestTextRepository(session).add(
            digest_text("2026-W40", run.id, status="failed")
        )
    _with_switch(monkeypatch, True)
    _route(monkeypatch)
    before = _snapshot(db_session_factory)
    _forbid_any_spend(monkeypatch)

    code = cli.main(ARGS)

    section = capsys.readouterr().out.split(HEADER, 1)[1]
    assert code == 0
    assert "excluida · 2026-W40 · writer_failed" in section
    assert "elegible · 2026-W40" not in section
    assert _snapshot(db_session_factory) == before


def test_dry_run_sin_historico_del_archivo_imprime_la_seccion_vacia(
    monkeypatch, db_session_factory, capsys
) -> None:
    _with_switch(monkeypatch, True)
    _route(monkeypatch)
    before = _snapshot(db_session_factory)
    _forbid_any_spend(monkeypatch)

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 0 and "elegibles=0 excluidas=0" in out.split(HEADER, 1)[1]
    assert _snapshot(db_session_factory) == before
