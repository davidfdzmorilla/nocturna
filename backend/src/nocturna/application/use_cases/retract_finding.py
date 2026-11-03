"""Caso de uso: retirar un hallazgo ya publicado (T100).

Un hallazgo retirado deja de salir en la lista pública, pero su detalle sigue
accesible con la nota de retirada. No llama a ningún agente: ni
`LLMProvider` ni `BudgetGuard`. El llamante abre la unidad de trabajo
(transacción) y la confirma; con `dry_run` no se escribe nada.
"""

import copy
import logging
from uuid import UUID

from nocturna.domain.clock import Clock
from nocturna.domain.entities import Finding
from nocturna.domain.errors import FindingNotFound
from nocturna.domain.repositories import FindingRepository

_logger = logging.getLogger(__name__)


class RetractFinding:
    """Retira un `Finding` publicado con una nota.

    Lanza `FindingNotFound` si el id no existe e `InvalidTransition` si el
    hallazgo no está publicado o ya está retirado (`Finding.retract`).
    """

    def __init__(self, *, findings: FindingRepository, clock: Clock) -> None:
        self._findings = findings
        self._clock = clock

    def __call__(self, finding_id: UUID, *, note: str, dry_run: bool) -> Finding:
        finding = self._findings.get(finding_id)
        if finding is None:
            raise FindingNotFound(finding_id)
        target = copy.copy(finding) if dry_run else finding
        target.retract(self._clock.now(), note)
        if not dry_run:
            self._findings.save_retraction(target)
        _logger.info(
            "finding.retracted",
            extra={"event": "finding.retracted", "finding_id": str(finding_id), "dry_run": dry_run},
        )
        return target
