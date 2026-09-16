"""Reloj del sistema, en la zona horaria de la ventana de ejecución.

Cumple `domain.clock.Clock` estructuralmente (es un `Protocol`):
`SystemClock` no hereda de él, es infraestructura quien satisface el
contrato del dominio, nunca al revés.

La zona se recibe por constructor, nunca se lee de `config/pipeline.toml`
ni de la variable de entorno `TZ`: así el reloj sigue siendo una pieza de
infraestructura simple y testeable, y quien decide qué zona usar (el punto
de composición del pipeline, leyendo `window.timezone`) queda explícito en
un único sitio.
"""

from datetime import datetime
from zoneinfo import ZoneInfo


class SystemClock:
    """Reloj real, basado en el reloj del sistema. Cumple `domain.clock.Clock`."""

    def __init__(self, timezone: ZoneInfo) -> None:
        self._timezone = timezone

    def now(self) -> datetime:
        """Hora actual, aware, en la zona recibida por constructor."""
        return datetime.now(self._timezone)
