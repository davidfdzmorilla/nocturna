"""T84.r: `SelectDigestWeeks` (solo lectura) y `WriteDigestText` con `FakeLLMProvider`.

En memoria: ningún test llama a Claude. Fichero en español.
"""

from __future__ import annotations

import hashlib
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
from helpers.run_night import Environment, make_policy

from nocturna.application.budget import BudgetDenied, DenyReason
from nocturna.application.use_cases.write_archive_digest import (
    DigestCandidate,
    PendingDigestWeeks,
    block_sha256,
    build_digest_block,
)
from nocturna.application.use_cases.write_tensions import WriteOutcome
from nocturna.domain.archive_digest import DigestTextStatus
from nocturna.domain.entities import AgentCallStatus
from nocturna.domain.errors import LLMError, LLMRateLimited, LLMTimeout
from nocturna.domain.llm import AgentRole

pytestmark = pytest.mark.anyio


def _env(**policy) -> Environment:
    defaults = {"writer_reserve_tokens": 20_000, "max_writer_calls_per_night": 5}
    defaults.update(policy)
    return Environment(items=[], policy=make_policy(**defaults), now=NOW, with_digest=True)


def _load_calendar(env: Environment) -> None:
    load_reader(env.digest_reader, "2026-W39", new_planet("Z b", "2026-W39"))
    load_reader(env.digest_reader, "2026-W40", changed("A b", "2026-W40"))
    load_reader(env.digest_reader, "2026-W41", new_planet("N b", "2026-W41"))
    load_reader(env.digest_reader, "2026-W42", changed("C b", "2026-W42"))
    load_reader(env.digest_reader, "2026-W43")  # snapshot sin transiciones
    load_reader(env.digest_reader, "2026-W44", new_planet("M b", "2026-W44"))
    load_reader(env.digest_reader, "2026-W46", new_planet("O b", "2026-W46"))  # abierta
    env.digest_texts.add(digest_text("2026-W41", env.run.id, sha="f" * 64))
    env.digest_texts.add(digest_text("2026-W42", env.run.id, status="failed"))


def _one_candidate(env: Environment) -> DigestCandidate:
    load_reader(env.digest_reader, "2026-W42", changed("A b", "2026-W42"))
    (candidate,) = make_digest_selector(env).pending().candidates
    return candidate


def _ok(fake: FakeLLMProvider, **overrides: object) -> None:
    fake.respond(AgentRole.WRITER, json={**DIGEST_JSON, **overrides}, tokens_in=700, tokens_out=300)


# --- SelectDigestWeeks --------------------------------------------------------


def test_pending_elige_las_semanas_cerradas_con_transiciones_mas_reciente_primero() -> None:
    env = _env()
    _load_calendar(env)

    pending = make_digest_selector(env).pending()

    assert isinstance(pending, PendingDigestWeeks)
    assert [c.digest.week for c in pending.candidates] == ["2026-W44", "2026-W40"]


def test_pending_explica_el_motivo_de_cada_semana_excluida() -> None:
    env = _env()
    _load_calendar(env)

    pending = make_digest_selector(env).pending()

    assert dict(pending.skipped) == {
        "2026-W39": "before_since",
        "2026-W41": "already_written",
        "2026-W42": "writer_failed",
        "2026-W43": "no_transitions",
        "2026-W46": "open_week",
    }


def test_el_candidato_lleva_el_bloque_y_su_hash() -> None:
    env = _env()
    candidate = _one_candidate(env)

    assert candidate.block == build_digest_block(candidate.digest)
    assert candidate.input_sha256 == block_sha256(candidate.block)
    assert candidate.input_sha256 == hashlib.sha256(candidate.block.encode()).hexdigest()


def test_pending_es_solo_lectura_sin_gasto_ni_escrituras() -> None:
    env = _env()
    _load_calendar(env)
    before = env.digest_texts.all()

    make_digest_selector(env).pending()

    assert env.agent_calls.calls == []
    assert env.digest_texts.all() == before


