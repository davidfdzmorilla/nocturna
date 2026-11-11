"""Instrumento de medición del cierre de la fase 3 (T110.a, ADR 0044).

`ProbePublicSite` comprueba la web pública y anota una línea en el registro de
comprobaciones; `MeasurePhase3` evalúa los criterios automáticos (a), (b) y (e)
con `domain/phase3.py`. Sin LLM, sin tokens y sin escritura en la base: este
módulo no importa el proveedor LLM, el Agent SDK ni el control de gasto.

El extractor del HTML (`parse_site_html`) vive en el dominio: es una función pura
sobre texto y así `application/` no importa infraestructura.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

from nocturna.domain.clock import Clock
from nocturna.domain.entities import Run
from nocturna.domain.night_report import is_night_run
from nocturna.domain.phase3 import (
    DeliveryRecord,
    Phase3Measurement,
    ProbeRecord,
    evaluate_phase3,
    parse_site_html,
    robots_header_is_noindex,
)

_logger = logging.getLogger(__name__)

URL_MARKER = "__NOCTURNA_PUBLIC_URL__"
NOT_CONFIGURED = 78
FAILED = 1


@dataclass(frozen=True, slots=True)
class SiteResponse:
    status: int | None
    tls_ok: bool
    error_class: str | None
    html: str | None
    robots_header: str | None = None


class PublicSite(Protocol):
    def fetch(self, url: str) -> SiteResponse:
        """Una sola petición, sin redirecciones. Nunca lanza."""
        ...


class ProbeLog(Protocol):
    def append(self, record: ProbeRecord) -> None: ...

    def read(self) -> list[ProbeRecord]: ...


class DeliveryLog(Protocol):
    def read(self) -> list[DeliveryRecord]: ...


class RunSource(Protocol):
    def started_between(self, start: datetime, end: datetime) -> list[Run]: ...

    def last_night_run_finished_before(self, before: datetime) -> Run | None:
        """El `Run` de noche cerrado más reciente con `started_at < before`, sin límite inferior.

        Filtra por inicio, no por fin: las noches no se solapan, así que el orden es el mismo.
        """
        ...


class ProbePublicSite:
    """Comprueba la URL pública y anota el resultado. `0`, `78` (sin URL) o `1` (log)."""

    def __init__(self, site: PublicSite, log: ProbeLog, clock: Clock) -> None:
        self._site = site
        self._log = log
        self._clock = clock

    def __call__(self, url: str | None, trigger: str) -> int:
        if url is None or not url.strip() or url.strip() == URL_MARKER:
            return NOT_CONFIGURED
        response = self._site.fetch(url.strip())
        noindex = False
        footer: datetime | None = None
        if response.html is not None:
            noindex, footer = parse_site_html(response.html)
        noindex = noindex or robots_header_is_noindex(response.robots_header)
        record = ProbeRecord(
            checked_at=self._clock.now(),
            trigger=trigger,
            http_status=response.status,
            tls_ok=response.tls_ok,
            error_class=response.error_class,
            noindex=noindex,
            footer_updated_at=footer,
        )
        try:
            self._log.append(record)
        except OSError as exc:
            _logger.warning("phase3.probe_log_failed", extra={"error": type(exc).__name__})
            return FAILED
        return 0


class MeasurePhase3:
    """Evalúa (a), (b) y (e) desde los registros y los `Run`. Solo lectura."""

    def __init__(
        self,
        probes: ProbeLog,
        deliveries: DeliveryLog,
        runs: RunSource,
        clock: Clock,
        tz: ZoneInfo,
    ) -> None:
        self._probes = probes
        self._deliveries = deliveries
        self._runs = runs
        self._clock = clock
        self._tz = tz

    def __call__(self, first_night: date, days: int) -> Phase3Measurement:
        # Un día antes y uno después de la ventana: los `Run` previos fijan el suelo de (b).
        start = datetime.combine(first_night - timedelta(days=1), time.min, self._tz)
        end = datetime.combine(first_night + timedelta(days=days + 1), time.min, self._tz)
        in_window = self._runs.started_between(start, end)
        # El último Run de noche cerrado antes de la ventana: sin Run en N-1 ni en N, (b) se
        # compara con él y no da "sin Run previo" mientras haya un Run más antiguo.
        older = self._runs.last_night_run_finished_before(start)
        finished = [
            r.finished_at
            for r in ([older] if older is not None else []) + in_window
            if is_night_run(r) and r.finished_at is not None
        ]
        return evaluate_phase3(
            first_night,
            days,
            self._probes.read(),
            self._deliveries.read(),
            finished,
            self._tz,
            self._clock.now(),
        )
