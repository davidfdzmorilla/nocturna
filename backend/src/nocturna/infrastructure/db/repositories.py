"""Implementaciones SQLAlchemy de los repositorios de dominio.

Cada clase cumple estructuralmente (sin heredar) el `Protocol`
correspondiente de `domain/repositories.py`. No hay `BaseRepository[T]`: la
skill `ddd-conventions` lo rechaza explícitamente como sobrearquitectura, y
cada repositorio tiene formas de consulta lo bastante distintas (un
`add_many` con deduplicación, dos agregados en `AgentCallRepository`, un
`current()` que debe ser ruidoso ante ambigüedad) como para que una base
genérica no ahorrara nada.

**Regla dura**: ningún método de este módulo confirma ni deshace la
transacción de la sesión (ni `commit`, ni `rollback`), ni abre una
transacción explícita. Eso es cosa exclusiva de `unit_of_work` en
`session.py`. Si un repositorio confirmara su propia transacción, un
`AgentCall` y el incremento de `Run.tokens_used` que `application/` escribe
en la misma unidad de trabajo (ver `CLAUDE.md`, control de gasto) podrían
quedar repartidos en dos transacciones distintas; un fallo entre medias
dejaría el acumulado de gasto corto de una llamada que ya se cobró. Los
métodos que lo necesitan usan `flush()` (visibilidad dentro de la misma
transacción, sin cerrarla), nunca confirman la transacción.
"""

from collections.abc import Collection, Sequence
from datetime import datetime
from itertools import batched
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from nocturna.domain.archive import (
    ArchiveSnapshot,
    ArchiveSolution,
    SnapshotDiff,
    SnapshotKind,
)
from nocturna.domain.archive_digest import DefaultTransition, iso_week_of
from nocturna.domain.entities import (
    AgentCall,
    AgentCallStatus,
    Finding,
    FindingType,
    Item,
    ItemStatus,
    Reading,
    Run,
    RunStatus,
)
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.llm import AgentRole
from nocturna.domain.tension import TensionEvaluation
from nocturna.infrastructure.db.mappers import (
    agent_call_to_row,
    apply_tension_evaluation,
    archive_default_change_to_row,
    archive_lost_default_to_row,
    archive_snapshot_from_row,
    archive_snapshot_to_row,
    archive_solution_from_row,
    archive_solution_to_values,
    finding_from_row,
    finding_to_row,
    item_from_row,
    reading_from_row,
    reading_to_row,
    run_from_row,
    run_to_row,
    tension_evaluation_from_row,
    tension_evaluation_to_row,
)
from nocturna.infrastructure.db.models import (
    AgentCallRow,
    ArchiveDefaultChangeRow,
    ArchiveSnapshotRow,
    ArchiveSolutionRow,
    FindingRow,
    ItemRow,
    ReadingRow,
    RunRow,
    TensionEvaluationRow,
)