def test_un_texto_ya_escrito_con_otro_hash_sigue_siendo_already_written() -> None:
    # El hash distinto solo lo marca `archive-digest` como «desactualizado»: no se reescribe.
    env = _env()
    load_reader(env.digest_reader, "2026-W42", changed("A b", "2026-W42"))
    env.digest_texts.add(digest_text("2026-W42", env.run.id, sha="0" * 64))

    pending = make_digest_selector(env).pending()

    assert pending.candidates == () and dict(pending.skipped) == {"2026-W42": "already_written"}


def test_since_week_igual_a_la_semana_la_incluye() -> None:
    env = _env()
    load_reader(env.digest_reader, "2026-W40", changed("A b", "2026-W40"))
    assert [
        c.digest.week for c in make_digest_selector(env, since_week="2026-W40").pending().candidates
    ] == ["2026-W40"]
    assert make_digest_selector(env, since_week="2026-W41").pending().candidates == ()


def test_sin_snapshots_no_hay_nada_pendiente() -> None:
    pending = make_digest_selector(_env()).pending()
    assert pending.candidates == () and pending.skipped == ()


def test_el_selector_usa_el_reloj_inyectado() -> None:
    env = _env()
    load_reader(env.digest_reader, "2026-W46", new_planet("O b", "2026-W46"))
    assert make_digest_selector(env).pending().candidates == ()  # abierta el 10 de noviembre
    env.clock.set(datetime(2026, 11, 17, 2, 0, tzinfo=UTC))
    assert [c.digest.week for c in make_digest_selector(env).pending().candidates] == ["2026-W46"]


# --- WriteDigestText: camino feliz --------------------------------------------


async def test_valido_guarda_el_texto_written_y_registra_la_llamada_del_redactor() -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    _ok(fake)

    result = await make_digest_writer(env, fake)(candidate)

    assert result.outcome is WriteOutcome.WRITTEN and result.attempts == 1
    assert result.tokens_spent == 1000
    stored = env.digest_texts.get("2026-W42")
    assert stored is not None and result.text == stored
    assert stored.status is DigestTextStatus.WRITTEN
    assert stored.title == DIGEST_JSON["title"] and stored.summary == DIGEST_JSON["summary"]
    assert stored.input_sha256 == candidate.input_sha256
    assert stored.prompt_version == "writer-digest-v1"
    assert stored.run_id == env.run.id
    (call,) = env.agent_calls.calls
    assert call.agent is AgentRole.WRITER and call.item_id is None
    assert call.prompt_version == "writer-digest-v1" and call.status is AgentCallStatus.OK
    assert [c.role for c in fake.calls] == [AgentRole.WRITER]
    assert fake.calls[0].prompt == candidate.block


async def test_el_salto_de_linea_literal_se_normaliza_como_en_t96() -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    _ok(fake, summary="Primer párrafo.\\n\\nSegundo párrafo.")

    await make_digest_writer(env, fake)(candidate)

    assert env.digest_texts.get("2026-W42").summary == "Primer párrafo.\n\nSegundo párrafo."


async def test_los_campos_extra_se_ignoran() -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    _ok(fake, extra="ignorado")

    result = await make_digest_writer(env, fake)(candidate)

    assert result.outcome is WriteOutcome.WRITTEN


async def test_un_json_invalido_y_luego_valido_escribe_en_el_segundo_intento() -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, raw="no es json", tokens_in=100, tokens_out=10)
    _ok(fake)

    result = await make_digest_writer(env, fake)(candidate)

    assert result.outcome is WriteOutcome.WRITTEN and result.attempts == 2
    assert env.digest_texts.get("2026-W42").status is DigestTextStatus.WRITTEN
    assert [c.status for c in env.agent_calls.calls] == [
        AgentCallStatus.INVALID_OUTPUT,
        AgentCallStatus.OK,
    ]


# --- WriteDigestText: salida inválida -----------------------------------------


