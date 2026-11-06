"""T105: `morning-report` de extremo a extremo con adaptadores reales de `infrastructure/ops`.

Sin Claude, sin red y sin `osascript` real: el aviso es un falso (en proceso) o un ejecutable
falso en el PATH que vuelca su `argv`. La base apunta a un puerto cerrado (informe sin base).
"""

from __future__ import annotations

import builtins
import io
import os
import stat
import subprocess
import sys
import textwrap
from datetime import date
from pathlib import Path

import pytest

from nocturna import cli
from nocturna.infrastructure.ops import notifier as notifier_module
from nocturna.infrastructure.ops.launch_log import FileLaunchLog
from nocturna.infrastructure.ops.notifier import OsascriptNotifier

NIGHT = date(2026, 11, 5)
SECRET = "s3cr3t"
DSN = f"postgresql+psycopg://u:{SECRET}@127.0.0.1:1/none"
DSN_SIN_DRIVER = f"postgresql://u:{SECRET}@127.0.0.1:1/none"


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    path = tmp_path / "logs"
    monkeypatch.setenv("NOCTURNA_LOG_DIR", str(path))
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/none")
    return path


class _Recorder:
    calls: list[tuple[str, str]] = []
    result = True

    def notify(self, title: str, message: str) -> bool:
        type(self).calls.append((title, message))
        return type(self).result


@pytest.fixture
def recorder(monkeypatch):
    _Recorder.calls, _Recorder.result = [], True
    monkeypatch.setattr(cli, "OsascriptNotifier", _Recorder)
    return _Recorder


# --- CLI -------------------------------------------------------------------------------


def test_el_subcomando_morning_report_existe_y_documenta_sus_opciones(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["morning-report", "--help"])

    out = capsys.readouterr().out
    assert exc.value.code == 0
    assert all(flag in out for flag in ("--night", "--trigger", "--no-notify"))


def test_cada_ejecucion_anade_una_linea_al_registro_de_entregas(log_dir):
    for _ in range(2):
        assert cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"]) == 0

    lines = (log_dir / "morning-deliveries.log").read_text().splitlines()
    assert len(lines) == 2
    assert all(line.endswith("notified=skip") for line in lines)
    assert stat.S_IMODE((log_dir / "morning-deliveries.log").stat().st_mode) == 0o600


def test_el_informe_queda_en_0600_dentro_de_nocturna_log_dir(log_dir):
    cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"])

    files = sorted(p.name for p in log_dir.iterdir())
    assert files == ["morning-20261105.txt", "morning-deliveries.log"]
    assert stat.S_IMODE((log_dir / "morning-20261105.txt").stat().st_mode) == 0o600


def test_un_dsn_con_contrasena_y_las_variables_anthropic_no_salen_en_ningun_sitio(
    log_dir, recorder, monkeypatch, capsys
):
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", DSN)
    monkeypatch.setenv("ANTHROPIC_API_KEY", SECRET)
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", SECRET)

    code = cli.main(["morning-report", "--night", "2026-11-05", "--trigger", "run-night"])

    captured = capsys.readouterr()
    assert code == 0
    assert recorder.calls, "el aviso debía mostrarse"
    assert SECRET not in (log_dir / "morning-20261105.txt").read_text()
    assert SECRET not in (log_dir / "morning-deliveries.log").read_text()
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert all(SECRET not in part for call in recorder.calls for part in call)


def test_un_dsn_sin_driver_tampoco_impide_el_informe(log_dir, recorder, monkeypatch, capsys):
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", DSN_SIN_DRIVER)

    assert cli.main(["morning-report", "--night", "2026-11-05", "--trigger", "run-night"]) == 0

    captured = capsys.readouterr()
    assert recorder.calls
    assert SECRET not in (log_dir / "morning-20261105.txt").read_text()
    assert SECRET not in (log_dir / "morning-deliveries.log").read_text()
    assert SECRET not in captured.out
    assert SECRET not in captured.err
    assert all(SECRET not in part for call in recorder.calls for part in call)


def test_aviso_que_devuelve_false_deja_notified_fail_y_codigo_0(log_dir, recorder):
    recorder.result = False

    code = cli.main(["morning-report", "--night", "2026-11-05", "--trigger", "archive-snapshot"])

    assert code == 0
    assert recorder.calls
    assert (log_dir / "morning-deliveries.log").read_text().endswith("notified=fail\n")


def test_programado_no_repite_el_aviso_de_una_noche_completed_ya_entregada(log_dir, recorder):
    # Sin Run no es COMPLETED: siempre avisa, aunque ya hubiera entrega.
    for _ in range(2):
        cli.main(["morning-report", "--night", "2026-11-05", "--trigger", "scheduled"])

    assert len(recorder.calls) == 2


def test_morning_report_no_carga_claude_ni_el_proveedor_llm_en_un_proceso_limpio(tmp_path):
    script = textwrap.dedent(
        """
        import sys
        from nocturna import cli
        code = cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"])
        bad = sorted(
            n for n in sys.modules
            if n == "claude_agent_sdk" or n.startswith("claude_agent_sdk.")
            or n.startswith("nocturna.infrastructure.llm")
        )
        if bad:
            print("LEAK", bad)
            sys.exit(99)
        sys.exit(code)
        """
    )
    env = {
        **os.environ,
        "NOCTURNA_LOG_DIR": str(tmp_path / "logs"),
        "NOCTURNA_DATABASE_URL": "postgresql+psycopg://u:p@127.0.0.1:1/none",
    }

    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (tmp_path / "logs" / "morning-20261105.txt").is_file()


