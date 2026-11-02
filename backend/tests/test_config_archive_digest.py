"""T84.r: `[archive_digest]` de `pipeline.toml` y el montaje desde configuración.

Los casos parten del TOML real y cambian un valor en `tmp_path`; ninguno modifica
`config/pipeline.toml`. Sin Claude. Fichero en español.

Interfaz supuesta (ver el informe de T84.r): `config.archive_digest.writer_enabled`,
`config.archive_digest.since_week`, `cli.select_digest_weeks_from_config(config, *, work,
clock)` y `cli.write_digest_text_from_config(config, *, work, provider, system_prompt, clock)`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from fakes.llm import FakeLLMProvider
from helpers.archive_digest import (
    DIGEST_JSON,
    NOW,
    SINCE_WEEK,
    changed,
    load_reader,
    make_digest_writer_work_factory,
)
from helpers.run_night import Environment, make_policy
from pydantic import ValidationError

from nocturna import cli
from nocturna.application.agents.prompt_loader import WRITER_DIGEST_PROMPT_VERSION
from nocturna.domain.llm import AgentRole
from nocturna.infrastructure.config import load_pipeline_config

pytestmark = pytest.mark.anyio

REAL_PIPELINE_TOML = Path(__file__).resolve().parents[2] / "config" / "pipeline.toml"
_SECTION = re.compile(r"^\[archive_digest\]\n.*?(?=^\[|\Z)", re.DOTALL | re.MULTILINE)


def _toml(tmp_path: Path, *, drop: str | None = None, **values: str) -> Path:
    text = REAL_PIPELINE_TOML.read_text(encoding="utf-8")
    match = _SECTION.search(text)
    assert match, "el TOML real debe tener [archive_digest]"
    section = match.group(0)
    for key, value in values.items():
        section, n = re.subn(rf"^{key} = .*$", f"{key} = {value}", section, flags=re.MULTILINE)
        assert n == 1, key
    if drop is not None:
        section, n = re.subn(rf"^{drop} = .*\n", "", section, flags=re.MULTILINE)
        assert n == 1, drop
    path = tmp_path / "pipeline.toml"
    path.write_text(text.replace(match.group(0), section, 1), encoding="utf-8")
    return path


def test_el_toml_real_trae_el_resumen_apagado_y_desde_la_w40() -> None:
    section = load_pipeline_config(REAL_PIPELINE_TOML).archive_digest
    assert section.writer_enabled is False
    assert section.since_week == "2026-W40"


def test_encendido_carga(tmp_path: Path) -> None:
    config = load_pipeline_config(_toml(tmp_path, writer_enabled="true"))
    assert config.archive_digest.writer_enabled is True


@pytest.mark.parametrize("key", ["writer_enabled", "since_week"])
def test_sin_la_clave_la_configuracion_falla(tmp_path: Path, key: str) -> None:
    with pytest.raises(ValidationError):
        load_pipeline_config(_toml(tmp_path, drop=key))


def test_sin_la_seccion_la_configuracion_falla(tmp_path: Path) -> None:
    text = REAL_PIPELINE_TOML.read_text(encoding="utf-8")
    path = tmp_path / "pipeline.toml"
    path.write_text(_SECTION.sub("", text, count=1), encoding="utf-8")
    with pytest.raises(ValidationError):
        load_pipeline_config(path)


@pytest.mark.parametrize(
    "value",
    ['"2026-W5"', '"2026W40"', '"26-W40"', '"2026-w40"', '""', '"abc"', '"2026-W401"', "40"],
)
def test_since_week_con_formato_invalido_falla(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValidationError):
        load_pipeline_config(_toml(tmp_path, since_week=value))


@pytest.mark.parametrize("value", ['"yes"', '"true"', "1"])
def test_writer_enabled_debe_ser_booleano(tmp_path: Path, value: str) -> None:
    with pytest.raises(ValidationError):
        load_pipeline_config(_toml(tmp_path, writer_enabled=value))


def test_el_interruptor_del_resumen_no_depende_del_de_las_tensiones_del_archivo(
    tmp_path: Path,
) -> None:
    config = load_pipeline_config(_toml(tmp_path, writer_enabled="true"))
    assert config.solution_tensions.writer_enabled is True
    off = load_pipeline_config(_toml(tmp_path, writer_enabled="false"))
    assert off.solution_tensions.writer_enabled is True  # el del archivo sigue como estaba


def test_el_editor_no_cambia_de_version_por_el_resumen(tmp_path: Path) -> None:
    on = load_pipeline_config(_toml(tmp_path, writer_enabled="true"))
    off = load_pipeline_config(_toml(tmp_path, writer_enabled="false"))
    assert cli.editor_prompt_version(on) == cli.editor_prompt_version(off)


# --- montaje desde la configuración -------------------------------------------------


async def test_write_digest_text_from_config_usa_modelo_prompt_y_estimacion_del_toml() -> None:
    config = load_pipeline_config(REAL_PIPELINE_TOML)
    env = Environment(
        items=[],
        policy=make_policy(writer_reserve_tokens=50_000, max_writer_calls_per_night=5),
        now=NOW,
        with_digest=True,
    )
    load_reader(env.digest_reader, "2026-W42", changed("A b", "2026-W42"))
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, json=DIGEST_JSON, tokens_in=700, tokens_out=300)
    seen: list[tuple[AgentRole, int]] = []
    real = env.guard.authorize
    env.guard.authorize = lambda role, est: (seen.append((role, est)), real(role, est))[1]  # type: ignore[method-assign]

    select = cli.select_digest_weeks_from_config(
        config,
        work=make_digest_writer_work_factory(env.digest_reader, env.digest_texts),
        clock=env.clock,
    )
    (candidate,) = select.pending().candidates
    writer = cli.write_digest_text_from_config(
        config, work=env.work, provider=fake, system_prompt="prompt", clock=env.clock
    )

    result = await writer(candidate)

    assert result.text is not None
    assert seen == [(AgentRole.WRITER, config.budget.writer_estimated_tokens)]
    (call,) = env.agent_calls.calls
    assert call.model == config.models.writer
    assert call.prompt_version == WRITER_DIGEST_PROMPT_VERSION == "writer-digest-v1"
    assert fake.calls[0].model == config.models.writer
    assert candidate.digest.week >= config.archive_digest.since_week >= SINCE_WEEK
