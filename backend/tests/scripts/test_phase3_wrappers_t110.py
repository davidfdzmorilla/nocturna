"""T110.a: los envoltorios lanzan `phase3-probe` en su sitio y sin cambiar su codigo de salida.

Se ejecutan los `run-night-scheduled.sh` y `morning-report-scheduled.sh` REALES contra `uv`,
`ssh`, `docker`, `caffeinate` y `osascript` falsos. Nunca hay red, Docker ni Claude.
Nombres en espanol.
"""

from __future__ import annotations

import plistlib
import subprocess
import time
from pathlib import Path

import pytest
from helpers import sync_fakes as fk
from test_morning_report import NIGHT as REPORT_NIGHT
from test_morning_report import NOTES_ARXIV, _build, _Data, _run
from test_morning_report_scheduled import Env as ReportEnv
from test_sync_wrappers_t108 import NIGHT as NIGHT_SCRIPT
from test_sync_wrappers_t108 import _make_rig

from nocturna.application.use_cases.morning_report import LaunchEvidence
from nocturna.domain.entities import RunStatus
from nocturna.infrastructure.ops.launch_log import FileLaunchLog

BACKEND = Path(__file__).resolve().parents[2]
MARKER = "__NOCTURNA_PUBLIC_URL__"

# uv: ordenes globales y un codigo por subcomando (el probe, el informe, la noche).
FAKE_UV = """#!/usr/bin/env bash
case "$*" in
    *phase3-probe*) printf 'phase3-probe %s\\n' "$*" >> "${ORDER_LOG:-/dev/null}"
        if [[ -n "${UV_PROBE_IGNORE_TERM:-}" ]]; then
            trap '' TERM; while :; do sleep 1; done
        fi
        if [[ -n "${UV_PROBE_HANG:-}" ]]; then sleep 300; fi
        exit "${UV_PROBE_EXIT_CODE:-0}" ;;
    *morning-report*) printf 'morning-report\\n' >> "${ORDER_LOG:-/dev/null}"
        exit "${UV_REPORT_EXIT_CODE:-0}" ;;
    *evaluate-tensions*) printf 'evaluate-tensions\\n' >> "${ORDER_LOG:-/dev/null}"
        exit "${UV_EVALUATE_EXIT_CODE:-0}" ;;
    *run-night*) printf 'run-night\\n' >> "${ORDER_LOG:-/dev/null}"
        exit "${UV_EXIT_CODE:-0}" ;;
esac
exit 0
"""


@pytest.fixture
def night(tmp_path):
    rig = _make_rig(tmp_path, NIGHT_SCRIPT)
    fk.install_all(tmp_path / "bin", uv=FAKE_UV)
    return rig


def _probe_lines(rig) -> list[str]:
    return [line for line in rig.order if line.startswith("phase3-probe")]


# --- run-night ---------------------------------------------------------------------------


def test_run_night_el_probe_va_tras_el_sync_y_antes_del_informe(night):
    result = night.run()

    assert result.returncode == 0, result.stderr
    steps = [s.split()[0] for s in night.order]
    assert steps == ["run-night", "evaluate-tensions", "ssh", "phase3-probe", "morning-report"]


def test_run_night_el_probe_se_lanza_con_el_disparador_run_night(night):
    night.run()

    assert _probe_lines(night) == [
        "phase3-probe run --frozen nocturna phase3-probe --trigger run-night"
    ]


def test_run_night_el_probe_se_lanza_aunque_el_sync_no_este_configurado(tmp_path):
    rig = _make_rig(tmp_path, NIGHT_SCRIPT, configured=False)
    fk.install_all(tmp_path / "bin", uv=FAKE_UV)

    result = rig.run()

    assert result.returncode == 0
    assert len(_probe_lines(rig)) == 1
    assert "sync=78" in rig.status and "probe=0" in rig.status


@pytest.mark.parametrize("probe_code", [0, 1, 78, 5])
def test_run_night_el_codigo_del_probe_queda_en_probe_y_no_cambia_el_de_la_noche(night, probe_code):
    result = night.run(UV_EXIT_CODE="3", UV_PROBE_EXIT_CODE=str(probe_code))

    assert result.returncode == 3
    assert f"probe={probe_code}" in night.status
    assert night.order[-1] == "morning-report"  # el informe sale igual


@pytest.mark.parametrize("night_code", [0, 7])
def test_run_night_un_probe_que_falla_no_convierte_la_noche_en_fallida(night, night_code):
    result = night.run(UV_EXIT_CODE=str(night_code), UV_PROBE_EXIT_CODE="1")

    assert result.returncode == night_code