class SqlAlchemyItemRepository:
    """Persistencia de `Item`. Cumple `domain.repositories.ItemRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add_many(self, items: list[Item]) -> int:
        """Inserta ítems nuevos, deduplicando por `(source, external_id)`.

        Deduplica la lista de entrada antes de tocar la base de datos,
        quedándose con la primera aparición de cada `(source, external_id)`:
        un `INSERT ... ON CONFLICT DO NOTHING` con dos filas duplicadas en el
        mismo lote no está definido de forma útil (PostgreSQL no garantiza
        cuál "gana" dentro de la misma sentencia), así que no se delega esa
        decisión en la base de datos.
        """
        if not items:
            return 0

        deduplicated: dict[tuple[str, str], Item] = {}
        for item in items:
            key = (item.source, item.external_id)
            if key not in deduplicated:
                deduplicated[key] = item

        rows = [
            {
                "id": item.id,
                "source": item.source,
                "external_id": item.external_id,
                "title": item.title,
                "abstract": item.abstract,
                "categories": list(item.categories),
                "published_at": item.published_at,
                "fetched_at": item.fetched_at,
                "status": item.status,
                "exoplanet_match": item.exoplanet_match,
            }
            for item in deduplicated.values()
        ]

        stmt = pg_insert(ItemRow).values(rows)
        stmt = stmt.on_conflict_do_nothing(index_elements=["source", "external_id"])
        # `result.rowcount` no es fiable aquí: con SQLAlchemy 2.0.54 +
        # psycopg + PostgreSQL 16, un `INSERT ... ON CONFLICT DO NOTHING`
        # construido desde el modelo declarativo devuelve `rowcount == -1`
        # aunque las filas se inserten correctamente. `.returning(ItemRow.id)`
        # sí es fiable: `ON CONFLICT DO NOTHING` solo deja pasar por el
        # `RETURNING` las filas que de verdad se insertaron, así que contar
        # las filas devueltas da el número real de ítems nuevos.
        stmt = stmt.returning(ItemRow.id)
        result = self._session.execute(stmt)
        return len(result.all())

    def get(self, item_id: UUID) -> Item | None:
        row = self._session.get(ItemRow, item_id)
        return item_from_row(row) if row is not None else None

    def next_unread(self, limit: int) -> list[Item]:
        # T79: los ítems marcados `exoplanet_match` van primero, para que con
        # `limit` (max_items_per_night) los candidatos a `reader-v3` no queden
        # detrás de ítems de v2. Dentro de cada grupo, orden de llegada:
        # `fetched_at ASC` refleja cuándo entró el ítem a la base. No basta
        # como criterio único: dos ítems de la misma ingesta pueden compartir
        # `fetched_at` al milisegundo, y sin desempate PostgreSQL puede
        # devolverlos en cualquier orden entre ellos. `external_id ASC`
        # desempata de forma determinista para que `run-item` y una
        # re-ejecución de la noche vean siempre el mismo orden. El LIMIT se
        # aplica después de ordenar.
        stmt = (
            select(ItemRow)
            .where(ItemRow.status == ItemStatus.NEW)
            .order_by(
                ItemRow.exoplanet_match.desc(),
                ItemRow.fetched_at.asc(),
                ItemRow.external_id.asc(),
            )
            .limit(limit)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [item_from_row(row) for row in rows]

    def save(self, item: Item) -> None:
        row = self._session.get(ItemRow, item.id)
        if row is None:
            raise LookupError(f"no existe Item con id={item.id}")
        row.status = item.status


class SqlAlchemyReadingRepository:
    """Persistencia de `Reading`. Cumple `domain.repositories.ReadingRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, reading: Reading) -> None:
        self._session.add(reading_to_row(reading))

    def get_for_item(self, item_id: UUID) -> Reading | None:
        stmt = select(ReadingRow).where(
            ReadingRow.item_id == item_id, ReadingRow.superseded_at.is_(None)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return reading_from_row(row) if row is not None else None

    def supersede(self, previous_id: UUID, reading: Reading) -> None:
        """Marca `previous_id` como sustituida y añade `reading` como vigente.

        El `UPDATE` es explícito y se vacía (`flush`) antes del `INSERT`: el
        índice único parcial `uq_readings_item_id_current` rechazaría dos
        vigentes a la vez, y el orden del unit-of-work de SQLAlchemy no está
        garantizado. `rowcount != 1` significa que la previa no existe, ya
        estaba sustituida o es de otro ítem.
        """
        result = self._session.execute(
            update(ReadingRow)
            .where(
                ReadingRow.id == previous_id,
                ReadingRow.item_id == reading.item_id,
                ReadingRow.superseded_at.is_(None),
            )
            .values(superseded_at=func.now())
        )
        if result.rowcount != 1:  # type: ignore[attr-defined]
            raise InvariantViolation(
                f"la lectura {previous_id} no es la vigente del ítem {reading.item_id}"
            )
        self._session.flush()
        self._session.add(reading_to_row(reading))
        self._session.flush()

    def with_measurements(self) -> list[Reading]:
        """Lecturas con `measurements IS NOT NULL`, ordenadas por `id`.

        Solo lecturas vigentes (`superseded_at IS NULL`, T82).

        `JSONB(none_as_null=True)` guarda `None` como SQL NULL y `()` como
        `[]`, así que `IS NOT NULL` separa "no extraído" de "sin medidas".
        """
        stmt = (
            select(ReadingRow)
            .where(ReadingRow.measurements.is_not(None), ReadingRow.superseded_at.is_(None))
            .order_by(ReadingRow.id)
        )
        return [reading_from_row(row) for row in self._session.execute(stmt).scalars()]


class SqlAlchemyFindingRepository:
    """Persistencia de `Finding`. Cumple `domain.repositories.FindingRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, finding: Finding) -> None:
        self._session.add(finding_to_row(finding))

    def unpublished_for_run(self, run_id: UUID) -> list[Finding]:
        """Candidatos pendientes de decisión del Editor, en orden estable por `id`.

        `ORDER BY id` es deliberado, y deliberadamente *no* es
        `ORDER BY interest_score DESC` (ni ningún otro campo de negocio):
        sin este `ORDER BY`, el orden en el que PostgreSQL devuelve las
        filas es indefinido, así que dos noches con exactamente los mismos
        candidatos podrían producir dos prompts distintos para el Editor
        -- que recibe todos los candidatos de la noche en una sola llamada
        -- y eso vuelve irreproducible la calibración de T60 (ver
        `docs/TECHNICAL_DEBT.md`, entrada de T43). Ordenar por
        `interest_score` (o cualquier otro criterio de negocio) sería
        colar aquí una política de priorización -- qué candidato se
        divulga primero cuando el presupuesto aprieta -- que es una
        decisión abierta de T44, no un arreglo de reproducibilidad. `id`
        solo da determinismo, nada de prioridad editorial.
        """
        stmt = (
            select(FindingRow)
            .where(FindingRow.run_id == run_id, FindingRow.published_at.is_(None))
            .order_by(FindingRow.id)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [finding_from_row(row) for row in rows]

    def save(self, finding: Finding) -> None:
        row = self._session.get(FindingRow, finding.id)
        if row is None:
            raise LookupError(f"no existe Finding con id={finding.id}")
        row.confidence = finding.confidence
        row.published_at = finding.published_at

    def published_page(
        self, limit: int, offset: int, *, finding_type: FindingType | None = None
    ) -> list[Finding]:
        """Página de hallazgos publicados para la web de solo lectura.

        `published_at IS NOT NULL` es el único filtro de publicación (T50):
        `published_at` y `confidence` solo se escriben juntos en
        `Finding.publish()`, respaldado por el `CHECK
        confidence_published_at_together` de `models.py`. No se cruza con
        `items.status`, que sería una segunda fuente de verdad divergente
        (`docs/OPEN_DECISIONS.md`).

        `ORDER BY published_at DESC, id DESC`: el desempate por `id` no es
        cosmético. El Editor publica todos los hallazgos de una noche en el
        mismo instante (`EditNight` usa un único `at` para todo el lote), así
        que los empates de `published_at` son la norma, no la excepción. Sin
        desempate estable, dos páginas consecutivas de la misma consulta
        pueden repetir u omitir filas (la lección de `unpublished_for_run`,
        ver `docs/TECHNICAL_DEBT.md`).

        `finding_type` (T77) añade `type = :t` a la misma sentencia, sin
        sustituir el filtro de publicación. Sin índice: el volumen es mínimo.
        """
        stmt = select(FindingRow).where(FindingRow.published_at.is_not(None))
        if finding_type is not None:
            stmt = stmt.where(FindingRow.type == finding_type)
        stmt = (
            stmt.order_by(FindingRow.published_at.desc(), FindingRow.id.desc())
            .limit(limit)
            .offset(offset)
        )
        rows = self._session.execute(stmt).scalars().all()
        return [finding_from_row(row) for row in rows]

    def count_published(self, *, finding_type: FindingType | None = None) -> int:
        """Total de hallazgos publicados, mismo filtro que `published_page`."""
        stmt = select(func.count()).where(FindingRow.published_at.is_not(None))
        if finding_type is not None:
            stmt = stmt.where(FindingRow.type == finding_type)
        return self._session.execute(stmt).scalar_one()

    def get_published(self, finding_id: UUID) -> Finding | None:
        """Recupera un hallazgo por id, solo si está publicado.

        El filtro `published_at IS NOT NULL` viaja en la misma sentencia que
        el id, no un `get()` genérico que el llamante filtre después: un
        hallazgo candidato que el Editor todavía no publicó (o que rechazó)
        no puede salir por la API ni siquiera un instante.
        """
        stmt = select(FindingRow).where(
            FindingRow.id == finding_id, FindingRow.published_at.is_not(None)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return finding_from_row(row) if row is not None else None

    def evaluation_ids_with_finding(self, type: FindingType) -> frozenset[UUID]:
        """Evaluaciones con `Finding` de ese tipo, publicado o no (T89)."""
        stmt = select(FindingRow.tension_evaluation_id).where(
            FindingRow.type == type, FindingRow.tension_evaluation_id.is_not(None)
        )
        return frozenset(self._session.execute(stmt).scalars().all())

    def count_for_run(self, run_id: UUID, types: Collection[FindingType]) -> int:
        stmt = (
            select(func.count())
            .select_from(FindingRow)
            .where(FindingRow.run_id == run_id, FindingRow.type.in_(list(types)))
        )
        return int(self._session.execute(stmt).scalar_one())


class SqlAlchemyRunRepository:
    """Persistencia de `Run`. Cumple `domain.repositories.RunRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, run: Run) -> None:
        self._session.add(run_to_row(run))

    def get(self, run_id: UUID) -> Run | None:
        row = self._session.get(RunRow, run_id)
        return run_from_row(row) if row is not None else None

    def current(self) -> Run | None:
        # `scalar_one_or_none()`, no `first()`: el índice único parcial
        # `uq_runs_status_running` (models.py) ya impide dos filas `running`
        # a nivel de base de datos, pero si alguna vez hubiera dos, quiero
        # un error ruidoso aquí, no que este método elija una en silencio.
        stmt = select(RunRow).where(RunRow.status == RunStatus.RUNNING)
        row = self._session.execute(stmt).scalar_one_or_none()
        return run_from_row(row) if row is not None else None

    def save(self, run: Run) -> None:
        """Persiste el estado actual del `Run`, incluido `tokens_used`.

        `runs.tokens_used` es una **caché desnormalizada**: la fuente de
        verdad del gasto de la noche es la suma de `agent_calls`
        (`AgentCallRepository.tokens_used_for_run`). Este método escribe lo
        que traiga la entidad `Run` en memoria, sea lo que sea, incluido un
        valor manipulado o desincronizado; no lo recalcula ni lo valida
        contra la base. Por eso `BudgetGuard` (T30) nunca debe leer
        `Run.tokens_used` para decidir si autoriza una llamada: debe leer
        siempre `tokens_used_for_run`.
        """
        row = self._session.get(RunRow, run.id)
        if row is None:
            raise LookupError(f"no existe Run con id={run.id}")
        row.status = run.status
        row.finished_at = run.finished_at
        row.tokens_used = run.tokens_used
        row.items_fetched = run.items_fetched
        row.items_read = run.items_read
        row.findings_published = run.findings_published
        row.notes = run.notes


class SqlAlchemyAgentCallRepository:
    """Persistencia de `AgentCall`. Cumple `domain.repositories.AgentCallRepository`."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, call: AgentCall) -> None:
        self._session.add(agent_call_to_row(call))

    def tokens_used_for_run(self, run_id: UUID) -> int:
        """Suma de `tokens_in + tokens_out` de todas las llamadas del Run.

        Cuenta llamadas de **cualquier** `status`, no solo `ok`: una llamada
        que acabó en `timeout` o `error` también consumió tokens de la
        suscripción, y filtrar por `ok` regalaría presupuesto que ya se
        gastó. No "arreglar" esto añadiendo un `WHERE status = 'ok'`.

        `COALESCE(..., 0)` es imprescindible: sin llamadas registradas,
        `SUM` devuelve `NULL` en SQL, y `BudgetGuard` espera un `int`, no un
        `None`.
        """
        stmt = select(
            func.coalesce(func.sum(AgentCallRow.tokens_in + AgentCallRow.tokens_out), 0)
        ).where(AgentCallRow.run_id == run_id)
        return self._session.execute(stmt).scalar_one()

    def count_for_run(self, run_id: UUID, agent: AgentRole) -> int:
        """Número de llamadas registradas para `run_id` y `agent`.

        Cuenta llamadas de **cualquier** `status`, incluidos `invalid_output`,
        `error` y `timeout`, no solo las que llegaron a producir una salida
        válida. Decisión de T30 (ADR 0005): lo que cuesta presupuesto de la
        suscripción es el intento, no el acierto, así que el tope se
        comprueba sobre todas las llamadas, de cualquier estado. La clave
        que hace esto compatible con la regla de reintento por JSON
        inválido ("si el JSON no valida, un reintento; si falla de nuevo, el
        ítem se marca failed") es `max_editor_calls_per_night = 2`
        (`pipeline.toml`): con tope 1, un primer intento fallido ya
        agotaría "la llamada de la noche" y consumiría el reintento en
        silencio; con tope 2, el reintento sigue disponible aunque se
        cuenten los intentos y no solo los aciertos. `BudgetGuard` usa este
        mismo conteo también para Reader y Popularizer, vía
        `max_calls_per_item * max_items_per_night`.
        """
        stmt = select(func.count()).where(
            AgentCallRow.run_id == run_id, AgentCallRow.agent == agent
        )
        return self._session.execute(stmt).scalar_one()

    def count_for_item(self, item_id: UUID, agent: AgentRole, status: AgentCallStatus) -> int:
        """Llamadas de `agent` sobre `item_id` con `status`, en todas las noches y Runs."""
        stmt = select(func.count()).where(
            AgentCallRow.item_id == item_id,
            AgentCallRow.agent == agent,
            AgentCallRow.status == status,
        )
        return self._session.execute(stmt).scalar_one()

    def count_runs_with_prompt_version(self, prompt_version: str) -> int:
        """Runs de noche distintos con al menos una llamada de esa
        `prompt_version`. Excluye los Runs de relectura (`notes = 'reread'`,
        D9 de T82): el criterio de T75 cuenta noches de `run-night`."""
        stmt = (
            select(func.count(func.distinct(AgentCallRow.run_id)))
            .join(RunRow, RunRow.id == AgentCallRow.run_id)
            .where(
                AgentCallRow.prompt_version == prompt_version,
                RunRow.notes.is_distinct_from("reread"),
            )
        )
        return self._session.execute(stmt).scalar_one()


_UPSERT_CHUNK = 1000

# Campos que se refrescan en cada snapshot que ve la solucion. Los que entran
# en `solution_key` (nombre, referencia, soltype, valores) no se tocan: si
# cambiaran, seria otra solucion. `first_seen_snapshot_id` tampoco.
_REFRESHED_ON_CONFLICT = (
    "hostname",
    "pl_refname",
    "ref_text",
    "arxiv_id",
    "releasedate",
    "pl_pubdate",
    "pl_bmassprov",
    "st_rad_value",
    "st_rad_err1",
    "st_rad_err2",
    "st_mass_value",
    "st_mass_err1",
    "st_mass_err2",
    "discoverymethod",
    "ttv_flag",
    "pl_controv_flag",
    "is_default",
    "last_seen_snapshot_id",
    "removed_at",
)


class SqlAlchemyArchiveRepository:
    """Persistencia del snapshot del Exoplanet Archive (T81).

    Cumple `domain.repositories.ArchiveRepository`. Como el resto de
    repositorios, no confirma ni deshace: `save_snapshot` es atomico porque
    todas sus sentencias van en la transaccion de la sesion que le dan, y
    `unit_of_work` la confirma o la deshace entera.
    """

    def __init__(self, session: Session) -> None:
        self._session = session

    def last_snapshot(self) -> ArchiveSnapshot | None:
        stmt = select(ArchiveSnapshotRow).order_by(ArchiveSnapshotRow.taken_at.desc()).limit(1)
        row = self._session.execute(stmt).scalar_one_or_none()
        return None if row is None else archive_snapshot_from_row(row)

    def last_full_snapshot(self) -> ArchiveSnapshot | None:
        stmt = (
            select(ArchiveSnapshotRow)
            .where(ArchiveSnapshotRow.kind == SnapshotKind.FULL)
            .order_by(ArchiveSnapshotRow.taken_at.desc())
            .limit(1)
        )
        row = self._session.execute(stmt).scalar_one_or_none()
        return None if row is None else archive_snapshot_from_row(row)

    def active_keys(self, planets: Collection[str] | None = None) -> dict[str, str]:
        stmt = select(ArchiveSolutionRow.solution_key, ArchiveSolutionRow.pl_name).where(
            ArchiveSolutionRow.removed_at.is_(None)
        )
        if planets is None:
            return {key: name for key, name in self._session.execute(stmt)}
        result: dict[str, str] = {}
        for chunk in batched(sorted(set(planets)), _UPSERT_CHUNK):
            chunk_stmt = stmt.where(ArchiveSolutionRow.pl_name.in_(chunk))
            result.update({key: name for key, name in self._session.execute(chunk_stmt)})
        return result

    def removed_keys(self, keys: Collection[str]) -> frozenset[str]:
        found: set[str] = set()
        for chunk in batched(sorted(set(keys)), _UPSERT_CHUNK):
            stmt = select(ArchiveSolutionRow.solution_key).where(
                ArchiveSolutionRow.solution_key.in_(chunk),
                ArchiveSolutionRow.removed_at.is_not(None),
            )
            found.update(self._session.execute(stmt).scalars())
        return frozenset(found)

    def current_defaults(self) -> dict[str, str]:
        stmt = select(ArchiveSolutionRow.pl_name, ArchiveSolutionRow.solution_key).where(
            ArchiveSolutionRow.is_default_current.is_(True)
        )
        return {name: key for name, key in self._session.execute(stmt)}

    def planet_names(self) -> frozenset[str]:
        stmt = select(ArchiveSolutionRow.pl_name).where(ArchiveSolutionRow.removed_at.is_(None))
        return frozenset(self._session.execute(stmt).scalars())

    def active_solutions(self, pl_name: str) -> list[tuple[ArchiveSolution, bool]]:
        stmt = (
            select(ArchiveSolutionRow)
            .where(ArchiveSolutionRow.pl_name == pl_name, ArchiveSolutionRow.removed_at.is_(None))
            .order_by(ArchiveSolutionRow.solution_key)
        )
        return [
            (archive_solution_from_row(row), row.is_default_current)
            for row in self._session.execute(stmt).scalars()
        ]

    def save_snapshot(
        self,
        snapshot: ArchiveSnapshot,
        solutions: Sequence[ArchiveSolution],
        diff: SnapshotDiff,
    ) -> None:
        """Persiste snapshot, soluciones y diff.

        `solutions` debe venir sin claves repetidas (`collapse_duplicates`):
        dos filas con la misma clave en un mismo bloque harian fallar el
        `ON CONFLICT`.

        Estado `is_default_current` tras el snapshot, coherente con
        `diff_snapshot`: se limpia para los planetas con cambio de default o
        default perdido y para las claves dadas de baja, y se activa para las
        soluciones vistas con `is_default`. Los planetas fuera del alcance de
        un incremental conservan su default. Una clave reactivada vuelve con
        `is_default_current` segun lo que se vea hoy, no con el que tenia.
        """
        self._session.add(archive_snapshot_to_row(snapshot))
        # FK de las soluciones: el snapshot debe existir antes que ellas.
        self._session.flush()

        # T84: el default vigente de cada planeta perdido se lee ANTES de
        # limpiar `is_default_current` (las bajas y los cambios de default lo
        # apagan mas abajo).
        lost_old_keys: dict[str, str] = {}
        for chunk in batched(sorted(diff.lost_defaults), _UPSERT_CHUNK):
            lost_stmt = select(ArchiveSolutionRow.pl_name, ArchiveSolutionRow.solution_key).where(
                ArchiveSolutionRow.pl_name.in_(chunk),
                ArchiveSolutionRow.is_default_current.is_(True),
            )
            lost_old_keys.update({name: key for name, key in self._session.execute(lost_stmt)})

        # Una sola sentencia compilada y ejecutada por bloques de parametros
        # (insertmanyvalues): compilar un `VALUES` de 1.000 filas por bloque
        # costaba ~0,3 s cada vez.
        insert_stmt = pg_insert(ArchiveSolutionRow.__table__)
        upsert_stmt = insert_stmt.on_conflict_do_update(
            index_elements=["solution_key"],
            set_={name: insert_stmt.excluded[name] for name in _REFRESHED_ON_CONFLICT},
        )
        for chunk in batched(solutions, _UPSERT_CHUNK):
            self._session.execute(
                upsert_stmt,
                [archive_solution_to_values(s, snapshot_id=snapshot.id) for s in chunk],
            )

        for chunk in batched(diff.removed, _UPSERT_CHUNK):
            self._session.execute(
                update(ArchiveSolutionRow)
                .where(ArchiveSolutionRow.solution_key.in_(chunk))
                .values(removed_at=snapshot.taken_at, is_default_current=False)
            )

        stale_planets = {c.pl_name for c in diff.default_changes} | set(diff.lost_defaults)
        for chunk in batched(sorted(stale_planets), _UPSERT_CHUNK):
            self._session.execute(
                update(ArchiveSolutionRow)
                .where(
                    ArchiveSolutionRow.pl_name.in_(chunk),
                    ArchiveSolutionRow.is_default_current.is_(True),
                )
                .values(is_default_current=False)
            )
        seen_default_keys = [s.solution_key for s in solutions if s.is_default]
        for chunk in batched(seen_default_keys, _UPSERT_CHUNK):
            self._session.execute(
                update(ArchiveSolutionRow)
                .where(ArchiveSolutionRow.solution_key.in_(chunk))
                .values(is_default_current=True)
            )

        self._session.add_all(
            archive_default_change_to_row(c, snapshot_id=snapshot.id, detected_at=snapshot.taken_at)
            for c in diff.default_changes
        )
        # `lost_defaults` sale de `current_defaults`: un planeta sin clave vigente
        # en la base es una incoherencia, no algo que omitir en silencio.
        missing = [n for n in sorted(diff.lost_defaults) if n not in lost_old_keys]
        if missing:
            raise InvariantViolation(
                f"default perdido sin clave vigente en la base para {missing[:5]!r}"
            )
        self._session.add_all(
            archive_lost_default_to_row(
                name, lost_old_keys[name], snapshot_id=snapshot.id, detected_at=snapshot.taken_at
            )
            for name in sorted(diff.lost_defaults)
        )
        self._session.flush()


class SqlAlchemyArchiveDigestReader:
    """Lectura del resumen semanal (T84). Cumple `domain.repositories.ArchiveDigestReader`;
    solo emite SELECT."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def snapshot_weeks(self, tz: ZoneInfo) -> list[tuple[str, int]]:
        taken = self._session.execute(select(ArchiveSnapshotRow.taken_at)).scalars()
        counts: dict[str, int] = {}
        for at in taken:
            week = iso_week_of(at, tz)
            counts[week] = counts.get(week, 0) + 1
        return sorted(counts.items())

    def transitions_between(self, start: datetime, end: datetime) -> list[DefaultTransition]:
        change = ArchiveDefaultChangeRow
        snap = ArchiveSnapshotRow
        earlier = ArchiveSnapshotRow.__table__.alias("earlier")
        seen_before = exists().where(
            ArchiveSolutionRow.pl_name == change.pl_name,
            ArchiveSolutionRow.first_seen_snapshot_id == earlier.c.id,
            earlier.c.taken_at < snap.taken_at,
        )
        stmt = (
            select(
                change.pl_name,
                change.old_solution_key,
                change.new_solution_key,
                snap.taken_at,
                seen_before,
            )
            .join(snap, snap.id == change.snapshot_id)
            .where(snap.taken_at >= start, snap.taken_at < end)
            .order_by(snap.taken_at, change.pl_name, change.id)
        )
        return [
            DefaultTransition(
                pl_name=name,
                old_key=old,
                new_key=new,
                snapshot_taken_at=at,
                planet_seen_before=bool(seen),
            )
            for name, old, new, at, seen in self._session.execute(stmt)
        ]

    def solutions_by_key(self, keys: Collection[str]) -> dict[str, ArchiveSolution]:
        result: dict[str, ArchiveSolution] = {}
        for chunk in batched(sorted(set(keys)), _UPSERT_CHUNK):
            stmt = select(ArchiveSolutionRow).where(ArchiveSolutionRow.solution_key.in_(chunk))
            for row in self._session.execute(stmt).scalars():
                result[row.solution_key] = archive_solution_from_row(row)
        return result


class SqlAlchemyTensionEvaluationRepository:
    """Persistencia de `TensionEvaluation` (T88). Cumple
    `domain.repositories.TensionEvaluationRepository`; no confirma ni deshace."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def all(self) -> list[TensionEvaluation]:
        stmt = select(TensionEvaluationRow).order_by(
            TensionEvaluationRow.first_evaluated_at, TensionEvaluationRow.id
        )
        return [tension_evaluation_from_row(r) for r in self._session.execute(stmt).scalars()]

    def add(self, evaluation: TensionEvaluation) -> None:
        self._session.add(tension_evaluation_to_row(evaluation))
        self._session.flush()

    def update(self, evaluation: TensionEvaluation) -> None:
        row = self._session.get(TensionEvaluationRow, evaluation.id)
        if row is None:
            raise LookupError(f"no existe TensionEvaluation con id={evaluation.id}")
        apply_tension_evaluation(row, evaluation)
        self._session.flush()
