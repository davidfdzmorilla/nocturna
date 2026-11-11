"""Registros de ficheros del instrumento de cierre de la fase 3 (T110.a).

`phase3-probes.log` se añade al final con permisos 0600. `morning-deliveries.log`
(T105, `report_file.py`) solo se lee. Las líneas ilegibles se ignoran.
"""

from __future__ import annotations

import os
from pathlib import Path

from nocturna.domain.phase3 import (
    DeliveryRecord,
    ProbeRecord,
    format_probe_line,
    parse_delivery_line,
    parse_probe_line,
)

_PROBES = "phase3-probes.log"
_DELIVERIES = "morning-deliveries.log"


def _lines(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except FileNotFoundError:
        return []


class FileProbeLog:
    """Cumple `application.use_cases.phase3.ProbeLog`."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    def append(self, record: ProbeRecord) -> None:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._log_dir / _PROBES, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(format_probe_line(record) + "\n")

    def read(self) -> list[ProbeRecord]:
        parsed = (parse_probe_line(line) for line in _lines(self._log_dir / _PROBES))
        return [r for r in parsed if r is not None]


class FileDeliveryLog:
    """Cumple `application.use_cases.phase3.DeliveryLog`."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    def read(self) -> list[DeliveryRecord]:
        parsed = (parse_delivery_line(line) for line in _lines(self._log_dir / _DELIVERIES))
        return [r for r in parsed if r is not None]
