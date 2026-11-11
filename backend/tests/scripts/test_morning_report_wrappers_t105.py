"""T105: el informe matinal nunca cambia el código de salida de los envoltorios.

Complementa los `test_t105_*` de `test_run_night_scheduled.py` y
`test_archive_snapshot_scheduled.py`: allí el `uv` falso devuelve el mismo código para la noche y
para el informe, lo que no distingue un envoltorio que propaga el código del informe. Aquí cada
uno tiene el suyo.
"""

from __future__ import annotations

import itertools
from datetime import date

import pytest
from test_archive_snapshot_scheduled import Env as ArchiveEnv
from test_archive_snapshot_scheduled import _install as install_archive
from test_run_night_scheduled import _base_env, _run_wrapper, _setup_env, _status_lines

_FAKE_UV_ARCHIVE = """#!/usr/bin/env bash
case "$*" in
    *morning-report*)
        printf '%s\\n' "$*" >> "$UV_REPORT_LOG_FILE"
        exit "${UV_REPORT_EXIT_CODE:-0}" ;;
esac
printf '%s\\n' "$*" >> "$UV_CALL_LOG_FILE"
exit "${UV_EXIT_CODE:-0}"
"""


@pytest.mark.parametrize(
    ("night_code", "report_code"), list(itertools.product([0, 7, 8, 1], [0, 1, 5]))
)
def test_run_night_el_codigo_de_salida_es_el_de_la_noche_aunque_el_informe_falle(
    tmp_path, night_code, report_code
):
    home, bin_dir = _setup_env(tmp_path)
    report_log = tmp_path / "report-calls.log"
    env = _base_env(
        home,
        bin_dir,
        extra={
            "UV_EXIT_CODE": str(night_code),
            "UV_EVALUATE_EXIT_CODE": "0",
            "UV_REPORT_LOG_FILE": str(report_log),
            "UV_REPORT_EXIT_CODE": str(report_code),
        },
    )

    result = _run_wrapper([], env, cwd=tmp_path)

    assert result.returncode == night_code
    assert _status_lines(home) == [
        f"run-night={night_code}",
        "evaluate-tensions=0",
        "sync=78",
        "probe=0",
    ]
    assert len(report_log.read_text().splitlines()) == 1


@pytest.mark.parametrize("report_code", [0, 1, 5])
def test_archive_snapshot_el_codigo_es_el_del_snapshot_aunque_el_informe_falle(
    tmp_path, report_code
):
    env = ArchiveEnv(tmp_path)
    install_archive(env.bin / "uv", _FAKE_UV_ARCHIVE)
    report_log = tmp_path / "report-calls.log"

    result = env.run(
        "--full",
        UV_EXIT_CODE="3",
        UV_REPORT_EXIT_CODE=str(report_code),
        UV_REPORT_LOG_FILE=str(report_log),
    )

    assert result.returncode == 3
    assert report_log.read_text().splitlines() == [
        "run --frozen nocturna morning-report --trigger archive-snapshot "
        f"--night {date.today().isoformat()}"
    ]
    status = next(env.logs.glob("archive-*.status"))
    assert status.read_text() == "archive-snapshot=3\nsync=78\n"


def test_archive_snapshot_con_exito_y_informe_fallido_sale_0(tmp_path):
    env = ArchiveEnv(tmp_path)
    install_archive(env.bin / "uv", _FAKE_UV_ARCHIVE)

    result = env.run(
        UV_EXIT_CODE="0",
        UV_REPORT_EXIT_CODE="1",
        UV_REPORT_LOG_FILE=str(tmp_path / "report-calls.log"),
    )

    assert result.returncode == 0
