"""Escritura del informe matinal y del registro de entregas (T105).

`morning-YYYYMMDD.txt` se escribe de forma atómica (fichero temporal + `os.replace`)
con permisos 0600. `morning-deliveries.log` solo se añade: una línea por entrega
con `notified=ok|fail|skip`.
"""

from __future__ import annotations

import os
import tempfile
from datetime import date
from pathlib import Path

_DELIVERIES = "morning-deliveries.log"


class FileReportSink:
    """Cumple `application.use_cases.morning_report.ReportSink`."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    def write(self, night: date, text: str) -> str:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        name = f"morning-{night.strftime('%Y%m%d')}.txt"
        fd, tmp = tempfile.mkstemp(dir=self._log_dir, prefix=f".{name}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text if text.endswith("\n") else text + "\n")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._log_dir / name)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
        return name

    def append_delivery(self, line: str) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        path = self._log_dir / _DELIVERIES
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line.rstrip("\n") + "\n")

    def delivered(self, night: date) -> bool:
        path = self._log_dir / _DELIVERIES
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            return False
        marker = f" {night.isoformat()} "
        return any(marker in line and line.endswith("notified=ok") for line in text.splitlines())
