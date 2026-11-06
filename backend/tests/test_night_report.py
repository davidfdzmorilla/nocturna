"""T105: clasificación de la causa de una noche, lectura de `Run.notes` y detección de la noche.

Dominio puro, sin base ni Claude.
"""

from __future__ import annotations

from datetime import UTC, datetime, time

import pytest

from nocturna.domain.entities import Run, RunStatus
from nocturna.domain.night_report import (
    ORPHAN_NOTE,
    NightCause,
    NightEvidence,
    classify_night,
    is_night_run,
    parse_run_notes,
    target_night,
)

_BASE = "ingest=ok fetched=10 new=4 | expired=0 | read=4(failed=0) | candidates=2 published=1"


def _notes(end: str, *, ingest: str = "ok", tokens: int = 1234) -> str:
    return (
        f"ingest={ingest} fetched=10 new=4 | expired=0 | read=4(failed=1) | "
        f"candidates=2 published=1 | tokens={tokens} | end={end}"
    )


def _evidence(status: RunStatus | None, notes: str | None = None, **kw) -> NightEvidence:
    kw.setdefault("launched", True)
    return NightEvidence(run_status=status, notes=notes, **kw)


def test_parse_run_notes_completo():
    parsed = parse_run_notes(_notes("edited", ingest="error:ArxivUnavailable"))

    assert parsed.ingest_ok is False
    assert parsed.ingest_error == "ArxivUnavailable"
    assert (parsed.fetched, parsed.new, parsed.expired) == (10, 4, 0)
    assert (parsed.read, parsed.read_failed) == (4, 1)
    assert (parsed.candidates, parsed.published, parsed.tokens) == (2, 1, 1234)
    assert parsed.end == "edited"


@pytest.mark.parametrize("notes", [None, "", "reread", "basura | = | end", "end=", "ingest=??"])
def test_parse_run_notes_nunca_lanza(notes):
    parse_run_notes(notes)


def test_parse_run_notes_con_error_de_caducidad():
    parsed = parse_run_notes("ingest=ok fetched=1 new=1 | expired=3:Boom | end=x")

    assert (parsed.expired, parsed.expire_error) == (3, "Boom")


@pytest.mark.parametrize(
    ("end", "ingest", "cause"),
    [
        ("ingest_error", "error:ArxivUnavailable", NightCause.ARXIV_UNAVAILABLE),
        ("ingest_error", "error:ValueError", NightCause.INGEST_ERROR),
        ("expire_error", "ok", NightCause.EXPIRE_ERROR),
        ("circuit_breaker_reader", "ok", NightCause.CIRCUIT_BREAKER),
        ("circuit_breaker_popularizer", "ok", NightCause.CIRCUIT_BREAKER),
        ("rate_limited_reader", "ok", NightCause.RATE_LIMITED),
        ("rate_limited_popularizer", "ok", NightCause.RATE_LIMITED),
        ("rate_limited_writer", "ok", NightCause.RATE_LIMITED),
        ("budget_exhausted", "ok", NightCause.BUDGET_EXHAUSTED),
        ("writer_budget_exhausted", "ok", NightCause.BUDGET_EXHAUSTED),
        ("call_limit_reached", "ok", NightCause.BUDGET_DENIED),
        ("run_not_running", "ok", NightCause.BUDGET_DENIED),
        ("editor_already_called", "ok", NightCause.BUDGET_DENIED),
        ("writer_error", "ok", NightCause.WRITER_ERROR),
        ("measurement_findings_error", "ok", NightCause.MEASUREMENT_FINDINGS_ERROR),
        ("editor_invalid_output", "ok", NightCause.EDITOR_FAILED),
        ("editor_timeout", "ok", NightCause.EDITOR_FAILED),
        ("editor_agent_error", "ok", NightCause.EDITOR_FAILED),
        ("editor_rate_limited", "ok", NightCause.EDITOR_FAILED),
        ("hard_stop", "ok", NightCause.HARD_STOP),
        ("outside_window", "ok", NightCause.HARD_STOP),
        ("run_timeout", "ok", NightCause.RUN_TIMEOUT),
    ],
)
def test_causa_principal_por_motivo_de_fin(end, ingest, cause):
    status = RunStatus.KILLED if end in ("hard_stop", "outside_window") else RunStatus.PARTIAL

    result = classify_night(_evidence(status, _notes(end, ingest=ingest)))

    assert result.causes[0] is cause


def test_arxiv_no_disponible_lleva_la_clase_en_el_detalle():
    result = classify_night(
        _evidence(RunStatus.PARTIAL, _notes("ingest_error", ingest="error:ArxivUnavailable"))
    )

    assert result.causes == (NightCause.ARXIV_UNAVAILABLE,)
    assert "ArxivUnavailable" in result.detail


def test_error_de_ingesta_coexistente_con_otro_fin_es_segunda_causa():
    notes = _notes("circuit_breaker_reader", ingest="error:ArxivUnavailable")

    result = classify_night(_evidence(RunStatus.PARTIAL, notes))

    assert result.causes == (NightCause.CIRCUIT_BREAKER, NightCause.ARXIV_UNAVAILABLE)


def test_cortacircuitos_detalla_rol_y_llamadas():
    result = classify_night(
        _evidence(
            RunStatus.PARTIAL,
            _notes("circuit_breaker_reader"),
            failed_calls=5,
            failed_calls_zero_tokens=3,
        )
    )

    assert result.detail == "reader: 5 llamadas con error, 3 con 0 tokens"


