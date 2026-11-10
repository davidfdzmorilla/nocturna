"""T108 (F): la linea «Sincronizacion con el VPS» del informe matinal.

Cuatro variantes: correcta, fallida (codigo N), no configurada (78) y no ejecutada (sin `sync=`).
Informativa: no cambia la causa, el estado ni el aviso de la noche. Sin base de datos ni Claude.
"""

from __future__ import annotations

from datetime import time

import pytest
from test_morning_report import NIGHT, NOTES_ARXIV, TZ, _Clock, _Data, _Log, _run

from nocturna.application.use_cases.morning_report import (
    BuildMorningReport,
    LaunchEvidence,
    Trigger,
    render_report,
    should_notify,
)
from nocturna.domain.entities import RunStatus

PREFIX = "Sincronización con el VPS: "


def _build(evidence: LaunchEvidence, trigger: Trigger, *, with_run: bool = True):
    runs = [_run(NIGHT, RunStatus.COMPLETED, "")] if with_run else []
    return BuildMorningReport(_Data(runs), _Log(evidence), _Clock(), TZ, time(0, 0), time(4, 45))(
        NIGHT, trigger
    )


def _sync_line(report) -> str:
    lines = [ln for ln in render_report(report).splitlines() if ln.startswith(PREFIX)]
    assert len(lines) == 1, lines
    return lines[0]


def _night(**exits: int) -> LaunchEvidence:
    return LaunchEvidence(launched=True, wrapper_exits={"run-night": 0, **exits})


def _archive(**exits: int) -> LaunchEvidence:
    return LaunchEvidence(launched=True, archive_exits={"archive-snapshot": 0, **exits})


@pytest.mark.parametrize(
    ("exit_code", "expected"),
    [
        (0, "correcta"),
        (2, "fallida (código 2)"),
        (3, "fallida: tiempo agotado"),
        (13, "fallida (código 13)"),
        (15, "fallida: otra restauración en curso"),
        (17, "fallida (código 17)"),
        (75, "fallida (código 75)"),
        (77, "fallida (código 77)"),
        (78, "no configurada"),
    ],
)
@pytest.mark.parametrize(
    ("trigger", "make"),
    [
        (Trigger.RUN_NIGHT, _night),
        (Trigger.SCHEDULED, _night),
        (Trigger.ARCHIVE_SNAPSHOT, _archive),
    ],
)
def test_las_variantes_de_la_linea(trigger, make, exit_code, expected):
    report = _build(make(sync=exit_code), trigger)

    assert _sync_line(report) == PREFIX + expected


@pytest.mark.parametrize(
    ("trigger", "make"),
    [
        (Trigger.RUN_NIGHT, _night),
        (Trigger.ARCHIVE_SNAPSHOT, _archive),
    ],
)
def test_sin_sync_en_el_status_es_no_ejecutada(trigger, make):
    assert _sync_line(_build(make(), trigger)) == PREFIX + "no ejecutada"


def test_sin_envoltorio_lanzado_es_no_ejecutada():
    report = _build(LaunchEvidence(launched=False), Trigger.SCHEDULED)

    assert _sync_line(report) == PREFIX + "no ejecutada"


def test_el_codigo_78_no_se_muestra_como_fallida():
    text = render_report(_build(_night(sync=78), Trigger.RUN_NIGHT))

    assert "fallida (código 78)" not in text
    assert "no configurada" in text


def test_el_sync_del_otro_envoltorio_sirve_de_respaldo():
    # Informe del snapshot sin `sync=` en archive-*.status pero con el de la noche.
    evidence = LaunchEvidence(
        launched=True,
        wrapper_exits={"run-night": 0, "sync": 0},
        archive_exits={"archive-snapshot": 0},
    )

    assert _sync_line(_build(evidence, Trigger.ARCHIVE_SNAPSHOT)) == PREFIX + "correcta (run-night)"


def test_el_origen_del_respaldo_es_archive_snapshot_si_dispara_la_noche():
    evidence = LaunchEvidence(
        launched=True,
        wrapper_exits={"run-night": 0},
        archive_exits={"archive-snapshot": 0, "sync": 3},
    )

    assert _sync_line(_build(evidence, Trigger.RUN_NIGHT)) == (
        PREFIX + "fallida: tiempo agotado (archive-snapshot)"
    )
    assert _sync_line(_build(evidence, Trigger.SCHEDULED)) == (
        PREFIX + "fallida: tiempo agotado (archive-snapshot)"
    )


def test_con_ambos_el_del_disparador_manda():
    evidence = LaunchEvidence(
        launched=True,
        wrapper_exits={"run-night": 0, "sync": 13},
        archive_exits={"archive-snapshot": 0, "sync": 0},
    )

    assert _sync_line(_build(evidence, Trigger.ARCHIVE_SNAPSHOT)) == PREFIX + "correcta"
    assert _sync_line(_build(evidence, Trigger.RUN_NIGHT)) == PREFIX + "fallida (código 13)"


def test_la_linea_sale_sin_run_de_la_noche():
    report = _build(_night(sync=2), Trigger.RUN_NIGHT, with_run=False)

    assert _sync_line(report) == PREFIX + "fallida (código 2)"


@pytest.mark.parametrize("sync", [0, 2, 13, 78])
def test_la_sincronizacion_no_cambia_causa_estado_ni_aviso(sync):
    base = _build(_night(), Trigger.RUN_NIGHT)
    other = _build(_night(sync=sync), Trigger.RUN_NIGHT)

    assert other.classification == base.classification
    for delivered in (True, False):
        assert should_notify(other, delivered) == should_notify(base, delivered)
    assert other.run_status == base.run_status


def test_la_linea_cabe_en_100_columnas_y_no_filtra_rutas():
    text = render_report(_build(_night(sync=13), Trigger.RUN_NIGHT))

    assert all(len(ln) <= 100 for ln in text.splitlines())
    assert "/" not in _sync_line(_build(_night(sync=13), Trigger.RUN_NIGHT))


def test_noche_con_causa_sigue_mostrando_la_linea():
    run = _run(NIGHT, RunStatus.PARTIAL, NOTES_ARXIV)
    report = BuildMorningReport(
        _Data([run]), _Log(_night(sync=13)), _Clock(), TZ, time(0, 0), time(4, 45)
    )(NIGHT, Trigger.RUN_NIGHT)

    assert _sync_line(report) == PREFIX + "fallida (código 13)"


@pytest.mark.parametrize(
    ("status_file", "trigger", "content", "expected"),
    [
        (
            "night-20261105.status",
            Trigger.RUN_NIGHT,
            "run-night=0\nsync=13\n",
            "fallida (código 13)",
        ),
        (
            "archive-20261105.status",
            Trigger.ARCHIVE_SNAPSHOT,
            "archive-snapshot=0\nsync=0\n",
            "correcta",
        ),
        ("night-20261105.status", Trigger.RUN_NIGHT, "run-night=0\nsync=78\n", "no configurada"),
        ("night-20261105.status", Trigger.RUN_NIGHT, "run-night=0\n", "no ejecutada"),
    ],
)
def test_de_extremo_a_extremo_con_el_fichero_status_real(
    tmp_path, status_file, trigger, content, expected
):
    from nocturna.infrastructure.ops.launch_log import FileLaunchLog

    (tmp_path / ".launched-20261105").write_text("x")
    (tmp_path / status_file).write_text(content)
    evidence = FileLaunchLog(tmp_path).evidence(NIGHT)

    assert _sync_line(_build(evidence, trigger)) == PREFIX + expected
