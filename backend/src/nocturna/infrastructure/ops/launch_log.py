"""Evidencia de lanzamiento de una noche, leída del directorio de logs (T105).

Los envoltorios (`backend/scripts/*-scheduled.sh`) dejan, por noche, un centinela
`.launched-YYYYMMDD` y ficheros de estado `night-YYYYMMDD.status` y
`archive-YYYYMMDD.status` con líneas `clave=código`. Aquí solo se comprueba la
existencia de los logs `night-*.out.log` / `.err.log`: su contenido no se lee
nunca (puede llevar rutas o datos que no deben salir en el informe).
"""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

from nocturna.application.use_cases.morning_report import LaunchEvidence

_logger = logging.getLogger(__name__)


def _read_status(path: Path) -> dict[str, int]:
    """`clave=código` por línea; la última de cada clave gana; la basura se ignora."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return {}
    except OSError:
        _logger.warning("launch_log.status_unreadable", extra={"file": path.name})
        return {}
    result: dict[str, int] = {}
    for raw in text.splitlines():
        key, sep, value = raw.strip().partition("=")
        key, value = key.strip(), value.strip()
        if not sep or not key or not value.isdecimal():
            continue
        result[key] = int(value)
    return result


class FileLaunchLog:
    """Cumple `application.use_cases.morning_report.LaunchLog`."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir

    def evidence(self, night: date) -> LaunchEvidence:
        key = night.strftime("%Y%m%d")
        logs = tuple(
            name
            for name in (f"night-{key}.out.log", f"night-{key}.err.log")
            if (self._log_dir / name).is_file()
        )
        return LaunchEvidence(
            launched=(self._log_dir / f".launched-{key}").exists(),
            wrapper_exits=_read_status(self._log_dir / f"night-{key}.status"),
            archive_exits=_read_status(self._log_dir / f"archive-{key}.status"),
            logs=logs,
        )