# --- OsascriptNotifier con un osascript falso en el PATH ------------------------------------


def _fake_osascript(directory: Path, body: str) -> None:
    directory.mkdir()
    path = directory / "osascript"
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(0o755)


def test_osascript_recibe_titulo_y_mensaje_como_argumentos_sin_interpretarlos(
    tmp_path, monkeypatch
):
    dump = tmp_path / "argv.bin"
    pwned = tmp_path / "PWNED"
    _fake_osascript(
        tmp_path / "bin", f'for a in "$@"; do printf "%s\\0" "$a"; done > "{dump}"\nexit 0\n'
    )
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    title = 'Nocturna "11-05"'
    message = f'x"; do shell script "touch {pwned}"; display notification "y'

    assert OsascriptNotifier().notify(title, message) is True

    argv = dump.read_bytes().decode().split("\0")[:-1]
    assert argv[-2:] == [title, message]
    script_parts = argv[: argv.index(title)]
    assert script_parts[0] == "-e"
    assert not any("shell script" in part or "touch" in part for part in script_parts)
    assert any("item 2 of argv" in part for part in script_parts)
    assert not pwned.exists()


def test_osascript_inexistente_devuelve_false_sin_lanzar(tmp_path, monkeypatch):
    (tmp_path / "vacio").mkdir()
    monkeypatch.setenv("PATH", str(tmp_path / "vacio"))

    assert OsascriptNotifier().notify("t", "m") is False


def test_osascript_con_codigo_distinto_de_cero_devuelve_false(tmp_path, monkeypatch):
    _fake_osascript(tmp_path / "bin", "exit 3\n")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")

    assert OsascriptNotifier().notify("t", "m") is False


def test_osascript_que_no_responde_devuelve_false_al_vencer_el_tiempo(tmp_path, monkeypatch):
    _fake_osascript(tmp_path / "bin", "exec sleep 5\n")
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'}:/usr/bin:/bin")
    monkeypatch.setattr(notifier_module, "_TIMEOUT_S", 0.3)

    assert OsascriptNotifier().notify("t", "m") is False


# --- FileLaunchLog -----------------------------------------------------------------------


def test_launch_log_la_ultima_linea_repetida_gana_y_la_basura_se_ignora(tmp_path):
    (tmp_path / "night-20261105.status").write_text(
        "run-night=1\nbasura sin igual\nrun-night=7\nevaluate-tensions=abc\nevaluate-tensions=-1\n"
        "evaluate-tensions=0\n\n  precondition = 75 \n=5\nx=\n"
    )

    evidence = FileLaunchLog(tmp_path).evidence(NIGHT)

    assert dict(evidence.wrapper_exits) == {
        "run-night": 7,
        "evaluate-tensions": 0,
        "precondition": 75,
    }
    assert dict(evidence.archive_exits) == {}


def test_launch_log_con_estado_ausente_o_basura_binaria_no_falla(tmp_path):
    assert dict(FileLaunchLog(tmp_path).evidence(NIGHT).wrapper_exits) == {}
    (tmp_path / "night-20261105.status").write_bytes(b"\xff\xfe\x00run-night=\x80\n\x00\x00")

    assert dict(FileLaunchLog(tmp_path).evidence(NIGHT).wrapper_exits) == {}


def test_launch_log_nunca_abre_los_logs_de_salida_ni_de_error(tmp_path, monkeypatch):
    out, err = tmp_path / "night-20261105.out.log", tmp_path / "night-20261105.err.log"
    out.write_text("SECRETO")
    err.write_text("SECRETO")
    out.chmod(0o000)
    err.chmod(0o000)
    opened: list[str] = []
    real_open = builtins.open
    real_os_open = os.open

    def spy_open(file, *args, **kwargs):
        opened.append(str(file))
        return real_open(file, *args, **kwargs)

    def spy_os_open(path, *args, **kwargs):
        opened.append(str(path))
        return real_os_open(path, *args, **kwargs)

    def spy_path(name):
        real = getattr(Path, name)

        def wrapper(self, *args, **kwargs):
            opened.append(str(self))
            return real(self, *args, **kwargs)

        monkeypatch.setattr(Path, name, wrapper)

    monkeypatch.setattr(builtins, "open", spy_open)
    monkeypatch.setattr(io, "open", spy_open)
    monkeypatch.setattr(os, "open", spy_os_open)
    for method in ("open", "read_text", "read_bytes"):
        spy_path(method)
    try:
        evidence = FileLaunchLog(tmp_path).evidence(NIGHT)
    finally:
        out.chmod(0o600)
        err.chmod(0o600)

    assert evidence.logs == ("night-20261105.out.log", "night-20261105.err.log")
    assert not [p for p in opened if p.endswith(".out.log") or p.endswith(".err.log")]
