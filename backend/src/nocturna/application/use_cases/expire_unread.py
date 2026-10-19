"""Caso de uso: caducar ítems `NEW` que llevan demasiado tiempo sin leerse (T94, ADR 0033).

Sin tokens y sin agentes: solo transiciona `NEW -> EXPIRED` a los ítems de
arXiv sin `exoplanet_match` cuya `published_at` (fecha de la v1) es anterior
a `now - max_age_days`. El llamador pone la unidad de trabajo y el commit.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from nocturna.domain.repositories import ItemRepository


@dataclass(frozen=True, slots=True)
class ExpiryResult:
    cutoff: datetime
    expired: int


def expire_stale_unread(items: ItemRepository, *, now: datetime, max_age_days: int) -> ExpiryResult:
    """Caduca los ítems `NEW` con `published_at < now - max_age_days`.

    Un ítem justo en el límite no caduca. Sin commit: lo hace el llamador.
    """
    cutoff = now - timedelta(days=max_age_days)
    stale = items.stale_unread(cutoff)
    for item in stale:
        item.expire()
        items.save(item)
    return ExpiryResult(cutoff=cutoff, expired=len(stale))