def test_run_night_el_estado_deja_probe_al_final_tras_sync(night):
    night.run()

    assert night.status == ["run-night=0", "evaluate-tensions=0", "sync=0", "probe=0"]


def test_run_night_con_dry_run_no_lanza_el_probe(night):
    result = night.run("--dry-run")

    assert result.returncode == 0
    assert _probe_lines(night) == []
    assert not any(line.startswith("probe=") for line in night.status)


@pytest.mark.parametrize(
    ("label", "env", "code"),
    [
        ("75-postgres-no-listo", {"DOCKER_HEALTH_STATUS": "starting"}, 75),
        ("77-entorno-invalido", {"NOCTURNA_WAIT_S": "abc"}, 77),
    ],
    ids=lambda v: v if isinstance(v, str) else None,
)
def test_run_night_con_salidas_75_o_77_no_lanza_el_probe(night, label, env, code):
    result = night.run(**env)

    assert result.returncode == code
    assert _probe_lines(night) == []
    assert not any(line.startswith("probe=") for line in night.status)


def test_run_night_con_salida_76_por_noche_duplicada_no_lanza_un_segundo_probe(night):
    first = night.run()
    second = night.run()

    assert (first.returncode, second.returncode) == (0, 76)
    assert len(_probe_lines(night)) == 1
    assert night.status.count("probe=0") == 1


def test_run_night_sin_uv_sale_77_sin_probe(tmp_path):
    rig = _make_rig(tmp_path, NIGHT_SCRIPT)
    (tmp_path / "bin" / "uv").unlink()

    assert rig.run().returncode == 77
    assert _probe_lines(rig) == []


def test_run_night_el_probe_no_recibe_la_url_en_los_argumentos(night):
    night.run(NOCTURNA_PUBLIC_URL="https://secreto.example.org/")

    assert all("secreto.example.org" not in line for line in night.order)


def test_run_night_un_probe_colgado_se_corta_con_probe_3_y_el_informe_sale_igual(night):
    started = time.monotonic()

    result = night.run(UV_EXIT_CODE="7", UV_PROBE_HANG="1", NOCTURNA_PROBE_TIMEOUT_S="2")

    assert time.monotonic() - started < 60
    assert result.returncode == 7  # el codigo de la noche no cambia
    assert "probe=3" in night.status
    assert night.order[-1] == "morning-report"


def test_run_night_con_el_probe_colgado_no_deja_procesos_sleep_huerfanos(night):
    night.run(UV_PROBE_HANG="1", NOCTURNA_PROBE_TIMEOUT_S="1")

    leftovers = subprocess.run(["pgrep", "-f", "sleep 300"], capture_output=True, text=True)
    assert leftovers.stdout.strip() == ""


def _pgrep(pattern: str) -> str:
    return subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.strip()


def test_run_night_un_probe_que_ignora_term_se_mata_con_kill_a_los_5s(night):
    started = time.monotonic()

    result = night.run(UV_PROBE_IGNORE_TERM="1", NOCTURNA_PROBE_TIMEOUT_S="2", UV_EXIT_CODE="0")

    elapsed = time.monotonic() - started
    assert 7 <= elapsed < 30  # 2 s de tope + 5 s de gracia antes del KILL
    assert result.returncode == 0
    assert "probe=3" in night.status
    assert _pgrep("phase3-probe --trigger run-night") == ""


def test_run_night_tras_un_probe_normal_no_queda_sleep_del_vigilante(night, tmp_path):
    night.run(NOCTURNA_PROBE_TIMEOUT_S="777", TMPDIR=str(tmp_path))

    assert _pgrep("sleep 777") == ""
    assert list(tmp_path.glob("nocturna-probe.*")) == []


def test_run_night_el_vigilante_no_deja_terminated_en_los_logs(night):
    result = night.run(NOCTURNA_PROBE_TIMEOUT_S="777")

    err_log = next((night.home / "nocturna-logs").glob("night-*.err.log"))
    assert "Terminated" not in result.stderr
    # `sync-to-vps.sh` (T108) tiene su propio vigilante con ese aviso, ajeno al del probe.
    probe_noise = [ln for ln in err_log.read_text().splitlines() if '"$flag"' in ln]
    assert [ln for ln in probe_noise if "Terminated" in ln] == []


