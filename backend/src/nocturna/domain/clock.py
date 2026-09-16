"""Puerto de la hora actual.

Vive en `domain/` por el mismo motivo que `sources.py`: sin esta interfaz,
`application/` (T30, `BudgetGuard`) tendría que leer la hora del sistema
directamente, acoplándose a `zoneinfo`/`TZ` y volviéndose imposible de
probar de forma determinista (un test que compara contra `hard_stop` no
puede depender de a qué hora se ejecuta el test).

El dominio no convierte zonas horarias (ver `ARCHITECTURE.md`): este puerto
solo declara que la hora que recibe `application/` ya viene *aware* y en la
zona de la ventana de ejecución. La conversión de zona es responsabilidad
exclusiva de infraestructura (`infrastructure/clock.py`).
"""

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    """Fuente de la hora actual.

    Implementada estructuralmente por `infrastructure/clock.py`
    (`SystemClock`), que no hereda de este `Protocol`: es infraestructura
    quien cumple el contrato del dominio, nunca al revés.
    """

    def now(self) -> datetime:
        """Hora actual, aware, ya en la zona de la ventana de ejecución."""
        ...
