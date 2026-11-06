"""T106 (ADR 0046): un único segundo intento de la ingesta en `RunNight`.

Sin red, sin base de datos y sin Claude: ingesta falsa que falla N veces,
`sleep` falso que registra, `FakeLLMProvider`.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging

import pytest
from fakes.llm import FakeLLMProvider
from helpers.run_night import (
    WITHIN_WINDOW,
    Environment,
    make_item,
    make_policy,
    make_run_night,
    valid_reading_json,
)

from nocturna.application.use_cases import run_night as run_night_module
from nocturna.application.use_cases.ingest_arxiv import IngestResult
from nocturna.domain.entities import RunStatus
from nocturna.domain.llm import AgentRole
from nocturna.domain.night_report import NightCause, NightEvidence, classify_night, parse_run_notes

pytestmark = pytest.mark.anyio


class ArxivUnavailable(Exception):  # noqa: N818 - mismo nombre de clase que el real
    """Doble con el nombre de clase de la excepción real de infrastructure."""


class Ingest:
    """Ingesta que falla las primeras `failures` veces con `error`."""

    def __init__(self, result: IngestResult, failures: int, error: type[Exception] | None = None):
        self.result = result
        self.failures = failures
        self.error = error or ArxivUnavailable
        self.calls = 0
        self.log: list[str] | None = None

    async def __call__(self) -> IngestResult:
        self.calls += 1
        if self.log is not None:
            self.log.append("ingest")
        if self.calls <= self.failures:
            raise self.error("caído")
        return self.result


class Sleeper:
    def __init__(self) -> None:
        self.delays: list[float] = []
        self.log: list[str] | None = None

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)
        if self.log is not None:
            self.log.append("sleep")


async def _hang(_: float) -> None:
    await asyncio.sleep(10)


def _result(items: list) -> IngestResult:
    return IngestResult(
        fetched=len(items), new=len(items), duplicates=0, skipped=0, truncated=False, items=items
    )


def _fake(n: int = 1) -> FakeLLMProvider:
    fake = FakeLLMProvider()
    for _ in range(n):
        fake.respond(
            AgentRole.READER,
            json=valid_reading_json(interest_score=1),
            tokens_in=100,
            tokens_out=10,
        )
    return fake


def _env(items: list | None = None) -> Environment:
    return Environment(items=items or [], policy=make_policy(), now=WITHIN_WINDOW)


def _night(env, fake, ingest, sleeper, **kwargs):
    kwargs.setdefault("ingest_retry_delay_s", 900)
    kwargs.setdefault("ingest_retry_min_remaining_s", 3600)
    return make_run_night(
        env=env, provider=fake, ingest_result=_result([]), ingest=ingest, sleep=sleeper, **kwargs
    )


async def test_falla_una_vez_reintenta_tras_la_espera_y_cierra_completed():
    item = make_item()
    env = _env([item])
    fake = _fake()
    ingest = Ingest(_result([item]), failures=1)
    sleeper = Sleeper()

    result = await _night(env, fake, ingest, sleeper)()

    assert ingest.calls == 2
    assert sleeper.delays == [900]
    assert result.status is RunStatus.COMPLETED
    assert result.items_fetched == 1
    assert result.items_read == 1
    assert [c.role for c in fake.calls if c.role is AgentRole.READER]
    assert (
        "ingest=ok fetched=1 new=1 | ingest_retry=ok:ArxivUnavailable | expired=0 |" in result.notes
    )
    assert "end=ingest_error" not in result.notes


async def test_fallan_los_dos_intentos_exactamente_dos_llamadas_y_partial():
    env = _env()
    ingest = Ingest(_result([]), failures=99)
    sleeper = Sleeper()

    result = await _night(env, _fake(0), ingest, sleeper)()

    assert ingest.calls == 2
    assert sleeper.delays == [900]
    assert result.status is RunStatus.PARTIAL
    assert (
        "ingest=error:ArxivUnavailable fetched=0 new=0 | ingest_retry=error:ArxivUnavailable"
        in result.notes
    )
    assert "end=ingest_error" in result.notes


async def test_ingesta_bien_a_la_primera_no_espera_y_notes_no_cambia():
    item = make_item()
    env = _env([item])
    ingest = Ingest(_result([item]), failures=0)
    sleeper = Sleeper()

    result = await _night(env, _fake(), ingest, sleeper)()

    assert ingest.calls == 1
    assert sleeper.delays == []
    assert result.status is RunStatus.COMPLETED
    assert "ingest_retry" not in result.notes
    assert result.notes.startswith("ingest=ok fetched=1 new=1 | expired=0 | read=")


async def test_delay_cero_desactiva_el_reintento():
    env = _env()
    ingest = Ingest(_result([]), failures=1)
    sleeper = Sleeper()

    result = await _night(env, _fake(0), ingest, sleeper, ingest_retry_delay_s=0)()

    assert ingest.calls == 1
    assert sleeper.delays == []
    assert result.status is RunStatus.PARTIAL
    assert "| ingest_retry=skipped:disabled |" in result.notes
    assert "end=ingest_error" in result.notes


async def test_margen_insuficiente_salta_sin_dormir():
    env = _env()
    ingest = Ingest(_result([]), failures=1)
    sleeper = Sleeper()

    # remaining ~ 4000; 4000 - 900 = 3100 < 3600
    result = await _night(env, _fake(0), ingest, sleeper, deadline_s=4000)()

    assert ingest.calls == 1
    assert sleeper.delays == []
    assert result.status is RunStatus.PARTIAL
    assert "| ingest_retry=skipped:window |" in result.notes


async def test_deadline_cero_salta_por_ventana():
    env = _env()
    ingest = Ingest(_result([]), failures=1)
    sleeper = Sleeper()

    result = await _night(env, _fake(0), ingest, sleeper, deadline_s=0)()

    assert ingest.calls == 1
    assert sleeper.delays == []
    assert "| ingest_retry=skipped:window |" in result.notes


async def test_margen_justo_si_reintenta():
    env = _env()
    ingest = Ingest(_result([]), failures=1)
    sleeper = Sleeper()

    result = await _night(
        env, _fake(0), ingest, sleeper, deadline_s=16_200, ingest_retry_min_remaining_s=15_000
    )()

    assert ingest.calls == 2
    assert "ingest_retry=ok:ArxivUnavailable" in result.notes


async def test_vigia_durante_la_espera_mata_sin_segundo_intento():
    env = _env()
    fake = _fake(0)
    ingest = Ingest(_result([]), failures=1)

    result = await _night(
        env,
        fake,
        ingest,
        _hang,
        deadline_s=1,
        ingest_retry_delay_s=0.5,
        ingest_retry_min_remaining_s=0,
    )()

    assert result.status is RunStatus.KILLED
    assert "end=hard_stop" in result.notes
    assert ingest.calls == 1
    assert fake.calls == []


async def test_vigia_durante_el_segundo_intento_mata_sin_llamadas():
    env = _env()
    fake = _fake(0)
    calls = 0

    async def hanging_second() -> IngestResult:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise ArxivUnavailable("caído")
        await asyncio.sleep(10)
        return _result([])

    async def instant(_: float) -> None:
        return None

    result = await _night(
        env,
        fake,
        hanging_second,
        instant,
        deadline_s=1,
        ingest_retry_delay_s=0.5,
        ingest_retry_min_remaining_s=0,
    )()

    assert result.status is RunStatus.KILLED
    assert "end=hard_stop" in result.notes
    assert calls == 2
    assert fake.calls == []


async def test_vigia_run_timeout_durante_la_espera_usa_ese_motivo():
    env = _env()
    ingest = Ingest(_result([]), failures=1)

    result = await _night(
        env,
        _fake(0),
        ingest,
        _hang,
        deadline_s=1,
        deadline_reason="run_timeout",
        ingest_retry_delay_s=0.5,
        ingest_retry_min_remaining_s=0,
    )()

    assert result.status is RunStatus.KILLED
    assert "end=run_timeout" in result.notes
    assert ingest.calls == 1


async def test_cancelacion_externa_durante_la_espera_se_relanza():
    env = _env()
    ingest = Ingest(_result([]), failures=1)

    task = asyncio.create_task(_night(env, _fake(0), ingest, _hang)())
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert ingest.calls == 1


async def test_value_error_dos_veces_es_ingest_error():
    env = _env()
    ingest = Ingest(_result([]), failures=99, error=ValueError)
    sleeper = Sleeper()

    result = await _night(env, _fake(0), ingest, sleeper)()

    assert ingest.calls == 2
    assert "ingest=error:ValueError" in result.notes
    assert "ingest_retry=error:ValueError" in result.notes
    assert result.status is RunStatus.PARTIAL
    parsed = parse_run_notes(result.notes)
    c = classify_night(
        NightEvidence(run_status=RunStatus.PARTIAL, notes=result.notes, launched=True)
    )
    assert parsed.ingest_retry == "error:ValueError"
    assert c.causes[0] is NightCause.INGEST_ERROR


async def test_orden_ingesta_espera_ingesta_caducidad_reader(monkeypatch):
    item = make_item()
    env = _env([item])
    log: list[str] = []
    ingest = Ingest(_result([item]), failures=1)
    ingest.log = log
    sleeper = Sleeper()
    sleeper.log = log
    real_expire = run_night_module.expire_stale_unread

    def spy(*args, **kwargs):
        log.append("expire")
        return real_expire(*args, **kwargs)

    monkeypatch.setattr(run_night_module, "expire_stale_unread", spy)

    fake = _fake()
    original = fake.run_agent

    async def tracking(*args, **kwargs):
        log.append("reader")
        return await original(*args, **kwargs)

    monkeypatch.setattr(fake, "run_agent", tracking)

    await _night(env, fake, ingest, sleeper)()

    assert log[:5] == ["ingest", "sleep", "ingest", "expire", "reader"]


def test_notes_con_retry_se_parsea_y_no_confunde_al_segmento_ingest():
    notes = (
        "ingest=ok fetched=3 new=2 | ingest_retry=ok:ArxivUnavailable | expired=0 | "
        "read=2(failed=0) | candidates=0 published=0 | tokens=10 | end=edited"
    )
    parsed = parse_run_notes(notes)
    assert parsed.ingest_ok is True
    assert parsed.ingest_error is None
    assert parsed.fetched == 3
    assert parsed.ingest_retry == "ok:ArxivUnavailable"
    assert parse_run_notes("ingest_retry=raro | end=edited").ingest_retry is None


async def test_notes_literal_sin_fallo_identico_al_formato_previo_a_t106():
    item = make_item()
    env = _env([item])
    ingest = Ingest(_result([item]), failures=0)

    result = await _night(env, _fake(), ingest, Sleeper())()

    assert result.notes == (
        "ingest=ok fetched=1 new=1 | expired=0 | read=1(failed=0) | "
        "candidates=0 published=0 | tokens=110 | end=no_candidates"
    )


async def test_retry_ok_ingest_y_end_se_parsean_igual_que_sin_retry():
    item, item2 = make_item(), make_item(external_id="2601.99999")
    ingest = Ingest(_result([item]), failures=1)
    result = await _night(_env([item]), _fake(), ingest, Sleeper())()
    plain = await _night(_env([item2]), _fake(), Ingest(_result([item2]), failures=0), Sleeper())()

    a, b = parse_run_notes(result.notes), parse_run_notes(plain.notes)

    assert a.ingest_retry == "ok:ArxivUnavailable" and b.ingest_retry is None
    for field in ("ingest_ok", "ingest_error", "fetched", "new", "end", "read", "expired"):
        assert getattr(a, field) == getattr(b, field)
    c = classify_night(NightEvidence(run_status=result.status, notes=result.notes, launched=True))
    assert c.causes == ()


async def test_retry_error_se_clasifica_como_arxiv_unavailable():
    ingest = Ingest(_result([]), failures=99)
    result = await _night(_env(), _fake(0), ingest, Sleeper())()

    c = classify_night(NightEvidence(run_status=result.status, notes=result.notes, launched=True))

    assert c.causes == (NightCause.ARXIV_UNAVAILABLE,)
    parsed = parse_run_notes(result.notes)
    assert parsed.ingest_retry == "error:ArxivUnavailable"
    assert parsed.end == "ingest_error"


@contextlib.contextmanager
def _capture_logs(name: str):
    """Captura los registros del logger con el formato JSON real, en nivel INFO."""
    from nocturna.infrastructure.logging import _JsonFormatter

    lines: list[str] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(_JsonFormatter().format(record))

    logger = logging.getLogger(name)
    handler = _H()
    old = (logger.level, logger.disabled, logger.propagate)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.disabled = False
    logging.disable(logging.NOTSET)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)
        logger.level, logger.disabled, logger.propagate = old


async def test_evento_ingest_retry_solo_lleva_el_nombre_de_la_clase_sin_secretos():
    class LeakyError(Exception):
        pass

    url = "https://export.arxiv.org/oai2?token=s3cr3t"
    env = _env()
    ingest = Ingest(_result([]), failures=99, error=LeakyError)
    ingest.error = lambda _msg: LeakyError(f"GET {url} falló con s3cr3t")  # type: ignore[assignment]
    sleeper = Sleeper()

    with _capture_logs(run_night_module.__name__) as lines:
        await _night(env, _fake(0), ingest, sleeper)()

    retry_lines = [line for line in lines if "night.ingest_retry" in line]
    assert retry_lines
    for line in retry_lines:
        assert "LeakyError" in line
        assert "s3cr3t" not in line
        assert "export.arxiv.org" not in line
