"""T105: matriz de clasificación de noches, lectura de `Run.notes` y noche a informar.

Complementa `test_night_report.py`: cada valor de `end=` que escribe `RunNight._build_notes`
con su causa y detalle, los casos reales (10-29, 11-05) y los bordes de `target_night` /
`is_night_run`. Dominio puro: sin base, sin Claude, sin red.
"""

from __future__ import annotations

from datetime import UTC, datetime, time
from zoneinfo import ZoneInfo

import pytest

from nocturna.domain.entities import Run, RunStatus
from nocturna.domain.night_report import (
    ORPHAN_NOTE,
    NightCause,
    NightEvidence,
    ParsedNotes,
    classify_night,
    is_night_run,
    parse_run_notes,
    target_night,
)

MADRID = ZoneInfo("Europe/Madrid")


def _notes(end: str, ingest: str = "ok") -> str:
    return (
        f"ingest={ingest} fetched=10 new=4 | expired=0 | read=4(failed=1) | "
        f"candidates=2 published=1 | tokens=1234 | end={end}"
    )


def _ev(status, notes=None, **kw) -> NightEvidence:
    kw.setdefault("launched", True)
    return NightEvidence(run_status=status, notes=notes, **kw)


# --- parse_run_notes -----------------------------------------------------------------


@pytest.mark.parametrize("notes", [None, "", "reread", "esto no es un formato", "a|b|c", "=|=|="])
def test_parse_run_notes_ilegible_deja_todos_los_campos_en_none(notes):
    assert parse_run_notes(notes) == ParsedNotes()


def test_parse_run_notes_con_ingesta_arxiv_unavailable():
    parsed = parse_run_notes(_notes("ingest_error", ingest="error:ArxivUnavailable"))

    assert parsed.ingest_ok is False
    assert parsed.ingest_error == "ArxivUnavailable"
    assert parsed.end == "ingest_error"


def test_parse_run_notes_con_un_segmento_roto_conserva_el_resto():
    parsed = parse_run_notes("ingest=ok fetched=x new=? | read=roto | tokens=77 | end=edited")

    assert parsed.ingest_ok is True
    assert (parsed.read, parsed.read_failed) == (None, None)
    assert (parsed.tokens, parsed.end) == (77, "edited")


# --- classify_night: un caso por cada valor de end= ----------------------------------------

_ENDS = [
    ("ingest_error", NightCause.INGEST_ERROR, "error:ValueError", "ValueError"),
    ("ingest_error", NightCause.ARXIV_UNAVAILABLE, "error:ArxivUnavailable", "ArxivUnavailable"),
    ("expire_error", NightCause.EXPIRE_ERROR, "ok", ""),
    ("circuit_breaker_reader", NightCause.CIRCUIT_BREAKER, "ok", "reader"),
    ("circuit_breaker_popularizer", NightCause.CIRCUIT_BREAKER, "ok", "popularizer"),
    ("rate_limited_reader", NightCause.RATE_LIMITED, "ok", "reader"),
    ("rate_limited_popularizer", NightCause.RATE_LIMITED, "ok", "popularizer"),
    ("rate_limited_writer", NightCause.RATE_LIMITED, "ok", "writer"),
    ("auth_failed_reader", NightCause.AUTH_FAILED, "ok", "reader"),
    ("auth_failed_popularizer", NightCause.AUTH_FAILED, "ok", "popularizer"),
    ("auth_failed_writer", NightCause.AUTH_FAILED, "ok", "writer"),
    ("auth_failed_editor", NightCause.AUTH_FAILED, "ok", "editor"),
    ("writer_error", NightCause.WRITER_ERROR, "ok", ""),
    ("writer_budget_exhausted", NightCause.BUDGET_EXHAUSTED, "ok", "writer_budget_exhausted"),
    ("measurement_findings_error", NightCause.MEASUREMENT_FINDINGS_ERROR, "ok", ""),
    ("editor_already_called", NightCause.BUDGET_DENIED, "ok", "editor_already_called"),
    ("editor_invalid_output", NightCause.EDITOR_FAILED, "ok", "invalid_output"),
    ("editor_timeout", NightCause.EDITOR_FAILED, "ok", "timeout"),
    ("editor_agent_error", NightCause.EDITOR_FAILED, "ok", "agent_error"),
    ("editor_rate_limited", NightCause.EDITOR_FAILED, "ok", "rate_limited"),
    ("budget_exhausted", NightCause.BUDGET_EXHAUSTED, "ok", "budget_exhausted"),
    ("call_limit_reached", NightCause.BUDGET_DENIED, "ok", "call_limit_reached"),
    ("run_not_running", NightCause.BUDGET_DENIED, "ok", "run_not_running"),
    ("outside_window", NightCause.HARD_STOP, "ok", "outside_window"),
    ("hard_stop", NightCause.HARD_STOP, "ok", "hard_stop"),
    ("run_timeout", NightCause.RUN_TIMEOUT, "ok", ""),
]


