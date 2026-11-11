"""T110.a: `MeasurePhase3` y `nocturna phase3-check` (ADR 0052), con la base sustituida.

La variante contra PostgreSQL (solo SELECT) esta en `tests/db/test_phase3_check_db.py`.
Nombres en espanol.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta

import pytest
from fakes.clock import FakeClock
from helpers.phase3 import (
    FIRST,
    MADRID,
    after_window,
    at,
    delivery,
    night_run,
    perfect_window,
    probe,
    write_delivery_log,
    write_probe_log,
)

from nocturna import cli
from nocturna.application.use_cases.phase3 import MeasurePhase3
from nocturna.domain.entities import Run, RunStatus
from nocturna.domain.night_report import is_night_run
from nocturna.domain.phase3 import Verdict


class _Runs:
    def __init__(self, runs: list[Run]) -> None:
        self.runs = runs
        self.requested: list[tuple[datetime, datetime]] = []

    def started_between(self, start: datetime, end: datetime) -> list[Run]:
        self.requested.append((start, end))
        return [r for r in self.runs if start <= r.started_at <= end]

    def last_night_run_finished_before(self, before: datetime) -> Run | None:
        older = sorted(
            (
                r
                for r in self.runs
                if r.started_at < before and is_night_run(r) and r.finished_at is not None
            ),
            key=lambda r: r.started_at,
        )
        return older[-1] if older else None


class _Probes:
    def __init__(self, probes) -> None:
        self._probes = probes

    def read(self):
        return self._probes


class _Deliveries:
    def __init__(self, deliveries) -> None:
        self._deliveries = deliveries

    def read(self):
        return self._deliveries


def _measure(probes, deliveries, runs, now):
    source = _Runs(runs)
    use_case = MeasurePhase3(
        _Probes(probes), _Deliveries(deliveries), source, FakeClock(now), MADRID
    )
    return use_case, source


def test_solo_los_run_de_noche_fijan_el_suelo_de_b_y_el_de_run_item_no_cuenta():
    night = night_run(FIRST, at(FIRST, 4))
    run_item = Run(started_at=at(FIRST, 5, 30), budget_tokens=300_000)  # `run-item`: sin notas
    run_item.finish(RunStatus.COMPLETED, at(FIRST, 6, 30), None)
    # el pie (04:30) refleja el Run de noche; el de run-item termino despues y no debe exigirse
    probes = [probe(at(FIRST, 7), footer=at(FIRST, 4, 30))]
    use_case, _ = _measure(probes, [], [night, run_item], after_window(FIRST, 1))

    result = use_case(FIRST, 1)

    assert result.b.days[0].ok is True


def test_una_relectura_tampoco_cuenta_como_run_de_noche():
    night = night_run(FIRST, at(FIRST, 4))
    reread = night_run(FIRST, at(FIRST, 6, 30), notes="reread")
    probes = [probe(at(FIRST, 7), footer=at(FIRST, 4, 30))]
    use_case, _ = _measure(probes, [], [night, reread], after_window(FIRST, 1))

    assert use_case(FIRST, 1).b.days[0].ok is True


def test_un_run_de_noche_posterior_al_pie_si_exige_al_vps_haberlo_reflejado():
    night = night_run(FIRST, at(FIRST, 4))
    probes = [probe(at(FIRST, 7), footer=at(FIRST, 3))]
    use_case, _ = _measure(probes, [], [night], after_window(FIRST, 1))

    assert use_case(FIRST, 1).b.days[0].ok is False


def test_un_run_sin_cerrar_no_aporta_finished_at():
    running = Run(started_at=at(FIRST, 0, 5), budget_tokens=300_000)
    probes = [probe(at(FIRST, 7), footer=at(FIRST, 4))]
    use_case, _ = _measure(probes, [], [running], after_window(FIRST, 1))

    assert use_case(FIRST, 1).b.days[0].ok is False  # sin Run cerrado previo


def test_la_consulta_a_la_base_cubre_desde_el_dia_anterior_hasta_el_dia_despues_de_la_ventana():
    use_case, source = _measure([], [], [], after_window())

    use_case(FIRST, 14)

    assert source.requested == [
        (at(FIRST - timedelta(days=1), 0), at(FIRST + timedelta(days=15), 0))
    ]


def test_con_un_run_de_hace_cinco_dias_y_nada_despues_b_se_compara_con_el_y_no_da_sin_run_previo():
    old = night_run(FIRST - timedelta(days=5), at(FIRST - timedelta(days=5), 4))
    probes = [probe(at(FIRST, 7), footer=at(FIRST - timedelta(days=5), 4, 30))]
    use_case, _ = _measure(probes, [], [old], after_window(FIRST, 1))

    day = use_case(FIRST, 1).b.days[0]

    assert day.reason != "sin Run previo"
    assert day.ok is True  # el pie refleja el Run antiguo


def test_el_run_antiguo_que_no_es_de_noche_no_fija_el_suelo():
    old = night_run(FIRST - timedelta(days=5), at(FIRST - timedelta(days=5), 4), notes="reread")
    probes = [probe(at(FIRST, 7), footer=at(FIRST, 4))]
    use_case, _ = _measure(probes, [], [old], after_window(FIRST, 1))

    assert use_case(FIRST, 1).b.days[0].reason == "sin Run previo"


def test_el_caso_de_uso_completo_cumple_con_una_ventana_perfecta():
    probes, deliveries, finished = perfect_window()
    runs = [night_run(FIRST + timedelta(days=i), f) for i, f in enumerate(finished)]
    use_case, _ = _measure(probes, deliveries, runs, after_window())

    assert use_case(FIRST, 14).status is Verdict.CUMPLE


# --- CLI ---------------------------------------------------------------------------------


@pytest.fixture
def check_env(tmp_path, monkeypatch):
    """Base y reloj falsos para `phase3-check`; el estado se ajusta por test."""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("NOCTURNA_LOG_DIR", str(log_dir))
    state = {"runs": [], "now": after_window(), "engine_kwargs": None, "fail": None}

    def create_engine(settings, **kwargs):
        if state["fail"]:
            raise state["fail"]
        state["engine_kwargs"] = kwargs
        return object()

    @contextmanager
    def unit_of_work(factory, **kwargs):
        state["uow_kwargs"] = kwargs
        yield object()

    class _Data(_Runs):
        def __init__(self, session, tz) -> None:
            super().__init__(state["runs"])

    monkeypatch.setattr(cli, "create_db_engine", create_engine)
    monkeypatch.setattr(cli, "create_session_factory", lambda engine: object())
    monkeypatch.setattr(cli, "unit_of_work", unit_of_work)
    monkeypatch.setattr(cli, "SqlMorningReportData", _Data)
    monkeypatch.setattr(cli, "SystemClock", lambda tz: FakeClock(state["now"]))
    state["log_dir"] = log_dir
    return state


def _fill(state, *, probes=None, deliveries=None, runs=None):
    base_probes, base_deliveries, finished = perfect_window()
    write_probe_log(state["log_dir"] / "phase3-probes.log", probes or base_probes)
    write_delivery_log(state["log_dir"] / "morning-deliveries.log", deliveries or base_deliveries)
    state["runs"] = runs or [
        night_run(FIRST + timedelta(days=i), f) for i, f in enumerate(finished)
    ]


ARGS = ["phase3-check", "--from", FIRST.isoformat()]


def test_cli_sale_0_si_cumple_y_lo_dice(check_env, capsys):
    _fill(check_env)

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 0
    assert "estado: cumple" in out
    assert "(a) 14/14, umbral 13: cumple" in out
    assert "(b) 14/14, umbral 12: cumple" in out
    assert "(e) 14/14, umbral 14: cumple" in out
    assert "(c) y (d) son manuales" in out


def test_cli_sale_3_si_no_cumple_e_indica_el_motivo_del_dia(check_env, capsys):
    probes, deliveries, _ = perfect_window()
    _fill(check_env, deliveries=deliveries[:-1])

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 3
    assert "(e) 13/14, umbral 14: no cumple" in out
    assert "sin línea de entrega" in out


def test_cli_sale_2_si_la_ventana_no_ha_terminado(check_env, capsys):
    _fill(check_env)
    check_env["now"] = at(FIRST + timedelta(days=13), 8, 59, 59)

    code = cli.main(ARGS)

    assert code == 2
    assert "estado: incompleta" in capsys.readouterr().out


def test_cli_sale_1_si_la_base_falla_y_no_filtra_el_dsn(check_env, capsys):
    _fill(check_env)
    check_env["fail"] = RuntimeError("password authentication failed postgresql://u:s3cr3t@h/db")

    code = cli.main(ARGS)

    captured = capsys.readouterr()
    assert code == 1
    assert "RuntimeError" in captured.err
    assert "s3cr3t" not in captured.err + captured.out and "postgresql" not in captured.err


@pytest.mark.parametrize("days", ["0", "-3"])
def test_cli_con_days_invalido_sale_1(check_env, days):
    assert cli.main([*ARGS, "--days", days]) == 1


def test_cli_con_days_menor_de_14_avisa_y_deja_los_codigos(check_env, capsys):
    _fill(check_env)

    code = cli.main([*ARGS, "--days", "7"])

    err = capsys.readouterr().err
    assert "con --days < 14 la ventana no puede cumplir" in err
    assert "13/12/14" in err
    assert code == 3


def test_cli_con_days_mayor_de_14_avisa_y_deja_los_codigos(check_env, capsys):
    _fill(check_env)

    check_env["now"] = at(FIRST + timedelta(days=16), 10)
    code = cli.main([*ARGS, "--days", "15"])

    err = capsys.readouterr().err
    assert "la ventana de ADR 0052 es de 14 días" in err
    assert "no son comparables" in err
    assert code == 0  # el dia 15 sin datos deja 14/15: los codigos no cambian


def test_cli_incompleta_con_e_con_un_fallo_dice_ya_no_puede_cumplir(check_env, capsys):
    _, deliveries, _ = perfect_window()
    _fill(check_env, deliveries=deliveries[1:])  # (e) falla el dia 1
    check_env["now"] = at(FIRST + timedelta(days=5), 8)  # ventana incompleta

    code = cli.main(ARGS)

    out = capsys.readouterr().out
    assert code == 2
    assert "ya no puede cumplir: (e)" in out
    assert "estado: incompleta" in out


def test_cli_incompleta_sin_fallos_cerrados_no_dice_ya_no_puede_cumplir(check_env, capsys):
    _fill(check_env)
    check_env["now"] = at(FIRST + timedelta(days=5), 8)

    code = cli.main(ARGS)

    assert code == 2
    assert "ya no puede cumplir" not in capsys.readouterr().out


def test_cli_14_dias_no_avisa_ni_de_mas_ni_de_menos(check_env, capsys):
    _fill(check_env)

    cli.main(ARGS)

    assert "la ventana de ADR 0052" not in capsys.readouterr().err


def test_cli_con_14_dias_no_avisa(check_env, capsys):
    _fill(check_env)

    cli.main(ARGS)

    assert "--days < 14" not in capsys.readouterr().err


def test_cli_con_nocturna_log_dir_vacio_sale_1(check_env, monkeypatch):
    monkeypatch.setenv("NOCTURNA_LOG_DIR", "")

    assert cli.main(ARGS) == 1


def test_cli_pide_una_sesion_de_solo_lectura_y_sin_commit(check_env):
    _fill(check_env)

    cli.main(ARGS)

    assert check_env["engine_kwargs"]["read_only"] is True
    assert check_env["uow_kwargs"] == {"commit": False}


def test_cli_sin_ficheros_de_registro_no_cumple_pero_no_falla(check_env):
    check_env["runs"] = []

    assert cli.main(ARGS) == 3


def test_cli_ignora_las_lineas_basura_de_los_registros(check_env):
    probes, deliveries, finished = perfect_window()
    runs = [night_run(FIRST + timedelta(days=i), f) for i, f in enumerate(finished)]
    write_probe_log(check_env["log_dir"] / "phase3-probes.log", probes, extra=["basura", ""])
    write_delivery_log(check_env["log_dir"] / "morning-deliveries.log", deliveries)
    with (check_env["log_dir"] / "morning-deliveries.log").open("a") as handle:
        handle.write("=== basura ===\n")
    check_env["runs"] = runs

    assert cli.main(ARGS) == 0


def test_cli_una_comprobacion_manual_no_ayuda_en_el_cli(check_env):
    probes, deliveries, finished = perfect_window()
    manual = [probe(p.checked_at, trigger="manual", footer=p.footer_updated_at) for p in probes]
    _fill(check_env, probes=manual)

    assert cli.main(ARGS) == 3


def test_cli_las_entregas_con_notified_fail_no_cumplen(check_env):
    _, deliveries, _ = perfect_window()
    failed = [delivery(d.night, notified="fail") for d in deliveries]
    _fill(check_env, deliveries=failed)

    assert cli.main(ARGS) == 3
