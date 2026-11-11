"""T110.a: reglas de (a), (b) y (e) del cierre de la fase 3 (ADR 0052). Dominio puro.

Los umbrales (13, 12, 14), la hora limite (09:00) y la primera comprobacion automatica del dia
se fijan aqui antes de medir. Nombres en espanol.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from helpers.phase3 import (
    FIRST,
    MADRID,
    after_window,
    at,
    days,
    delivery,
    perfect_window,
    probe,
)

from nocturna.domain.phase3 import (
    A_MIN,
    B_MIN,
    E_MIN,
    WINDOW_DAYS,
    Verdict,
    evaluate_phase3,
    format_probe_line,
    parse_delivery_line,
    parse_probe_line,
    parse_site_html,
)

ROOT = Path(__file__).resolve().parents[2]


def _eval(probes, deliveries, finished, *, n=14, now=None, first=FIRST):
    return evaluate_phase3(
        first, n, probes, deliveries, finished, MADRID, now or after_window(first, n)
    )


def _one_day(probes, finished=(), deliveries=(), day=FIRST):
    return _eval(probes, deliveries, list(finished), n=1, first=day, now=at(day, 9, 0, 1))


def test_los_umbrales_fijados_por_el_adr():
    assert (A_MIN, B_MIN, E_MIN, WINDOW_DAYS) == (13, 12, 14, 14)


def test_la_ventana_perfecta_cumple_los_tres_criterios():
    m = _eval(*perfect_window())

    assert (m.a.count, m.b.count, m.e.count) == (14, 14, 14)
    assert m.status is Verdict.CUMPLE


# --- (a) ---------------------------------------------------------------------------------


def _break_a(probes, indexes):
    out = list(probes)
    for i in indexes:
        out[i] = probe(out[i].checked_at, http=503, footer=out[i].footer_updated_at)
    return out


def test_a_13_dias_ok_de_14_cumple():
    probes, deliveries, finished = perfect_window()

    m = _eval(_break_a(probes, [3]), deliveries, finished)

    assert m.a.count == 13
    assert m.status is Verdict.CUMPLE


def test_a_12_dias_ok_de_14_no_cumple():
    probes, deliveries, finished = perfect_window()

    m = _eval(_break_a(probes, [3, 9]), deliveries, finished)

    assert m.a.count == 12
    assert m.status is Verdict.NO_CUMPLE


def test_a_un_dia_sin_comprobacion_falla():
    probes, deliveries, finished = perfect_window()
    del probes[5]

    m = _eval(probes, deliveries, finished)

    assert m.a.days[5].ok is False
    assert m.a.count == 13


def test_a_decide_la_primera_automatica_del_dia_y_una_posterior_ok_no_rescata():
    first_fails = probe(at(FIRST, 1), http=503)
    later_ok = probe(at(FIRST, 7), footer=at(FIRST, 4))

    m = _one_day([later_ok, first_fails])  # desordenadas a proposito

    assert m.a.days[0].ok is False


def test_a_una_primera_ok_no_se_estropea_por_una_posterior_fallida():
    m = _one_day([probe(at(FIRST, 1)), probe(at(FIRST, 7), http=503)])

    assert m.a.days[0].ok is True


def test_a_una_comprobacion_manual_no_cuenta_ni_para_bien_ni_para_mal():
    only_manual = _one_day([probe(at(FIRST, 6), trigger="manual")])
    manual_before_failing_auto = _one_day(
        [probe(at(FIRST, 5), trigger="manual"), probe(at(FIRST, 6), http=503)]
    )
    failing_manual_before_ok_auto = _one_day(
        [probe(at(FIRST, 5), trigger="manual", http=503), probe(at(FIRST, 6))]
    )

    assert only_manual.a.days[0].ok is False
    assert manual_before_failing_auto.a.days[0].ok is False
    assert failing_manual_before_ok_auto.a.days[0].ok is True


@pytest.mark.parametrize(
    "bad",
    [
        {"tls": False},
        {"http": 503},
        {"http": 301},
        {"http": None, "error": "ConnectTimeout"},
        {"noindex": True},
        {"error": "ProbeBodyTooLarge"},
    ],
    ids=["tls", "503", "301-no-se-sigue", "http-guion", "noindex", "cuerpo-demasiado-grande"],
)
def test_a_tls_fallido_503_sin_http_o_noindex_fallan(bad):
    m = _one_day([probe(at(FIRST, 6), **bad)])

    assert m.a.days[0].ok is False


def test_a_cuerpo_demasiado_grande_con_200_falla_con_la_clase_en_el_motivo():
    m = _one_day([probe(at(FIRST, 6), http=200, tls=True, error="ProbeBodyTooLarge")])

    assert m.a.days[0].ok is False
    assert m.a.days[0].reason == "error: ProbeBodyTooLarge"


def test_a_el_dia_es_el_dia_local_no_el_utc_y_respeta_el_cambio_de_hora():
    day = date(2026, 3, 29)  # 29-mar-2026: pasa de CET a CEST a las 02:00 locales
    late_previous_day = probe(datetime(2026, 3, 28, 22, 30, tzinfo=UTC))  # 23:30 CET del 28
    early_target_day = probe(datetime(2026, 3, 28, 23, 30, tzinfo=UTC), http=503)  # 00:30 del 29

    m = _one_day([late_previous_day, early_target_day], day=day)

    assert m.a.days[0].ok is False  # solo cuenta la del 29, que es un 503


# --- (b) ---------------------------------------------------------------------------------


def test_b_una_comprobacion_a_las_08_59_59_con_el_pie_al_dia_cumple_y_a_las_09_00_00_falla():
    finished = [at(FIRST, 4)]
    footer = at(FIRST, 4, 30)

    before = _one_day([probe(at(FIRST, 8, 59, 59), footer=footer)], finished)
    at_limit = _one_day([probe(at(FIRST, 9, 0, 0), footer=footer)], finished)

    assert before.b.days[0].ok is True
    assert at_limit.b.days[0].ok is False


def test_b_el_pie_anterior_al_ultimo_run_falla():
    m = _one_day([probe(at(FIRST, 6), footer=at(FIRST, 3, 59, 59))], [at(FIRST, 4)])

    assert m.b.days[0].ok is False
    assert "pie anterior" in m.b.days[0].reason


def test_b_el_pie_igual_al_cierre_del_run_cumple():
    m = _one_day([probe(at(FIRST, 6), footer=at(FIRST, 4))], [at(FIRST, 4)])

    assert m.b.days[0].ok is True


def test_b_sin_fecha_en_el_pie_falla():
    m = _one_day([probe(at(FIRST, 6), footer=None)], [at(FIRST, 4)])

    assert m.b.days[0].ok is False
    assert "sin fecha" in m.b.days[0].reason


def test_b_sin_comprobacion_antes_de_las_09_00_falla():
    m = _one_day([probe(at(FIRST, 9, 30), footer=at(FIRST, 4, 30))], [at(FIRST, 4)])

    assert m.b.days[0].ok is False


def test_b_con_varios_run_manda_el_de_mayor_finished_at_anterior_a_la_comprobacion():
    finished = [at(FIRST, 1), at(FIRST, 4), at(FIRST, 7, 30)]  # el ultimo es posterior
    stale = _one_day([probe(at(FIRST, 7), footer=at(FIRST, 2))], finished)
    fresh = _one_day([probe(at(FIRST, 7), footer=at(FIRST, 4))], finished)

    assert stale.b.days[0].ok is False  # hay un Run de las 04:00 que el pie no refleja
    assert fresh.b.days[0].ok is True  # el Run de las 07:30 es posterior: no cuenta


def test_b_una_noche_sin_run_cumple_si_el_pie_refleja_el_run_previo():
    previous = FIRST - timedelta(days=1)
    finished = [at(previous, 4)]

    ok = _one_day([probe(at(FIRST, 6), footer=at(previous, 4, 30))], finished)
    stale = _one_day([probe(at(FIRST, 6), footer=at(previous, 3))], finished)

    assert ok.b.days[0].ok is True
    assert stale.b.days[0].ok is False


def test_b_sin_ningun_run_previo_falla_en_vez_de_cumplir_en_vacio():
    m = _one_day([probe(at(FIRST, 6), footer=at(FIRST, 4))], [])

    assert m.b.days[0].ok is False


def test_b_12_dias_cumplidos_de_14_cumplen_y_11_no():
    probes, deliveries, finished = perfect_window()

    def stale(indexes):
        out = list(probes)
        for i in indexes:
            out[i] = probe(out[i].checked_at, footer=at(days()[i], 3))
        return out

    twelve = _eval(stale([2, 8]), deliveries, finished)
    eleven = _eval(stale([2, 8, 11]), deliveries, finished)

    assert twelve.b.count == 12 and twelve.status is Verdict.CUMPLE
    assert eleven.b.count == 11 and eleven.status is Verdict.NO_CUMPLE


@pytest.mark.parametrize(
    ("day", "limit_utc"),
    [
        (date(2026, 3, 29), datetime(2026, 3, 29, 7, 0, tzinfo=UTC)),  # CEST: 09:00 = 07:00Z
        (date(2026, 10, 25), datetime(2026, 10, 25, 8, 0, tzinfo=UTC)),  # CET: 09:00 = 08:00Z
    ],
    ids=["cambio-de-marzo", "cambio-de-octubre"],
)
def test_b_el_limite_de_las_09_00_es_hora_local_en_los_dias_de_cambio_de_hora(day, limit_utc):
    finished = [datetime.combine(day, time(0, 30), tzinfo=UTC)]
    footer = limit_utc - timedelta(hours=6)

    just_before = _one_day(
        [probe(limit_utc - timedelta(seconds=1), footer=footer)], finished, day=day
    )
    at_limit = _one_day([probe(limit_utc, footer=footer)], finished, day=day)

    assert just_before.b.days[0].ok is True
    assert at_limit.b.days[0].ok is False


def test_la_ventana_se_completa_a_las_09_00_locales_aunque_haya_cambio_de_hora():
    first = date(2026, 10, 19)  # N+13 = 1-nov, despues del cambio del 25-oct
    probes, deliveries, finished = perfect_window(first)
    complete = datetime(2026, 11, 1, 9, 0, tzinfo=MADRID)

    assert _eval(probes, deliveries, finished, first=first, now=complete).complete_at == complete


# --- (e) ---------------------------------------------------------------------------------


def _e(deliveries, night=FIRST):
    return _one_day([], deliveries=deliveries, day=night).e.days[0]


@pytest.mark.parametrize("notified", ["fail", "skip"])
def test_e_completed_solo_con_notified_fail_o_skip_falla(notified):
    assert _e([delivery(FIRST, notified=notified)]).ok is False


def test_e_completed_con_un_fallo_y_luego_un_ok_cumple():
    assert _e([delivery(FIRST, notified="fail"), delivery(FIRST, notified="ok")]).ok is True


def test_e_partial_con_ok_antes_de_las_09_00_y_causa_clasificada_cumple():
    line = delivery(FIRST, status="partial", causes="rate_limited", generated=at(FIRST, 8, 59, 59))

    assert _e([line]).ok is True


def test_e_partial_con_ok_a_las_09_00_o_despues_falla():
    line = delivery(FIRST, status="partial", causes="rate_limited", generated=at(FIRST, 9, 0, 0))

    assert _e([line]).ok is False


@pytest.mark.parametrize("causes", ["unclassified", "-"])
def test_e_no_completed_con_unclassified_o_sin_causa_falla(causes):
    assert _e([delivery(FIRST, status="failed", causes=causes)]).ok is False


def test_e_no_completed_con_unclassified_mas_otra_causa_cumple():
    assert _e([delivery(FIRST, status="failed", causes="unclassified,x")]).ok is True


def test_e_una_noche_sin_linea_falla():
    assert _e([delivery(FIRST - timedelta(days=1))]).ok is False


def test_e_sin_run_antes_de_las_09_00_con_causa_cumple_y_sin_causa_falla():
    ok = delivery(FIRST, status="sin-run", causes="not_launched")
    sin_causa = delivery(FIRST, status="sin-run", causes="-")

    assert _e([ok]).ok is True
    assert _e([sin_causa]).ok is False


def test_e_la_causa_tardia_no_rescata_a_la_temprana_sin_clasificar():
    early = delivery(FIRST, status="failed", causes="unclassified", generated=at(FIRST, 7))
    late = delivery(FIRST, status="failed", causes="rate_limited", generated=at(FIRST, 10))

    assert _e([early, late]).ok is False


def test_e_la_causa_y_el_aviso_ok_deben_ir_en_la_misma_linea():
    cause_but_fail = delivery(
        FIRST, status="failed", causes="rate_limited", notified="fail", generated=at(FIRST, 7)
    )
    ok_but_unclassified = delivery(
        FIRST, status="failed", causes="unclassified", notified="ok", generated=at(FIRST, 8)
    )
    both = delivery(FIRST, status="failed", causes="rate_limited", notified="ok")

    assert _e([cause_but_fail, ok_but_unclassified]).ok is False
    assert _e([cause_but_fail, ok_but_unclassified, both]).ok is True


def test_e_las_14_noches_de_14_cumplen_y_13_no():
    probes, deliveries, finished = perfect_window()

    full = _eval(probes, deliveries, finished)
    thirteen = _eval(probes, deliveries[:-1], finished)

    assert full.e.count == 14 and full.status is Verdict.CUMPLE
    assert thirteen.e.count == 13 and thirteen.status is Verdict.NO_CUMPLE


# --- ventana -----------------------------------------------------------------------------


def test_antes_de_las_09_00_de_n_mas_13_da_incompleta_y_nunca_cumple():
    probes, deliveries, finished = perfect_window()
    last = FIRST + timedelta(days=13)

    early = _eval(probes, deliveries, finished, now=at(last, 8, 59, 59))
    on_time = _eval(probes, deliveries, finished, now=at(last, 9, 0, 0))

    assert early.status is Verdict.INCOMPLETA
    assert on_time.status is Verdict.CUMPLE


def test_una_ventana_a_mitad_de_camino_es_incompleta():
    probes, deliveries, finished = perfect_window()

    m = _eval(probes[:5], deliveries[:5], finished, now=at(FIRST + timedelta(days=4), 12))

    assert m.status is Verdict.INCOMPLETA


# --- lineas ------------------------------------------------------------------------------


def test_ida_y_vuelta_de_la_linea_de_comprobacion():
    original = probe(at(FIRST, 6, 1, 2), http=200, footer=at(FIRST, 4, 30))

    line = format_probe_line(original)

    assert line == (
        "checked_at=2026-11-02T06:01:02+01:00 trigger=scheduled http=200 tls=ok error=- "
        "noindex=no footer=2026-11-02T04:30:00+01:00"
    )
    assert parse_probe_line(line) == original


def test_ida_y_vuelta_con_todos_los_opcionales_a_none():
    original = probe(
        at(FIRST, 6), http=None, tls=False, error="ConnectError", noindex=True, footer=None
    )

    line = format_probe_line(original)

    assert "http=- tls=fail error=ConnectError noindex=yes footer=-" in line
    assert parse_probe_line(line) == original


@pytest.mark.parametrize(
    "key", ["checked_at", "trigger", "http", "tls", "error", "noindex", "footer"]
)
def test_una_linea_de_comprobacion_con_una_clave_de_menos_da_none(key):
    line = format_probe_line(probe(at(FIRST, 6), footer=at(FIRST, 4)))
    without = " ".join(t for t in line.split() if not t.startswith(f"{key}="))

    assert parse_probe_line(without) is None


_P = "checked_at=2026-11-02T06:00:00+01:00 trigger=s http=200 tls=ok error=- noindex=no footer=-"


@pytest.mark.parametrize(
    "garbage",
    [
        "",
        "basura",
        _P.replace("2026-11-02T06:00:00+01:00", "ayer"),
        _P.replace("+01:00", ""),  # sin zona horaria
        _P.replace("http=200", "http=2x"),
        _P.replace("tls=ok", "tls=quizas"),
        _P.replace("footer=-", "footer=ayer"),
    ],
)
def test_las_lineas_basura_se_ignoran(garbage):
    assert parse_probe_line(garbage) is None
    assert parse_delivery_line(garbage) is None


def test_la_linea_valida_de_referencia_si_se_parsea():
    assert parse_probe_line(_P) is not None


# --- contratos ---------------------------------------------------------------------------


def test_una_linea_real_de_delivery_line_se_parsea_con_parse_delivery_line():
    from test_morning_report import NIGHT, NOTES_AUTH_ARXIV, _build, _Data, _run

    from nocturna.application.use_cases.morning_report import delivery_line
    from nocturna.domain.entities import RunStatus

    run = _run(NIGHT, RunStatus.PARTIAL, NOTES_AUTH_ARXIV)
    line = delivery_line(_build(_Data([run])), "ok")

    parsed = parse_delivery_line(line)

    assert parsed is not None
    assert (parsed.night, parsed.trigger, parsed.status) == (NIGHT, "scheduled", "partial")
    assert parsed.causes == ("auth_failed", "arxiv_unavailable")
    assert parsed.notified == "ok"
    assert parsed.generated_at.utcoffset() is not None


def test_una_linea_sin_causas_y_sin_run_se_parsea():
    parsed = parse_delivery_line(
        "2026-11-05T07:30:00+01:00 2026-11-05 run-night sin-run - notified=skip"
    )

    assert parsed is not None and parsed.causes == () and parsed.status == "sin-run"


def test_el_pie_de_la_web_conserva_el_formato_que_lee_el_instrumento():
    source = (ROOT / "web" / "src" / "components" / "SiteFooter.tsx").read_text()

    assert "Datos actualizados el" in source
    assert "<time dateTime=" in source


_PAGE = """<!doctype html><html><head><title>Nocturna</title></head><body>
<main>otro <time datetime="2020-01-01T00:00:00Z">viejo</time></main>
<footer><p>Datos actualizados el <time dateTime="{stamp}">{label}</time></p></footer>
</body></html>"""


@pytest.mark.parametrize(
    ("stamp", "expected"),
    [
        ("2026-11-02T03:41:10Z", datetime(2026, 11, 2, 3, 41, 10, tzinfo=UTC)),
        ("2026-11-02T03:41:10.123456+00:00", datetime(2026, 11, 2, 3, 41, 10, 123456, tzinfo=UTC)),
        ("2026-11-02T04:41:10+01:00", datetime(2026, 11, 2, 3, 41, 10, tzinfo=UTC)),
    ],
    ids=["Z", "microsegundos", "offset"],
)
def test_parse_site_html_lee_la_fecha_del_pie_con_z_y_con_offset(stamp, expected):
    noindex, footer = parse_site_html(_PAGE.format(stamp=stamp, label="2 nov"))

    assert noindex is False
    assert footer == expected


def test_parse_site_html_ignora_los_time_fuera_del_pie():
    html = '<main><time datetime="2026-11-02T03:00:00Z">x</time></main><footer>sin fecha</footer>'

    assert parse_site_html(html) == (False, None)


@pytest.mark.parametrize("stamp", ["ayer", "2026-11-02T03:41:10", ""])
def test_parse_site_html_sin_fecha_valida_o_sin_zona_da_none(stamp):
    assert parse_site_html(_PAGE.format(stamp=stamp, label="x"))[1] is None


@pytest.mark.parametrize(
    "meta",
    [
        '<meta name="robots" content="noindex">',
        '<meta content="noindex, nofollow" name="robots">',
        '<META NAME="ROBOTS" CONTENT="NoIndex">',
        "<meta name='robots' content='index, noindex'/>",
        '<meta  name = "robots"  data-x="1" content="noindex" >',
    ],
)
def test_parse_site_html_detecta_el_meta_robots_noindex_en_cualquier_orden_y_caso(meta):
    html = f"<html><head>{meta}</head><body></body></html>"

    assert parse_site_html(html)[0] is True


@pytest.mark.parametrize(
    "meta",
    [
        '<meta name="robots" content="index, follow">',
        '<meta name="description" content="noindex en el texto">',
        '<!-- <meta name="robots" content="noindex"> -->',
    ],
    ids=["index", "otro-meta", "solo-en-comentario"],
)
def test_parse_site_html_no_cuenta_noindex_que_no_es_un_meta_robots_real(meta):
    assert parse_site_html(f"<head>{meta}</head>")[0] is False


def test_parse_site_html_con_html_roto_no_lanza():
    noindex, footer = parse_site_html("<footer><time dateTime='2026-11-02T03:00:00Z'><meta name=")

    assert noindex is False
    assert footer == datetime(2026, 11, 2, 3, 0, tzinfo=UTC)


# --- "ya no puede cumplir" -------------------------------------------------------------------

_NOW = at(FIRST + timedelta(days=8), 10)  # ventana incompleta; dias 0..7 cerrados


def _measure_incomplete(*, probes_from=0, deliveries_from=0):
    probes, deliveries, finished = perfect_window()
    return evaluate_phase3(
        FIRST, 14, probes[probes_from:], deliveries[deliveries_from:], finished, MADRID, _NOW
    )


def test_cannot_comply_vacio_si_todo_va_bien_en_ventana_incompleta():
    result = _measure_incomplete()

    assert result.status is Verdict.INCOMPLETA
    assert result.cannot_comply == ()


def test_cannot_comply_e_con_un_fallo_cerrado():
    assert _measure_incomplete(deliveries_from=1).cannot_comply == ("e",)


def test_cannot_comply_a_con_dos_fallos_y_no_con_uno():
    assert "a" not in _measure_incomplete(probes_from=1).cannot_comply
    assert "a" in _measure_incomplete(probes_from=2).cannot_comply


def test_cannot_comply_b_con_tres_fallos_y_no_con_dos():
    assert "b" not in _measure_incomplete(probes_from=2).cannot_comply
    assert "b" in _measure_incomplete(probes_from=3).cannot_comply


def test_cannot_comply_ignora_los_dias_que_aun_no_han_cerrado():
    probes, deliveries, finished = perfect_window()
    kept = [d for d in deliveries if d.night < FIRST + timedelta(days=10)]  # faltan dias futuros

    result = evaluate_phase3(FIRST, 14, probes, kept, finished, MADRID, _NOW)

    assert result.cannot_comply == ()