@pytest.mark.parametrize(("end", "cause", "ingest", "detail"), _ENDS)
@pytest.mark.parametrize("status", [RunStatus.PARTIAL, RunStatus.KILLED])
def test_cada_motivo_de_fin_da_su_causa_y_su_detalle(end, cause, ingest, detail, status):
    result = classify_night(_ev(status, _notes(end, ingest), failed_calls=0))

    assert result.primary is cause
    if cause is NightCause.CIRCUIT_BREAKER:
        assert result.detail.startswith(detail + ":")
    else:
        assert result.detail == detail


@pytest.mark.parametrize("end", ["edited", "no_candidates"])
def test_completed_con_fin_normal_no_tiene_causas(end):
    assert classify_night(_ev(RunStatus.COMPLETED, _notes(end))).causes == ()


def test_completed_con_ingesta_fallida_sigue_explicando_la_ingesta():
    result = classify_night(_ev(RunStatus.COMPLETED, _notes("edited", "error:ArxivUnavailable")))

    assert result.causes == (NightCause.ARXIV_UNAVAILABLE,)


def test_killed_por_hard_stop_con_arxiv_caido_da_hard_stop_y_arxiv():
    notes = _notes("hard_stop", ingest="error:ArxivUnavailable")

    result = classify_night(_ev(RunStatus.KILLED, notes))

    assert result.causes == (NightCause.HARD_STOP, NightCause.ARXIV_UNAVAILABLE)


def test_caso_del_29_de_octubre_cortacircuitos_del_reader_con_tres_errores_de_cero_tokens():
    result = classify_night(
        _ev(
            RunStatus.PARTIAL,
            _notes("circuit_breaker_reader"),
            failed_calls=3,
            failed_calls_zero_tokens=3,
        )
    )

    assert result.causes == (NightCause.CIRCUIT_BREAKER,)
    assert "reader" in result.detail
    assert "3 llamadas con error" in result.detail
    assert "3 con 0 tokens" in result.detail
    assert NightCause.RATE_LIMITED not in result.causes


@pytest.mark.parametrize("end", ["unknown", "motivo_del_futuro", "circuit"])
def test_fin_desconocido_o_futuro_es_unclassified_con_el_bruto(end):
    result = classify_night(_ev(RunStatus.PARTIAL, _notes(end)))

    assert result.causes == (NightCause.UNCLASSIFIED,)
    assert result.detail == f"end={end}"


def test_huerfano_killed_es_orphan_closed():
    assert classify_night(_ev(RunStatus.KILLED, ORPHAN_NOTE)).causes == (NightCause.ORPHAN_CLOSED,)


def test_failed_sin_notas_es_excepcion():
    assert classify_night(_ev(RunStatus.FAILED, None)).causes == (NightCause.EXCEPTION,)


def test_running_pasado_hard_stop_es_still_running():
    result = classify_night(_ev(RunStatus.RUNNING, None, past_hard_stop=True))

    assert result.causes == (NightCause.STILL_RUNNING,)