@pytest.mark.parametrize(
    "payload",
    [
        {"title": "solo título"},
        {"summary": "solo resumen"},
        {"title": "   ", "summary": "resumen"},
        {"title": "título", "summary": ""},
        {"title": "x" * 91, "summary": "resumen"},
        {"title": 3, "summary": "resumen"},
    ],
)
async def test_salida_invalida_dos_veces_guarda_la_fila_failed_con_dos_agent_calls(
    payload: dict,
) -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    for _ in range(2):
        fake.respond(AgentRole.WRITER, json=payload, tokens_in=100, tokens_out=10)

    result = await make_digest_writer(env, fake, max_attempts=2)(candidate)

    assert result.outcome is WriteOutcome.INVALID_OUTPUT and result.attempts == 2
    stored = env.digest_texts.get("2026-W42")
    assert stored is not None and stored.status is DigestTextStatus.FAILED
    assert stored.title is None and stored.summary is None
    assert stored.input_sha256 == candidate.input_sha256
    assert stored.prompt_version == "writer-digest-v1" and stored.run_id == env.run.id
    assert [c.status for c in env.agent_calls.calls] == [AgentCallStatus.INVALID_OUTPUT] * 2
    assert all(c.item_id is None and c.agent is AgentRole.WRITER for c in env.agent_calls.calls)


async def test_titulo_de_90_caracteres_es_valido() -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    _ok(fake, title="x" * 90)

    result = await make_digest_writer(env, fake)(candidate)

    assert result.outcome is WriteOutcome.WRITTEN


async def test_tras_una_fila_failed_la_semana_sale_como_writer_failed() -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    for _ in range(2):
        fake.respond(AgentRole.WRITER, raw="basura", tokens_in=100, tokens_out=10)
    await make_digest_writer(env, fake, max_attempts=2)(candidate)

    pending = make_digest_selector(env).pending()

    assert pending.candidates == () and dict(pending.skipped) == {"2026-W42": "writer_failed"}


# --- WriteDigestText: fallos del agente y del guard ---------------------------


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (LLMError("fallo del agente"), WriteOutcome.AGENT_ERROR),
        (LLMTimeout("tiempo agotado"), WriteOutcome.TIMEOUT),
        (LLMRateLimited("límite alcanzado"), WriteOutcome.RATE_LIMITED),
    ],
)
async def test_errores_del_agente_no_dejan_fila_y_la_semana_sigue_pendiente(
    error: Exception, outcome: WriteOutcome
) -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    fake.fail(AgentRole.WRITER, error=error)

    result = await make_digest_writer(env, fake)(candidate)

    assert result.outcome is outcome
    assert env.digest_texts.get("2026-W42") is None
    assert [c.digest.week for c in make_digest_selector(env).pending().candidates] == ["2026-W42"]


async def test_budget_denied_sale_sin_traducir_y_sin_llamada_ni_fila() -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()

    with pytest.raises(BudgetDenied) as exc:
        await make_digest_writer(env, fake, estimated_tokens=10**9)(candidate)

    assert exc.value.reason is DenyReason.BUDGET_EXHAUSTED
    assert fake.calls == [] and env.agent_calls.calls == []
    assert env.digest_texts.get("2026-W42") is None


async def test_invalida_y_reintento_denegado_guarda_failed_y_propaga_la_denegacion() -> None:
    env = _env(max_writer_calls_per_night=1)
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, raw="basura", tokens_in=100, tokens_out=10)

    with pytest.raises(BudgetDenied) as exc:
        await make_digest_writer(env, fake, max_attempts=2)(candidate)

    assert exc.value.reason is DenyReason.CALL_LIMIT_REACHED
    stored = env.digest_texts.get("2026-W42")
    assert stored is not None and stored.status is DigestTextStatus.FAILED
    assert stored.input_sha256 == candidate.input_sha256
    assert stored.prompt_version == "writer-digest-v1" and stored.run_id == env.run.id
    assert stored.created_at == env.clock.now()
    assert [c.status for c in env.agent_calls.calls] == [AgentCallStatus.INVALID_OUTPUT]
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    "reason",
    [DenyReason.OUTSIDE_WINDOW, DenyReason.BUDGET_EXHAUSTED, DenyReason.CALL_LIMIT_REACHED],
)
async def test_invalida_y_reintento_denegado_guarda_failed_y_propaga_la_misma_denegacion(
    reason: DenyReason,
) -> None:
    env = _env()
    candidate = _one_candidate(env)
    real = env.guard.authorize
    denial = BudgetDenied(reason, AgentRole.WRITER, 0)
    calls = 0

    def _deny_second(role, estimated_tokens):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise denial
        return real(role, estimated_tokens)

    env.guard.authorize = _deny_second  # type: ignore[method-assign]
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, raw="basura", tokens_in=100, tokens_out=10)

    with pytest.raises(BudgetDenied) as exc:
        await make_digest_writer(env, fake, max_attempts=2)(candidate)

    assert exc.value is denial and exc.value.reason is reason
    stored = env.digest_texts.get("2026-W42")
    assert stored is not None and stored.status is DigestTextStatus.FAILED
    assert len(fake.calls) == 1


