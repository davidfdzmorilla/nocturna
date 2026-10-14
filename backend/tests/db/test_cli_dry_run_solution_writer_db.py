"""T85.e (D13): la subsección del archivo de `run-night --dry-run` no escribe ni gasta.

Siembra una tensión elegible real (HD 202206 c) y comprueba, con el redactor apagado y
encendido, que el ensayo no cambia ninguna tabla y que ni el SDK, ni `AgentRunner`, ni
`BudgetGuard.authorize`, ni `WriteSolutionTensions` se tocan.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from helpers.solution_tension_smoke import seed_hd202206c_solution_tension
from test_cli_dry_run_no_writes_db import (  # noqa: F401 - fixtures autouse reutilizadas
    ARGS,
    _fast_waits,
    _route,
    _snapshot,
    _use_test_database,
    _via_api,
)

from nocturna import cli
from nocturna.application.agents.runner import AgentRunner
from nocturna.application.budget import BudgetGuard
from nocturna.application.use_cases.write_solution_tensions import WriteSolutionTensions
from nocturna.infrastructure.config import load_pipeline_config
from nocturna.infrastructure.llm import agent_sdk_provider

REAL_TOML = Path(__file__).resolve().parents[3] / "config" / "pipeline.toml"


def _forbid_any_spend(monkeypatch: pytest.MonkeyPatch) -> None:
    def _boom(*_a: object, **_k: object):
        raise AssertionError("el --dry-run no debe gastar ni llamar a un agente")

    monkeypatch.setattr(agent_sdk_provider, "AgentSDKProvider", _boom)
    monkeypatch.setattr(AgentRunner, "run", _boom)
    monkeypatch.setattr(BudgetGuard, "authorize", _boom)
    monkeypatch.setattr(WriteSolutionTensions, "__call__", _boom)


@pytest.mark.parametrize("enabled", [False, True])
def test_dry_run_lista_la_tension_del_archivo_sin_escribir_ni_gastar(
    monkeypatch, db_session_factory, capsys, enabled: bool
) -> None:
    seed_hd202206c_solution_tension(
        db_session_factory, load_pipeline_config(REAL_TOML), run_budget_tokens=26_000
    )
    real = cli.load_pipeline_config

    def _with_switch(*args: object, **kwargs: object):
        config = real(*args, **kwargs)
        st = config.solution_tensions.model_copy(update={"writer_enabled": enabled})
        return config.model_copy(update={"solution_tensions": st})

    monkeypatch.setattr(cli, "load_pipeline_config", _with_switch)
    _route(monkeypatch)
    before = _snapshot(db_session_factory)
    _forbid_any_spend(monkeypatch)

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    section = out.split("Tensiones del archivo pendientes del redactor", 1)[1]
    assert "HD 202206 c (mass)" in section
    assert ("desactivado (writer_enabled = false)" in section) is (not enabled)
    assert _snapshot(db_session_factory) == before


def test_dry_run_con_un_item_del_archivo_descuadrado_imprime_el_error_y_termina_en_0(
    monkeypatch, db_session_factory, capsys
) -> None:
    import sqlalchemy as sa

    seed_hd202206c_solution_tension(
        db_session_factory, load_pipeline_config(REAL_TOML), run_budget_tokens=26_000
    )
    with db_session_factory() as session:
        session.execute(sa.text("update items set external_id = 'no-casa' where source <> 'arxiv'"))
        session.commit()
    _route(monkeypatch)
    before = _snapshot(db_session_factory)
    _forbid_any_spend(monkeypatch)

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    section = out.split("Tensiones del archivo pendientes del redactor", 1)[1]
    assert "error al seleccionar las tensiones del archivo" in section
    assert section.index("error al seleccionar") < section.index("Tensiones entre soluciones:")
    assert _snapshot(db_session_factory) == before
