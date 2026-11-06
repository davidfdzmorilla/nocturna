"""T105: subcomando `morning-report` y adaptadores de `infrastructure/ops`.

Sin Claude y sin `osascript` real: el aviso se sustituye por un falso o se desactiva con
`--no-notify`. La base apunta a un puerto cerrado, así que el informe se genera con la base
marcada como inaccesible (la rama que no tiene que depender de nada).
"""

from __future__ import annotations

import contextlib
import logging
import stat
import subprocess
from datetime import date

import pytest

from nocturna import cli
from nocturna.infrastructure.ops.launch_log import FileLaunchLog
from nocturna.infrastructure.ops.notifier import OsascriptNotifier
from nocturna.infrastructure.ops.report_file import FileReportSink

NIGHT = date(2026, 11, 5)


@contextlib.contextmanager
def _capture_logs(name: str):
    """Captura los registros de un logger con su formato JSON real, sea cual sea el
    estado global del logging que hayan dejado otros tests."""
    from nocturna.infrastructure.logging import _JsonFormatter

    lines: list[str] = []

    class _H(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            lines.append(_JsonFormatter().format(record))

    logger = logging.getLogger(name)
    handler = _H()
    old = (logger.level, logger.disabled, logger.propagate)
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    logger.disabled = False
    logging.disable(logging.NOTSET)
    try:
        yield lines
    finally:
        logger.removeHandler(handler)
        logger.level, logger.disabled, logger.propagate = old


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    path = tmp_path / "logs"
    monkeypatch.setenv("NOCTURNA_LOG_DIR", str(path))
    monkeypatch.setenv("NOCTURNA_DATABASE_URL", "postgresql+psycopg://u:p@127.0.0.1:1/none")
    return path


class _FakeNotifier:
    calls: list[tuple[str, str]] = []
    result = True

    def notify(self, title: str, message: str) -> bool:
        type(self).calls.append((title, message))
        return type(self).result


@pytest.fixture
def fake_notifier(monkeypatch):
    _FakeNotifier.calls = []
    _FakeNotifier.result = True
    monkeypatch.setattr(cli, "OsascriptNotifier", _FakeNotifier)
    return _FakeNotifier


def test_escribe_el_informe_con_la_base_caida_y_sale_0(log_dir, capsys):
    code = cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"])

    out = capsys.readouterr().out
    assert code == 0
    assert "Causa: base de datos inaccesible" in out
    report = log_dir / "morning-20261105.txt"
    assert report.read_text().strip() == out.strip()
    assert stat.S_IMODE(report.stat().st_mode) == 0o600
    assert "notified=skip" in (log_dir / "morning-deliveries.log").read_text()


def test_el_informe_no_contiene_el_dsn_ni_rutas(log_dir, tmp_path, capsys):
    cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"])

    out = capsys.readouterr().out
    for forbidden in (str(tmp_path), "127.0.0.1", "postgresql", ":p@"):
        assert forbidden not in out


def test_lee_el_estado_del_envoltorio(log_dir, capsys):
    log_dir.mkdir()
    (log_dir / "night-20261105.status").write_text("basura\nprecondition=75\n")

    cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"])

    assert "precondiciones no listas" in capsys.readouterr().out


def test_avisa_con_run_night_y_registra_la_entrega(log_dir, fake_notifier):
    code = cli.main(["morning-report", "--night", "2026-11-05", "--trigger", "run-night"])

    assert code == 0
    assert fake_notifier.calls == [("Nocturna · 11-05 sin datos", "base de datos inaccesible")]
    assert "run-night" in (log_dir / "morning-deliveries.log").read_text()
    assert (log_dir / "morning-deliveries.log").read_text().endswith("notified=ok\n")


def test_fallo_del_aviso_se_registra_y_no_cambia_el_codigo(log_dir, fake_notifier):
    fake_notifier.result = False

    code = cli.main(["morning-report", "--night", "2026-11-05", "--trigger", "run-night"])

    assert code == 0
    assert (log_dir / "morning-deliveries.log").read_text().endswith("notified=fail\n")


def test_no_notify_no_llama_al_aviso(log_dir, fake_notifier):
    cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"])

    assert fake_notifier.calls == []


def test_log_dir_vacio_sale_1(monkeypatch, capsys):
    monkeypatch.setenv("NOCTURNA_LOG_DIR", "")

    assert cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"]) == 1
    assert "NOCTURNA_LOG_DIR" in capsys.readouterr().err


def test_si_no_puede_escribir_sale_1(log_dir, capsys):
    log_dir.parent.mkdir(parents=True, exist_ok=True)
    log_dir.write_text("soy un fichero, no un directorio")

    assert cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"]) == 1


def test_night_invalida_la_rechaza_argparse(log_dir):
    with pytest.raises(SystemExit) as exc:
        cli.main(["morning-report", "--night", "ayer"])

    assert exc.value.code == 2


# --- adaptadores ---------------------------------------------------------------------


def test_launch_log_lee_centinela_estado_y_logs(tmp_path):
    (tmp_path / ".launched-20261105").write_text("x")
    (tmp_path / "night-20261105.status").write_text(
        "run-night=7\nrun-night=8\nevaluate-tensions=0\nruido\nclave=abc\n=3\n"
    )
    (tmp_path / "archive-20261105.status").write_text("archive-snapshot=3\n")
    (tmp_path / "night-20261105.out.log").write_text("SECRETO")

    evidence = FileLaunchLog(tmp_path).evidence(NIGHT)

    assert evidence.launched is True
    assert evidence.wrapper_exits == {"run-night": 8, "evaluate-tensions": 0}
    assert evidence.archive_exits == {"archive-snapshot": 3}
    assert evidence.logs == ("night-20261105.out.log",)


def test_launch_log_sin_nada(tmp_path):
    evidence = FileLaunchLog(tmp_path / "no-existe").evidence(NIGHT)

    assert (evidence.launched, dict(evidence.wrapper_exits), evidence.logs) == (False, {}, ())


def test_report_sink_atomico_0600_y_entregas(tmp_path):
    sink = FileReportSink(tmp_path)

    name = sink.write(NIGHT, "uno")
    sink.write(NIGHT, "dos")

    assert name == "morning-20261105.txt"
    assert (tmp_path / name).read_text() == "dos\n"
    assert stat.S_IMODE((tmp_path / name).stat().st_mode) == 0o600
    assert [p.name for p in tmp_path.iterdir()] == [name]

    assert sink.delivered(NIGHT) is False
    sink.append_delivery("t 2026-11-05 scheduled partial - notified=fail")
    assert sink.delivered(NIGHT) is False
    sink.append_delivery("t 2026-11-05 scheduled partial - notified=ok")
    assert sink.delivered(NIGHT) is True
    assert sink.delivered(date(2026, 11, 4)) is False


def test_notifier_pasa_titulo_y_mensaje_como_argv(monkeypatch):
    seen: dict[str, object] = {}

    def fake_run(command, **kwargs):
        seen["command"], seen["kwargs"] = command, kwargs
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(subprocess, "run", fake_run)

    assert OsascriptNotifier().notify('T "x"', "M; do shell script") is True
    assert seen["command"][-2:] == ['T "x"', "M; do shell script"]
    assert seen["command"][:2] == ["osascript", "-e"]
    assert seen["kwargs"]["timeout"] == 10


@pytest.mark.parametrize(
    "failure",
    [FileNotFoundError("osascript"), subprocess.TimeoutExpired("osascript", 10)],
)
def test_notifier_nunca_lanza(monkeypatch, failure):
    def boom(*_a, **_k):
        raise failure

    monkeypatch.setattr(subprocess, "run", boom)

    assert OsascriptNotifier().notify("t", "m") is False


def test_notifier_codigo_de_salida_distinto_de_cero(monkeypatch):
    monkeypatch.setattr(
        subprocess, "run", lambda command, **_k: subprocess.CompletedProcess(command, 1)
    )

    assert OsascriptNotifier().notify("t", "m") is False


def test_fallo_del_registro_de_entregas_no_vuelca_la_ruta_en_los_logs(log_dir, monkeypatch):
    def boom(self, line):
        raise OSError(13, "s3cr3t denied", "/Users/david/logs/morning-deliveries.log")

    monkeypatch.setattr(FileReportSink, "append_delivery", boom)

    with _capture_logs("nocturna.cli") as lines:
        code = cli.main(["morning-report", "--night", "2026-11-05", "--no-notify"])

    logs = "\n".join(lines)
    assert code == 0
    assert "delivery_log_failed" in logs
    assert "PermissionError" in logs
    assert "s3cr3t" not in logs
    assert "/Users/" not in logs
