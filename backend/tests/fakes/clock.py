"""Reloj falso de hora fijable, para tests deterministas de `BudgetGuard`.

Cumple `nocturna.domain.clock.Clock` estructuralmente (no hereda de él, es
un `Protocol`). Ningún test de la suite debe leer `datetime.now()`: la hora
sale siempre de un `FakeClock`, fijada a mano en el test, para que un
`hard_stop` mal calculado se vea en un `assert` y no dependa de a qué hora
del día se ejecute la suite.
"""

from __future__ import annotations

from datetime import datetime


class FakeClock:
    """Devuelve siempre la hora que se le fija, hasta la siguiente llamada a `set`."""

    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def set(self, now: datetime) -> None:
        """Mueve el reloj a otra hora, para un test que comprueba varios instantes."""
        self._now = now
