"""Aviso de macOS con `osascript` (T105). Cumple `morning_report.Notifier`.

Título y mensaje viajan como argumentos del script (`argv`), nunca interpolados
en el código AppleScript: ningún texto del informe puede inyectar instrucciones.
"""

from __future__ import annotations

import logging
import subprocess

_logger = logging.getLogger(__name__)

_TIMEOUT_S = 10


class OsascriptNotifier:
    def notify(self, title: str, message: str) -> bool:
        """Muestra la notificación. Nunca lanza: cualquier fallo devuelve `False`."""
        command = [
            "osascript",
            "-e",
            "on run argv",
            "-e",
            "display notification (item 2 of argv) with title (item 1 of argv)",
            "-e",
            "end run",
            title,
            message,
        ]
        try:
            result = subprocess.run(command, timeout=_TIMEOUT_S, capture_output=True, check=False)
        except (OSError, subprocess.SubprocessError) as exc:
            _logger.warning("notifier.osascript_failed", extra={"error": type(exc).__name__})
            return False
        if result.returncode != 0:
            _logger.warning("notifier.osascript_exit", extra={"returncode": result.returncode})
            return False
        return True