def test_rate_limited_solo_con_motivo_rate_limited():
    # Un cortacircuitos nunca se presenta como límite de uso aunque haya llamadas con 0 tokens.
    result = classify_night(
        _evidence(
            RunStatus.PARTIAL,
            _notes("circuit_breaker_reader"),
            failed_calls=5,
            failed_calls_zero_tokens=5,
        )
    )

    assert NightCause.RATE_LIMITED not in result.causes


def test_completed_sin_incidencias_no_tiene_causas():
    assert classify_night(_evidence(RunStatus.COMPLETED, _notes("edited"))).causes == ()


def test_failed_es_excepcion():
    assert classify_night(_evidence(RunStatus.FAILED, None)).causes == (NightCause.EXCEPTION,)


def test_huerfano_cerrado_como_killed():
    result = classify_night(_evidence(RunStatus.KILLED, ORPHAN_NOTE))

    assert result.causes == (NightCause.ORPHAN_CLOSED,)


def test_running_pasada_la_hora_limite():
    assert classify_night(_evidence(RunStatus.RUNNING, "")).causes == (NightCause.STILL_RUNNING,)


def test_running_antes_de_la_hora_limite_no_se_inventa():
    result = classify_night(_evidence(RunStatus.RUNNING, "", past_hard_stop=False))

    assert result.causes == (NightCause.UNCLASSIFIED,)
    assert result.detail == "status=running"


def test_fin_desconocido_va_a_unclassified_con_el_dato_bruto():
    result = classify_night(_evidence(RunStatus.PARTIAL, _notes("algo_nuevo")))

    assert result.causes == (NightCause.UNCLASSIFIED,)
    assert result.detail == "end=algo_nuevo"


def test_partial_sin_notas_legibles_es_unclassified_con_el_estado():
    result = classify_night(_evidence(RunStatus.PARTIAL, "???"))

    assert result.causes == (NightCause.UNCLASSIFIED,)
    assert result.detail == "status=partial"


# --- Sin Run --------------------------------------------------------------------------


def test_sin_run_ni_centinela_ni_estado_es_no_lanzado():
    result = classify_night(_evidence(None, launched=False))

    assert result.causes == (NightCause.NOT_LAUNCHED,)


@pytest.mark.parametrize(
    ("exits", "cause"),
    [
        ({"precondition": 75}, NightCause.PRECONDITIONS),
        ({"environment": 77}, NightCause.ENVIRONMENT),
    ],
)
def test_sin_run_con_estado_del_envoltorio(exits, cause):
    assert classify_night(_evidence(None, launched=False, wrapper_exits=exits)).causes == (cause,)


def test_sin_run_con_run_night_registrado_es_lanzado_sin_run():
    result = classify_night(_evidence(None, wrapper_exits={"run-night": 1, "evaluate-tensions": 0}))

    assert result.causes == (NightCause.LAUNCHED_NO_RUN,)
    assert "run-night=1" in result.detail


def test_solo_duplicado_es_unclassified():
    result = classify_night(_evidence(None, launched=True, wrapper_exits={"duplicate": 76}))

    assert result.causes == (NightCause.UNCLASSIFIED,)
    assert result.detail == "duplicate=76"


def test_base_inaccesible_con_centinela_no_inventa_estado():
    result = classify_night(
        _evidence(None, launched=True, db_reachable=False, wrapper_exits={"run-night": 0})
    )

    assert result.causes == (NightCause.DB_UNREACHABLE,)


def test_base_inaccesible_sin_otra_evidencia():
    result = classify_night(_evidence(None, launched=False, db_reachable=False))

    assert result.causes == (NightCause.DB_UNREACHABLE,)


def test_centinela_sin_codigo_es_unclassified():
    assert classify_night(_evidence(None, launched=True)).causes == (NightCause.UNCLASSIFIED,)


# --- is_night_run y target_night ----------------------------------------------------------


def _run(notes: str, status: RunStatus = RunStatus.PARTIAL) -> Run:
    run = Run(started_at=datetime(2026, 11, 5, 0, 5, tzinfo=UTC), budget_tokens=300_000)
    if status is not RunStatus.RUNNING:
        run.finish(status, datetime(2026, 11, 5, 1, 0, tzinfo=UTC), notes)
    else:
        run.notes = notes
    return run


def test_is_night_run_acepta_notas_de_noche_fallidos_huerfanos_y_running():
    assert is_night_run(_run(_BASE + " | tokens=1 | end=edited", RunStatus.COMPLETED))
    assert is_night_run(_run("", RunStatus.FAILED))
    assert is_night_run(_run(ORPHAN_NOTE, RunStatus.KILLED))
    assert is_night_run(_run("", RunStatus.RUNNING))


@pytest.mark.parametrize("status", [RunStatus.COMPLETED, RunStatus.PARTIAL, RunStatus.KILLED])
def test_is_night_run_excluye_relecturas_y_run_item(status):
    assert not is_night_run(_run("reread", status))
    assert not is_night_run(_run("", status))


def test_nota_de_huerfano_es_la_del_cli():
    from nocturna import cli  # noqa: PLC0415

    assert cli.ORPHAN_NOTE == ORPHAN_NOTE


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (datetime(2026, 11, 5, 4, 44), "2026-11-04"),
        (datetime(2026, 11, 5, 4, 45), "2026-11-05"),
        (datetime(2026, 11, 5, 7, 30), "2026-11-05"),
        (datetime(2026, 11, 5, 0, 1), "2026-11-04"),
    ],
)
def test_target_night(now, expected):
    assert target_night(now, time(4, 45)).isoformat() == expected