def test_run_night_si_el_envoltorio_muere_no_queda_el_fichero_de_marca(night, tmp_path):
    env = {
        "HOME": str(night.home),
        "PATH": fk.SYSTEM_PATH,
        "NOCTURNA_PATH": f"{tmp_path / 'bin'}:{fk.SYSTEM_PATH}",
        "NOCTURNA_WAIT_S": "1",
        "ORDER_LOG": str(tmp_path / "order.log"),
        "DOCKER_CALL_LOG_FILE": str(tmp_path / "docker.log"),
        "UV_PROBE_IGNORE_TERM": "1",
        "NOCTURNA_PROBE_TIMEOUT_S": "1",
        "TMPDIR": str(tmp_path),
        **night.extra_env,
    }
    proc = subprocess.Popen(  # noqa: S603
        [str(night.script)],
        env=env,
        cwd=tmp_path,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not list(tmp_path.glob("nocturna-probe.*")):
            time.sleep(0.1)  # el vigilante crea la marca al vencer el tope (1 s)
        assert list(tmp_path.glob("nocturna-probe.*")), "el vigilante no llego a crear la marca"
        proc.terminate()
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    time.sleep(0.5)

    assert list(tmp_path.glob("nocturna-probe.*")) == []
    assert _pgrep(f"{tmp_path}.*phase3-probe") == ""


# --- informe matinal ---------------------------------------------------------------------


@pytest.fixture
def report(tmp_path):
    env = ReportEnv(tmp_path)
    fk.install_all(env.bin, uv=FAKE_UV)
    return env


def _report_order(env: ReportEnv, tmp_path: Path) -> list[str]:
    f = tmp_path / "order.log"
    return f.read_text().splitlines() if f.exists() else []


def test_informe_el_probe_va_antes_del_informe(report, tmp_path):
    result = report.run("--no-notify", ORDER_LOG=str(tmp_path / "order.log"))

    assert result.returncode == 0, result.stderr
    assert [s.split()[0] for s in _report_order(report, tmp_path)] == [
        "phase3-probe",
        "morning-report",
    ]
    assert "--trigger scheduled" in _report_order(report, tmp_path)[0]


@pytest.mark.parametrize("probe_code", [0, 1, 78, 5])
@pytest.mark.parametrize("report_code", [0, 1, 4])
def test_informe_el_codigo_es_el_del_informe_pase_lo_que_pase_con_el_probe(
    report, tmp_path, probe_code, report_code
):
    result = report.run(
        ORDER_LOG=str(tmp_path / "order.log"),
        UV_PROBE_EXIT_CODE=str(probe_code),
        UV_REPORT_EXIT_CODE=str(report_code),
    )

    assert result.returncode == report_code
    assert _report_order(report, tmp_path)[-1] == "morning-report"


def test_informe_con_el_probe_colgado_se_corta_y_el_informe_se_ejecuta_igual(report, tmp_path):
    started = time.monotonic()

    result = report.run(
        ORDER_LOG=str(tmp_path / "order.log"),
        UV_PROBE_HANG="1",
        NOCTURNA_PROBE_TIMEOUT_S="2",
        UV_REPORT_EXIT_CODE="4",
    )

    assert time.monotonic() - started < 60
    assert result.returncode == 4
    assert _report_order(report, tmp_path)[-1] == "morning-report"


def test_informe_un_probe_que_ignora_term_se_mata_con_kill_a_los_5s(report, tmp_path):
    started = time.monotonic()

    result = report.run(
        ORDER_LOG=str(tmp_path / "order.log"),
        UV_PROBE_IGNORE_TERM="1",
        NOCTURNA_PROBE_TIMEOUT_S="2",
    )

    assert 7 <= time.monotonic() - started < 30
    assert result.returncode == 0
    assert _report_order(report, tmp_path)[-1] == "morning-report"
    assert _pgrep("phase3-probe --trigger scheduled") == ""


def test_informe_tras_un_probe_normal_no_queda_sleep_ni_terminated_ni_marca(report, tmp_path):
    result = report.run(NOCTURNA_PROBE_TIMEOUT_S="778", TMPDIR=str(tmp_path))

    err_log = next((report.home / "nocturna-logs").glob("morning-*.err.log"), None)
    assert _pgrep("sleep 778") == ""
    assert "Terminated" not in result.stderr
    assert err_log is None or "Terminated" not in err_log.read_text()
    assert list(tmp_path.glob("nocturna-probe.*")) == []


def test_informe_si_el_envoltorio_muere_no_queda_el_fichero_de_marca(report, tmp_path):
    env = {
        "HOME": str(report.home),
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "NOCTURNA_PATH": f"{report.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
        "NOCTURNA_WAIT_S": "1",
        "UV_PROBE_IGNORE_TERM": "1",
        "NOCTURNA_PROBE_TIMEOUT_S": "1",
        "TMPDIR": str(tmp_path),
    }
    script = Path(__file__).resolve().parents[2] / "scripts" / "morning-report-scheduled.sh"
    proc = subprocess.Popen(  # noqa: S603
        [str(script)], env=env, cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    try:
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline and not list(tmp_path.glob("nocturna-probe.*")):
            time.sleep(0.1)
        assert list(tmp_path.glob("nocturna-probe.*")), "el vigilante no llego a crear la marca"
        proc.terminate()
        proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
    time.sleep(0.5)

    assert list(tmp_path.glob("nocturna-probe.*")) == []
    assert _pgrep(f"{tmp_path}.*phase3-probe") == ""


def test_informe_sin_uv_sigue_saliendo_77_y_sin_probe(tmp_path):
    env = ReportEnv(tmp_path, uv=False)

    assert env.run().returncode == 77


# --- el estado y las plantillas ----------------------------------------------------------


@pytest.mark.parametrize("extra", [{}, {"probe": 0}, {"probe": 1}, {"probe": 78}])
def test_probe_en_el_status_no_cambia_la_clasificacion_de_la_noche(extra):
    run = _run(REPORT_NIGHT, RunStatus.PARTIAL, NOTES_ARXIV, published=0, read=7)
    base_exits = {"run-night": 7, "evaluate-tensions": 0, "sync": 0}

    plain = _build(_Data([run]), LaunchEvidence(launched=True, wrapper_exits=base_exits))
    with_probe = _build(
        _Data([run]), LaunchEvidence(launched=True, wrapper_exits={**base_exits, **extra})
    )

    assert with_probe.classification == plain.classification
    assert with_probe.run_status == plain.run_status


def test_el_lector_del_status_acepta_la_clave_probe_sin_romper_las_demas(tmp_path):
    key = REPORT_NIGHT.strftime("%Y%m%d")
    (tmp_path / f"night-{key}.status").write_text("run-night=0\nsync=0\nprobe=78\n")

    evidence = FileLaunchLog(tmp_path).evidence(REPORT_NIGHT)

    assert evidence.wrapper_exits == {"run-night": 0, "sync": 0, "probe": 78}


@pytest.mark.parametrize(
    "template",
    ["com.nocturna.run-night.plist.template", "com.nocturna.morning-report.plist.template"],
)
def test_las_plantillas_plist_traen_el_marcador_de_la_url_publica(template):
    text = (BACKEND / "scripts" / template).read_text()
    plist = plistlib.loads(text.replace("__HOME__", "/tmp/h").encode())

    assert plist["EnvironmentVariables"]["NOCTURNA_PUBLIC_URL"] == MARKER


def test_ninguna_plantilla_ni_envoltorio_lleva_una_url_real():
    for name in (
        "com.nocturna.run-night.plist.template",
        "com.nocturna.morning-report.plist.template",
        "run-night-scheduled.sh",
        "morning-report-scheduled.sh",
    ):
        text = (BACKEND / "scripts" / name).read_text()
        assert "https://" not in text.replace("https://www.apple.com", ""), name


def test_el_envoltorio_de_la_noche_no_toca_la_base_ni_claude_para_el_probe():
    script = (BACKEND / "scripts" / "run-night-scheduled.sh").read_text()
    line = next(ln for ln in script.splitlines() if "phase3-probe" in ln and "uv run" in ln)

    assert "--trigger" in line
    assert "run_probe_guarded run-night" in script
    assert "claude" not in line and "ANTHROPIC" not in line


def test_los_envoltorios_lanzan_el_probe_con_uv_run_frozen_y_su_sintaxis_es_valida():
    for name in ("run-night-scheduled.sh", "morning-report-scheduled.sh"):
        text = (BACKEND / "scripts" / name).read_text()
        assert "uv run --frozen nocturna phase3-probe" in text
    assert (
        subprocess.run(
            ["bash", "-n", str(BACKEND / "scripts" / "run-night-scheduled.sh")]
        ).returncode
        == 0
    )
    assert (
        subprocess.run(
            ["bash", "-n", str(BACKEND / "scripts" / "morning-report-scheduled.sh")]
        ).returncode
        == 0
    )