@pytest.mark.parametrize(
    ("error", "outcome"),
    [
        (LLMError("fallo del agente"), WriteOutcome.AGENT_ERROR),
        (LLMTimeout("tiempo agotado"), WriteOutcome.TIMEOUT),
    ],
)
async def test_invalida_y_luego_error_del_agente_no_deja_fila(
    error: Exception, outcome: WriteOutcome
) -> None:
    env = _env()
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, raw="basura", tokens_in=100, tokens_out=10)
    fake.fail(AgentRole.WRITER, error=error)

    result = await make_digest_writer(env, fake, max_attempts=2)(candidate)

    assert result.outcome is outcome
    assert env.digest_texts.get("2026-W42") is None


async def test_si_guardar_la_fila_failed_falla_sale_la_denegacion_original_y_se_registra(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # alembic/env.py (fileConfig) deshabilita los loggers `nocturna.*` en la suite completa.
    import nocturna.application.use_cases.write_archive_digest as digest_module

    monkeypatch.setattr(digest_module._logger, "disabled", False)
    env = _env(max_writer_calls_per_night=1)
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()
    fake.respond(AgentRole.WRITER, raw="basura", tokens_in=100, tokens_out=10)
    writer = make_digest_writer(env, fake, max_attempts=2)

    def _boom(text) -> None:
        raise RuntimeError("base caída")

    writer._store = _boom  # type: ignore[method-assign]

    with caplog.at_level(logging.ERROR), pytest.raises(BudgetDenied) as exc:
        await writer(candidate)

    assert exc.value.reason is DenyReason.CALL_LIMIT_REACHED
    records = [
        r for r in caplog.records if getattr(r, "event", "") == "digest.failed_row_store_error"
    ]
    assert len(records) == 1
    assert records[0].week == "2026-W42" and records[0].error_type == "RuntimeError"
    assert env.digest_texts.get("2026-W42") is None


async def test_denegacion_antes_de_ningun_intento_no_deja_fila() -> None:
    env = _env(max_writer_calls_per_night=0)
    candidate = _one_candidate(env)
    fake = FakeLLMProvider()

    with pytest.raises(BudgetDenied) as exc:
        await make_digest_writer(env, fake, max_attempts=2)(candidate)

    assert exc.value.reason is DenyReason.CALL_LIMIT_REACHED
    assert fake.calls == [] and env.agent_calls.calls == []
    assert env.digest_texts.get("2026-W42") is None


async def test_la_autorizacion_pasa_por_el_guard_con_rol_writer_y_la_estimacion() -> None:
    env = _env()
    candidate = _one_candidate(env)
    seen: list[tuple[AgentRole, int]] = []
    real = env.guard.authorize

    def _spy(role, estimated_tokens):
        seen.append((role, estimated_tokens))
        return real(role, estimated_tokens)

    env.guard.authorize = _spy  # type: ignore[method-assign]
    fake = FakeLLMProvider()
    _ok(fake)

    await make_digest_writer(env, fake, estimated_tokens=777)(candidate)

    assert seen == [(AgentRole.WRITER, 777)]


async def test_la_llamada_del_redactor_no_acumula_contexto_una_conversacion_por_semana() -> None:
    env = _env()
    load_reader(env.digest_reader, "2026-W42", changed("A b", "2026-W42"))
    load_reader(env.digest_reader, "2026-W44", new_planet("M b", "2026-W44"))
    candidates = make_digest_selector(env).pending().candidates
    fake = FakeLLMProvider()
    for _ in candidates:
        _ok(fake)
    writer = make_digest_writer(env, fake)

    for candidate in candidates:
        await writer(candidate)

    assert [c.prompt for c in fake.calls] == [c.block for c in candidates]
