"""`TensionEvaluationRepository` en memoria (T88).

Replica la semántica de `SqlAlchemyTensionEvaluationRepository`: orden de
inserción, clave única (reading_id, planet_name, parameter) y `update` que
falla si el `id` no existe.
"""

from __future__ import annotations

from nocturna.domain.tension import TensionEvaluation


class InMemoryTensionEvaluationRepository:
    def __init__(self) -> None:
        self._by_id: dict[object, TensionEvaluation] = {}

    @staticmethod
    def _key(evaluation: TensionEvaluation) -> tuple:
        return (evaluation.reading_id, evaluation.planet_name, evaluation.parameter)

    def all(self) -> list[TensionEvaluation]:
        return list(self._by_id.values())

    def add(self, evaluation: TensionEvaluation) -> None:
        if evaluation.id in self._by_id:
            raise ValueError(f"ya existe TensionEvaluation con id={evaluation.id}")
        if any(self._key(e) == self._key(evaluation) for e in self._by_id.values()):
            raise ValueError("ya existe una evaluación para (reading, planeta, parámetro)")
        self._by_id[evaluation.id] = evaluation

    def update(self, evaluation: TensionEvaluation) -> None:
        if evaluation.id not in self._by_id:
            raise LookupError(f"no existe TensionEvaluation con id={evaluation.id}")
        clash = [
            e
            for e in self._by_id.values()
            if e.id != evaluation.id and self._key(e) == self._key(evaluation)
        ]
        if clash:
            raise ValueError("ya existe una evaluación para (reading, planeta, parámetro)")
        self._by_id[evaluation.id] = evaluation