# --- sin Run ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "cause"),
    [
        ({"launched": False}, NightCause.NOT_LAUNCHED),
        ({"wrapper_exits": {"precondition": 75}}, NightCause.PRECONDITIONS),
        ({"wrapper_exits": {"environment": 77}}, NightCause.ENVIRONMENT),
        ({"wrapper_exits": {"run-night": 0}}, NightCause.LAUNCHED_NO_RUN),
        ({"wrapper_exits": {"run-night": 0}, "db_reachable": False}, NightCause.DB_UNREACHABLE),
        ({"db_reachable": False, "launched": False}, NightCause.DB_UNREACHABLE),
        ({"wrapper_exits": {"duplicate": 76}}, NightCause.UNCLASSIFIED),
    ],
)
def test_sin_run_cada_evidencia_da_su_causa(kwargs, cause):
    assert classify_night(_ev(None, None, **kwargs)).causes == (cause,)


def test_precondiciones_llevan_el_codigo_en_el_detalle():
    result = classify_night(_ev(None, None, wrapper_exits={"precondition": 75}))

    assert "75" in result.detail


def test_clasificar_nunca_lanza_con_combinaciones_raras():
    for status in (*RunStatus, None):
        for notes in (None, "", "x", ORPHAN_NOTE, _notes("hard_stop"), _notes("???")):
            classify_night(_ev(status, notes))
            classify_night(_ev(status, notes, wrapper_exits={"duplicate": 76}, launched=False))


# --- target_night -----------------------------------------------------------------------

HARD_STOP = time(4, 45)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 11, 5, 4, 44, tzinfo=MADRID), "2026-11-04"),
        (datetime(2026, 11, 5, 4, 45, tzinfo=MADRID), "2026-11-05"),
        (datetime(2026, 11, 5, 7, 30, tzinfo=MADRID), "2026-11-05"),
        (datetime(2026, 11, 5, 23, 0, tzinfo=MADRID), "2026-11-05"),
        # Cambio de hora de otoño en Europe/Madrid (domingo 2026-10-25, 03:00 -> 02:00).
        (datetime(2026, 10, 25, 4, 44, tzinfo=MADRID), "2026-10-24"),
        (datetime(2026, 10, 25, 4, 45, tzinfo=MADRID), "2026-10-25"),
        (datetime(2026, 10, 25, 7, 30, tzinfo=MADRID), "2026-10-25"),
        (datetime(2026, 10, 26, 0, 30, tzinfo=MADRID), "2026-10-25"),
    ],
)
def test_target_night_en_los_bordes_y_en_el_cambio_de_hora(now, expected):
    assert target_night(now, HARD_STOP).isoformat() == expected


# --- is_night_run ------------------------------------------------------------------------


def _run(notes: str | None, status: RunStatus) -> Run:
    run = Run(started_at=datetime(2026, 11, 5, 0, 5, tzinfo=UTC), budget_tokens=300_000)
    if status is RunStatus.RUNNING:
        run.notes = notes
    else:
        run.finish(status, datetime(2026, 11, 5, 1, 0, tzinfo=UTC), notes)
    return run


@pytest.mark.parametrize("status", list(RunStatus))
def test_is_night_run_excluye_reread_en_cualquier_estado(status):
    assert is_night_run(_run("reread", status)) is False


@pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.KILLED])
def test_is_night_run_excluye_run_item_con_notas_none_o_vacias(status):
    assert is_night_run(_run(None, status)) is False
    assert is_night_run(_run("", status)) is False


@pytest.mark.parametrize(
    ("notes", "status"),
    [
        (_notes("edited"), RunStatus.COMPLETED),
        (_notes("hard_stop"), RunStatus.KILLED),
        (ORPHAN_NOTE, RunStatus.KILLED),
        (None, RunStatus.FAILED),
        (None, RunStatus.RUNNING),
    ],
)
def test_is_night_run_incluye_run_night_fallidos_huerfanos_y_en_curso(notes, status):
    assert is_night_run(_run(notes, status)) is True
