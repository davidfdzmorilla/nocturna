"""T84.r: la tercera pasada (resumen semanal) de `_phase_writer` de `RunNight`.

En memoria, sin Claude. Fichero en español. Supuestos de interfaz (ver el informe de
T84.r): `RunNight(select_digest_weeks=..., write_digest_text=...)`, los dos o ninguno;
fase de log `writer_digest`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import pytest
from fakes.llm import FakeLLMProvider
from helpers.archive_digest import (
    DIGEST_JSON,
    NOW,
    changed,
    digest_text,
    load_reader,
    make_digest_selector,
    make_digest_writer,
    new_planet,
)
from helpers.run_night import (
    Environment,
    approve_items_in_editor,
    in_memory_finding_ids_by_item,
    make_item,
    make_policy,
    make_run_night,
    make_solution_selector,
    make_solution_writer,
)
from helpers.solution_tension_writer import make_archive_item, make_solution_tension
from helpers.tension_writer import v1298_evaluation

from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.application.use_cases.run_night import RunNight
from nocturna.application.use_cases.run_night import _logger as run_night_logger
from nocturna.application.use_cases.write_archive_digest import SelectDigestWeeks
from nocturna.domain.archive_digest import DigestTextStatus
from nocturna.domain.entities import (
    AgentCall,
    AgentCallStatus,
    Finding,
    FindingType,
    ItemStatus,
    RunStatus,
)
from nocturna.domain.errors import LLMRateLimited
from nocturna.domain.llm import AgentRequest, AgentResult, AgentRole

pytestmark = pytest.mark.anyio

TENSION_JSON = {
    "title": "HD 114082 b: diferencia calculada entre dos soluciones del archivo",
    "level_curious": "c",
    "level_amateur": "a",
    "level_technical": "t",
}
ARXIV_JSON = {**TENSION_JSON, "title": "V1298 Tau b: una discrepancia por revisar"}
LATE = datetime(2026, 11, 10, 5, 0, 0, tzinfo=UTC)  # pasado el hard_stop de 04:45 local


def _ingest() -> IngestResult:
    return IngestResult(fetched=0, new=0, duplicates=0, skipped=0, truncated=False, items=[])


def _env(
    *, arxiv: int = 0, archive: int = 0, weeks: int = 1, paper: bool = False, now=NOW, **policy
) -> Environment:
    """`weeks` semanas cerradas con transiciones (la más reciente W44, luego W42, W40)."""
    defaults = {"writer_reserve_tokens": 40_000, "max_writer_calls_per_night": 5}
    defaults.update(policy)
    arxiv_items = [make_item(status=ItemStatus.READ) for _ in range(arxiv)]
    keys = [chr(ord("c") + i) * 64 for i in range(archive)]
    archive_items = [make_archive_item(external_id=k) for k in keys]
    env = Environment(
        items=[*arxiv_items, *archive_items],
        policy=make_policy(**defaults),
        now=now,
        with_digest=True,
    )
    for item in arxiv_items:
        env.evaluations.add(v1298_evaluation(item.id))
    for i in range(archive):
        env.solution_tensions.add(
            make_solution_tension(archive_items[i].id, new_key=keys[i], sigma=100.0 - i * 10)
        )
    for week, part in zip(
        ("2026-W44", "2026-W42", "2026-W40"),
        (new_planet("M b", "2026-W44"), changed("A b", "2026-W42"), changed("B b", "2026-W40")),
        strict=False,
    ):
        if weeks > ("2026-W44", "2026-W42", "2026-W40").index(week):
            load_reader(env.digest_reader, week, part)
    if paper:
        paper_item = make_item(status=ItemStatus.READ)
        env.items.add_many([paper_item])
        env.findings.add(
            Finding(
                item_id=paper_item.id,
                run_id=env.run.id,
                type=FindingType.PAPER_EXPLAINED,
                title="Un paper",
                level_curious="c",
                level_amateur="a",
                level_technical="t",
            )
        )
    return env


def _night(env, provider, *, digest: bool = True, solution: bool = True, **kw) -> RunNight:
    extra: dict = {}
    if solution and env.solution_tensions.all():
        extra = {
            "select_solution_tensions": make_solution_selector(env),
            "write_solution_tensions": make_solution_writer(env, provider),
        }
    if digest:
        extra["select_digest_weeks"] = make_digest_selector(env)
        extra["write_digest_text"] = make_digest_writer(env, provider)
    return make_run_night(env=env, provider=provider, ingest_result=_ingest(), **extra, **kw)


def _writes(fake):
    return [c for c in fake.calls if c.role is AgentRole.WRITER]


def _digest_writes(fake):
    return [c for c in _writes(fake) if "<weekly_digest>" in c.prompt]


def _roles(fake):
    return [c.role for c in fake.calls]


def _ok_digest(fake, n: int = 1, tokens=(700, 300)) -> None:
    for _ in range(n):
        fake.respond(AgentRole.WRITER, json=DIGEST_JSON, tokens_in=tokens[0], tokens_out=tokens[1])


def _ok_tension(fake, n: int = 1, *, data=TENSION_JSON, tokens=(700, 300)) -> None:
    for _ in range(n):
        fake.respond(AgentRole.WRITER, json=data, tokens_in=tokens[0], tokens_out=tokens[1])


def _approve(fake, env) -> None:
    approve_items_in_editor(
        fake,
        finding_ids_by_item=in_memory_finding_ids_by_item(env),
        item_ids=[i.id for i in env.items._items.values()],
    )


def _spy_authorize(env) -> list[AgentRole]:
    seen: list[AgentRole] = []
    real = env.guard.authorize

    def _spy(*args, **kwargs):
        seen.append(args[0] if args else kwargs.get("role"))
        return real(*args, **kwargs)

    env.guard.authorize = _spy  # type: ignore[method-assign]
    return seen


class _ClockJumpProvider(FakeLLMProvider):
    """Tras `after` llamadas del redactor, la hora pasa de `hard_stop`."""

    def __init__(self, env, *, after: int = 1) -> None:
        super().__init__()
        self._env = env
        self._after = after
        self._seen = 0

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        result = await super().run_agent(request)
        if request.role is AgentRole.WRITER:
            self._seen += 1
            if self._seen >= self._after:
                self._env.clock.set(LATE)
        return result


class _SlowProvider:
    def __init__(self, *, usage=(400, 25)) -> None:
        self.calls: list[AgentRequest] = []
        self.started = asyncio.Event()
        self._usage = usage

    async def run_agent(self, request: AgentRequest) -> AgentResult:
        self.calls.append(request)
        self.started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError as exc:
            exc.tokens_in, exc.tokens_out = self._usage  # type: ignore[attr-defined]
            raise
        raise AssertionError("debe cancelarse antes")


def _phase_logs(caplog, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == event or r.msg == event]


# --- interruptor y construcción -----------------------------------------------


async def test_interruptor_apagado_cero_llamadas_y_ni_authorize() -> None:
    env = _env()
    fake = FakeLLMProvider()
    seen = _spy_authorize(env)

    result = await _night(env, fake, digest=False)()

    assert fake.calls == [] and env.agent_calls.calls == [] and seen == []
    assert env.digest_texts.all() == {}
    assert result.status is RunStatus.COMPLETED


def test_select_y_write_del_resumen_van_los_dos_o_ninguno() -> None:
    env = _env()
    fake = FakeLLMProvider()
    with pytest.raises(ValueError):
        make_run_night(
            env=env,
            provider=fake,
            ingest_result=_ingest(),
            select_digest_weeks=make_digest_selector(env),
        )
    with pytest.raises(ValueError):
        make_run_night(
            env=env,
            provider=fake,
            ingest_result=_ingest(),
            write_digest_text=make_digest_writer(env, fake),
        )


# --- camino feliz ---------------------------------------------------------------


async def test_sin_tensiones_y_una_semana_pendiente_hace_una_llamada_y_guarda_el_texto() -> None:
    env = _env(weeks=1)
    fake = FakeLLMProvider()
    _ok_digest(fake)

    result = await _night(env, fake)()

    assert [c.role for c in fake.calls] == [AgentRole.WRITER]
    assert "<weekly_digest>" in fake.calls[0].prompt
    stored = env.digest_texts.get("2026-W44")
    assert stored is not None and stored.status is DigestTextStatus.WRITTEN
    (call,) = env.agent_calls.calls
    assert call.agent is AgentRole.WRITER and call.item_id is None
    assert call.prompt_version == "writer-digest-v1"
    assert result.status is RunStatus.COMPLETED and result.tokens_used == 1000
    assert AgentRole.EDITOR not in _roles(fake)  # el resumen no es un candidato del Editor


async def test_el_resumen_no_cuenta_como_hallazgo_ni_cambia_ningun_item() -> None:
    env = _env(weeks=1)
    fake = FakeLLMProvider()
    _ok_digest(fake)

    result = await _night(env, fake)()

    assert env.findings._findings == {} and result.findings_published == 0


async def test_orden_arxiv_archivo_resumen_y_despues_el_editor() -> None:
    env = _env(arxiv=1, archive=1, weeks=1)
    fake = FakeLLMProvider()
    _ok_tension(fake, data=ARXIV_JSON)
    _ok_tension(fake)
    _ok_digest(fake)
    _approve(fake, env)

    result = await _night(env, fake)()

    writes = _writes(fake)
    assert "<tension>" in writes[0].prompt
    assert "<solution_tension>" in writes[1].prompt
    assert "<weekly_digest>" in writes[2].prompt
    assert _roles(fake)[-1] is AgentRole.EDITOR
    assert result.status is RunStatus.COMPLETED


async def test_dos_semanas_pendientes_la_mas_reciente_primero() -> None:
    env = _env(weeks=2)
    fake = FakeLLMProvider()
    _ok_digest(fake, 2)

    await _night(env, fake)()

    assert [c.prompt.count("2026-W44") > 0 for c in _digest_writes(fake)] == [True, False]
    assert set(env.digest_texts.all()) == {"2026-W44", "2026-W42"}


async def test_semana_vacia_abierta_o_ya_redactada_cero_llamadas() -> None:
    env = _env(weeks=0)
    load_reader(env.digest_reader, "2026-W43")  # cerrada sin transiciones
    load_reader(env.digest_reader, "2026-W46", new_planet("O b", "2026-W46"))  # abierta
    load_reader(env.digest_reader, "2026-W42", changed("A b", "2026-W42"))
    env.digest_texts.add(digest_text("2026-W42", env.run.id))  # ya redactada
    fake = FakeLLMProvider()
    seen = _spy_authorize(env)

    result = await _night(env, fake)()

    assert fake.calls == [] and seen == [] and env.agent_calls.calls == []
    assert result.status is RunStatus.COMPLETED


async def test_una_segunda_noche_no_vuelve_a_redactar_la_semana() -> None:
    env = _env(weeks=1)
    fake = FakeLLMProvider()
    _ok_digest(fake)
    await _night(env, fake)()
    again = FakeLLMProvider()

    await _night(env, again)()

    assert again.calls == []


async def test_json_invalido_deja_la_fila_failed_y_la_noche_sigue_completa() -> None:
    env = _env(weeks=1)
    fake = FakeLLMProvider()
    for _ in range(2):
        fake.respond(AgentRole.WRITER, raw="no es json", tokens_in=100, tokens_out=10)

    result = await _night(env, fake)()

    assert len(_writes(fake)) == 2 and result.status is RunStatus.COMPLETED
    assert env.digest_texts.get("2026-W44").status is DigestTextStatus.FAILED


# --- tope y reserva compartidos con los redactores de tensiones -----------------


async def test_tope_agotado_por_las_tensiones_no_llama_no_degrada_y_el_editor_corre(
    caplog, monkeypatch
) -> None:
    monkeypatch.setattr(run_night_logger, "disabled", False)
    env = _env(arxiv=1, archive=1, weeks=1, max_writer_calls_per_night=2)
    fake = FakeLLMProvider()
    _ok_tension(fake, data=ARXIV_JSON)
    _ok_tension(fake)
    _approve(fake, env)

    with caplog.at_level(logging.INFO):
        result = await _night(env, fake)()

    assert len(_writes(fake)) == 2 and _digest_writes(fake) == []
    assert env.digest_texts.all() == {}
    assert AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.COMPLETED
    ends = {
        r.phase: r for r in _phase_logs(caplog, "night.phase_end") if r.phase.startswith("writer")
    }
    assert ends["writer_digest"].stop_reason == "call_limit_reached"


async def test_el_resumen_comparte_el_tope_una_tension_mas_una_semana_y_la_segunda_es_call_limit(
    caplog, monkeypatch
) -> None:
    monkeypatch.setattr(run_night_logger, "disabled", False)
    env = _env(archive=1, weeks=2, max_writer_calls_per_night=2)
    fake = FakeLLMProvider()
    _ok_tension(fake)
    _ok_digest(fake, 2)  # una de más: si el tope fallara, se consumiría
    _approve(fake, env)

    with caplog.at_level(logging.INFO):
        result = await _night(env, fake)()

    assert len(_writes(fake)) == 2 and len(_digest_writes(fake)) == 1
    assert set(env.digest_texts.all()) == {"2026-W44"}
    ends = {r.phase: r for r in _phase_logs(caplog, "night.phase_end")}
    assert ends["writer_digest"].stop_reason == "call_limit_reached"
    assert ends["writer_digest"].ok == 1 and ends["writer_digest"].processed == 2
    assert result.status is RunStatus.COMPLETED


async def test_invalido_y_reintento_denegado_por_el_tope_guarda_failed_sin_degradar_la_noche(
    caplog, monkeypatch
) -> None:
    monkeypatch.setattr(run_night_logger, "disabled", False)
    env = _env(archive=1, weeks=1, max_writer_calls_per_night=2)
    fake = FakeLLMProvider()
    _ok_tension(fake)
    fake.respond(AgentRole.WRITER, raw="no es json", tokens_in=100, tokens_out=10)
    _approve(fake, env)

    with caplog.at_level(logging.INFO):
        result = await _night(env, fake)()

    assert len(_writes(fake)) == 2 and len(_digest_writes(fake)) == 1
    stored = env.digest_texts.get("2026-W44")
    assert stored is not None and stored.status is DigestTextStatus.FAILED
    assert stored.run_id == env.run.id and stored.prompt_version == "writer-digest-v1"
    assert AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.COMPLETED
    ends = {r.phase: r for r in _phase_logs(caplog, "night.phase_end")}
    assert ends["writer_digest"].stop_reason == "call_limit_reached"

    again = FakeLLMProvider()
    await _night(env, again)()
    assert _digest_writes(again) == []


async def test_budget_exhausted_en_el_resumen_es_partial_y_el_editor_conserva_su_reserva() -> None:
    env = _env(weeks=1, paper=True)
    env.agent_calls.add(
        AgentCall(
            run_id=env.run.id,
            item_id=None,
            agent=AgentRole.READER,
            model="m",
            tokens_in=60_000,
            tokens_out=30_000,
            duration_ms=1,
            status=AgentCallStatus.OK,
        )
    )
    fake = FakeLLMProvider()
    _approve(fake, env)

    result = await _night(env, fake)()

    assert _digest_writes(fake) == [] and env.digest_texts.all() == {}
    assert AgentRole.EDITOR in _roles(fake)  # la reserva del Editor sigue intacta
    assert result.status is RunStatus.PARTIAL and "end=writer_budget_exhausted" in result.notes


async def test_budget_exhausted_tras_una_llamada_del_resumen_corta_el_resto() -> None:
    env = _env(weeks=2, paper=True)
    fake = FakeLLMProvider()
    _ok_digest(fake, 2, tokens=(60_000, 29_800))
    _approve(fake, env)

    result = await _night(env, fake)()

    assert len(_digest_writes(fake)) == 1 and set(env.digest_texts.all()) == {"2026-W44"}
    assert AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.PARTIAL and "end=writer_budget_exhausted" in result.notes


# --- cortes de las pasadas anteriores -------------------------------------------


async def test_rate_limited_en_arxiv_corta_el_resumen_y_salta_el_editor() -> None:
    env = _env(arxiv=1, weeks=1)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.WRITER, error=LLMRateLimited("límite alcanzado"))
    _approve(fake, env)

    result = await _night(env, fake)()

    assert _roles(fake) == [AgentRole.WRITER] and _digest_writes(fake) == []
    assert result.status is RunStatus.PARTIAL


async def test_fallo_al_seleccionar_arxiv_corta_el_resumen(monkeypatch) -> None:
    env = _env(arxiv=1, weeks=1)
    fake = FakeLLMProvider()
    _approve(fake, env)
    night = _night(env, fake)

    def _boom():
        raise RuntimeError("base caída")

    monkeypatch.setattr(night._select_tensions, "pending", _boom)

    result = await night()

    assert _writes(fake) == [] and env.digest_texts.all() == {}
    assert result.status is RunStatus.PARTIAL and "writer_error" in result.notes


async def test_rate_limited_en_el_archivo_corta_el_resumen_y_salta_el_editor() -> None:
    env = _env(archive=2, weeks=1)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.WRITER, error=LLMRateLimited("límite alcanzado"))
    _approve(fake, env)

    result = await _night(env, fake)()

    assert _roles(fake) == [AgentRole.WRITER] and _digest_writes(fake) == []
    assert result.status is RunStatus.PARTIAL


async def test_call_limit_en_la_pasada_del_archivo_corta_el_resumen() -> None:
    env = _env(archive=2, weeks=1, max_writer_calls_per_night=1)
    fake = FakeLLMProvider()
    _ok_tension(fake)
    _ok_digest(fake)
    _approve(fake, env)

    result = await _night(env, fake)()

    assert len(_writes(fake)) == 1 and _digest_writes(fake) == []
    assert AgentRole.EDITOR in _roles(fake) and result.status is RunStatus.COMPLETED


async def test_parada_de_la_pasada_del_archivo_ni_siquiera_selecciona_el_resumen(
    monkeypatch,
) -> None:
    env = _env(archive=2, weeks=1, max_writer_calls_per_night=1)
    fake = FakeLLMProvider()
    _ok_tension(fake)
    _ok_digest(fake)
    _approve(fake, env)
    selected: list[bool] = []
    real = SelectDigestWeeks.pending

    def _spy(self):
        selected.append(True)
        return real(self)

    monkeypatch.setattr(SelectDigestWeeks, "pending", _spy)

    await _night(env, fake)()

    assert selected == []  # la pasada del archivo paró por call_limit: el resumen no arranca


async def test_fallo_al_seleccionar_el_archivo_corta_el_resumen(monkeypatch) -> None:
    env = _env(archive=1, weeks=1)
    fake = FakeLLMProvider()
    _approve(fake, env)
    night = _night(env, fake)

    def _boom():
        raise RuntimeError("base caída")

    monkeypatch.setattr(night._select_solution_tensions, "pending", _boom)

    result = await night()

    assert _writes(fake) == [] and env.digest_texts.all() == {}
    assert result.status is RunStatus.PARTIAL and "writer_error" in result.notes


async def test_editor_ya_saltado_no_lanza_la_pasada_ni_selecciona(monkeypatch) -> None:
    new_item = make_item(published_at=NOW)
    env = _env(weeks=1, paper=True)
    env.items.add_many([new_item])
    fake = FakeLLMProvider()
    fake.fail(AgentRole.READER, error=LLMRateLimited("límite"))
    _ok_digest(fake)
    _approve(fake, env)
    selected: list[bool] = []
    real = SelectDigestWeeks.pending

    def _spy(self):
        selected.append(True)
        return real(self)

    monkeypatch.setattr(SelectDigestWeeks, "pending", _spy)
    ingest = IngestResult(
        fetched=1, new=1, duplicates=0, skipped=0, truncated=False, items=[new_item]
    )
    night = make_run_night(
        env=env,
        provider=fake,
        ingest_result=ingest,
        select_digest_weeks=make_digest_selector(env),
        write_digest_text=make_digest_writer(env, fake),
    )

    result = await night()

    assert _roles(fake) == [AgentRole.READER] and selected == []
    assert result.status is RunStatus.PARTIAL


# --- desenlaces del guard y errores en la pasada del resumen ---------------------


async def test_outside_window_antes_de_empezar_es_killed_sin_llamadas() -> None:
    env = _env(weeks=1, paper=True, now=LATE)
    fake = FakeLLMProvider()
    _approve(fake, env)

    result = await _night(env, fake)()

    assert fake.calls == [] and result.status is RunStatus.KILLED


async def test_outside_window_en_la_pasada_del_resumen_es_killed_sin_editor(monkeypatch) -> None:
    env = _env(weeks=2, paper=True)
    fake = _ClockJumpProvider(env, after=1)
    _ok_digest(fake, 2)
    _approve(fake, env)
    entered: list[bool] = []

    async def _spy(self) -> None:
        entered.append(True)

    monkeypatch.setattr(RunNight, "_phase_editor", _spy)

    result = await _night(env, fake)()

    assert len(_digest_writes(fake)) == 1 and entered == []
    assert AgentRole.EDITOR not in _roles(fake)
    assert result.status is RunStatus.KILLED and "outside_window" in result.notes


async def test_outside_window_tras_una_llamada_de_tension_corta_el_resumen(monkeypatch) -> None:
    env = _env(archive=1, weeks=1, paper=True)
    fake = _ClockJumpProvider(env, after=1)
    _ok_tension(fake)
    _ok_digest(fake)
    _approve(fake, env)

    result = await _night(env, fake)()

    assert len(_writes(fake)) == 1 and _digest_writes(fake) == []
    assert AgentRole.EDITOR not in _roles(fake)
    assert result.status is RunStatus.KILLED


async def test_rate_limited_en_el_resumen_es_partial_y_salta_el_editor() -> None:
    env = _env(weeks=2, paper=True)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.WRITER, error=LLMRateLimited("límite"))
    _approve(fake, env)

    result = await _night(env, fake)()

    assert _roles(fake) == [AgentRole.WRITER]
    assert env.digest_texts.all() == {}
    assert result.status is RunStatus.PARTIAL and "end=rate_limited_writer" in result.notes


async def test_run_not_running_pasa_por_terminal_status_for() -> None:
    env = _env(weeks=1, paper=True)
    env.run.finish(RunStatus.COMPLETED, at=env.run.started_at)
    fake = FakeLLMProvider()
    _ok_digest(fake)
    _approve(fake, env)

    result = await _night(env, fake)()

    assert fake.calls == []
    assert result.status is RunStatus.PARTIAL and "end=run_not_running" in result.notes


async def test_fallo_al_guardar_el_texto_corta_la_pasada_y_el_editor_sigue(monkeypatch) -> None:
    env = _env(weeks=2, paper=True)
    fake = FakeLLMProvider()
    _ok_digest(fake, 2)
    _approve(fake, env)

    def _failing_add(_text) -> None:
        raise RuntimeError("IntegrityError simulado")

    monkeypatch.setattr(env.digest_texts, "add", _failing_add)

    result = await _night(env, fake)()

    assert len(_digest_writes(fake)) == 1  # la segunda semana no sale
    assert AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.PARTIAL and "end=writer_error" in result.notes


async def test_fallo_al_seleccionar_el_resumen_es_partial_sin_llamadas_y_el_editor_sigue(
    monkeypatch,
) -> None:
    env = _env(weeks=1, paper=True)
    fake = FakeLLMProvider()
    _approve(fake, env)
    night = _night(env, fake)

    def _boom():
        raise RuntimeError("base caída")

    monkeypatch.setattr(night._select_digest_weeks, "pending", _boom)

    result = await night()

    assert _digest_writes(fake) == [] and AgentRole.EDITOR in _roles(fake)
    assert result.status is RunStatus.PARTIAL and "writer_error" in result.notes


async def test_fallo_al_seleccionar_el_resumen_deja_intactas_las_pasadas_de_tensiones(
    monkeypatch,
) -> None:
    env = _env(archive=1, weeks=1, paper=True)
    fake = FakeLLMProvider()
    _ok_tension(fake)
    _approve(fake, env)
    night = _night(env, fake)
    monkeypatch.setattr(
        night._select_digest_weeks, "pending", lambda: (_ for _ in ()).throw(RuntimeError("x"))
    )

    result = await night()

    assert len(_writes(fake)) == 1 and "<solution_tension>" in _writes(fake)[0].prompt
    assert result.status is RunStatus.PARTIAL


# --- cancelación ------------------------------------------------------------------


async def test_hard_stop_en_vuelo_en_la_llamada_del_resumen_es_killed_con_gasto() -> None:
    env = _env(weeks=1)
    provider = _SlowProvider()

    result = await make_run_night(
        env=env,
        provider=provider,
        ingest_result=_ingest(),
        deadline_s=0.05,
        select_digest_weeks=make_digest_selector(env),
        write_digest_text=make_digest_writer(env, provider),
    )()

    assert result.status is RunStatus.KILLED and "end=hard_stop" in result.notes
    (call,) = env.agent_calls.calls
    assert call.agent is AgentRole.WRITER and call.item_id is None
    assert (call.tokens_in, call.tokens_out) == (400, 25) and call.status is AgentCallStatus.ERROR
    assert result.tokens_used == 425 and env.digest_texts.all() == {}


async def test_cancelacion_externa_en_la_llamada_del_resumen_se_relanza() -> None:
    env = _env(weeks=1)
    provider = _SlowProvider()
    night = make_run_night(
        env=env,
        provider=provider,
        ingest_result=_ingest(),
        select_digest_weeks=make_digest_selector(env),
        write_digest_text=make_digest_writer(env, provider),
    )
    task = asyncio.create_task(night())
    await asyncio.wait_for(provider.started.wait(), timeout=5)

    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=5)


# --- registro ---------------------------------------------------------------------


async def test_el_log_trae_la_fase_writer_digest_y_un_night_item_por_semana(
    caplog, monkeypatch
) -> None:
    monkeypatch.setattr(run_night_logger, "disabled", False)
    env = _env(weeks=1)
    fake = FakeLLMProvider()
    _ok_digest(fake)

    with caplog.at_level(logging.INFO):
        await _night(env, fake)()

    ends = [r for r in _phase_logs(caplog, "night.phase_end") if r.phase == "writer_digest"]
    assert len(ends) == 1 and ends[0].ok == 1 and ends[0].processed == 1
    assert ends[0].failed == 0 and ends[0].stop_reason is None
    items = [r for r in _phase_logs(caplog, "night.item") if r.phase == "writer_digest"]
    assert len(items) == 1
    assert items[0].outcome == "written" and items[0].week == "2026-W44"
    assert items[0].attempts == 1 and items[0].tokens_spent == 1000
