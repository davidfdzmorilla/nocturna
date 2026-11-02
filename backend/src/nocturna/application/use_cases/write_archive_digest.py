"""Caso de uso: el redactor escribe el resumen semanal del archivo (T84.r, ADR 0038).

Tercera fuente del redactor (rol `WRITER`, siempre vía `AgentRunner`): redacta,
para cada semana ISO **cerrada** con transiciones de solución por defecto, un
titular y un resumen a partir del bloque `<weekly_digest>` que se construye
aquí, de forma determinista, con los datos de T84. No es un `Finding`: el
texto se guarda en `archive_digest_text` (una fila por semana) y no pasa por el
Editor.

`SelectDigestWeeks.pending()` es solo lectura: sin proveedor, sin
`AgentRunner` y sin `BudgetGuard`, no puede gastar tokens.
`WriteDigestText.__call__` redacta UNA semana: la autorización de gasto, la
llamada, el reintento y el `AgentCall` (con `item_id = None`) viven en
`AgentRunner`; este módulo no recibe `BudgetGuard` ni llama a
`provider.run_agent`. `BudgetDenied` se propaga sin traducir: lo trata
`RunNight`.

Con dos salidas inválidas seguidas se guarda una fila `failed` (la semana no se
reintenta); un fallo del agente o un tiempo agotado no deja fila y la semana
sigue pendiente otra noche.
"""

import hashlib
import logging
import math
from collections import defaultdict
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import date
from uuid import UUID
from zoneinfo import ZoneInfo

from nocturna.application.agents.parsing import InvalidAgentOutput
from nocturna.application.agents.prompt_text import inline, percent_4sf, round_to_error
from nocturna.application.agents.runner import AgentRunner, AttemptOutcome
from nocturna.application.agents.writer_output import parse_digest_writer_output
from nocturna.application.archive_digest_text import parameter_label
from nocturna.application.budget import BudgetDenied
from nocturna.application.measurement_finding_texts import unit_label
from nocturna.application.unit_of_work import AgentWorkFactory
from nocturna.application.use_cases.archive_digest import GetWeeklyDigest
from nocturna.application.use_cases.write_tensions import OUTCOME_BY_ATTEMPT, WriteOutcome
from nocturna.domain.archive import ArchiveParameterValue, ArchiveSolution, limit_of, parameter_unit
from nocturna.domain.archive_digest import (
    DigestEntry,
    DigestText,
    DigestTextStatus,
    ParameterChange,
    TransitionKind,
    WeeklyDigest,
    digest_writer_skip_reason,
)
from nocturna.domain.clock import Clock
from nocturna.domain.entities import MeasuredParameter, MeasurementLimit
from nocturna.domain.errors import InvariantViolation
from nocturna.domain.llm import AgentResult, AgentRole, LLMProvider
from nocturna.domain.repositories import ArchiveDigestReader, ArchiveDigestTextRepository

#: Cuántos nombres de planeta lleva como máximo cada grupo del bloque; el resto se cuenta.
MAX_NAMES_PER_GROUP = 8

_NO_REFERENCE = "(sin referencia)"
_logger = logging.getLogger(__name__)


def _reference(sol: ArchiveSolution | None) -> str:
    if sol is None:
        return _NO_REFERENCE
    return inline(sol.ref_text.strip() or _NO_REFERENCE)


def _sig4(value: float) -> str:
    """Valor con 4 cifras significativas, sin exponente (sin errores que dicten el redondeo)."""
    if value == 0:
        return "0"
    decimals = max(0, 3 - math.floor(math.log10(abs(value))))
    return f"{value:.{decimals}f}"


def _value_text(p: ArchiveParameterValue | None, parameter: MeasuredParameter) -> str:
    """Valor con errores redondeados al error menor y unidad ("1.235 +0.012/-0.012 M⊕");
    "< x" / "> x" si es una cota; "sin valor" si falta."""
    if p is None or p.value is None:
        return "sin valor"
    unit = unit_label(parameter_unit(parameter))
    limit = limit_of(p)
    rounded = round_to_error(p.value, p.err1, p.err2)
    if rounded is not None:
        value, plus, minus = rounded
        figures = f"{value} +{plus}/-{minus}"
    else:
        value = _sig4(p.value)
        figures = value
    if limit == MeasurementLimit.UPPER:
        return f"< {value} {unit}"
    if limit == MeasurementLimit.LOWER:
        return f"> {value} {unit}"
    return f"{figures} {unit}"


def _change_line(change: ParameterChange) -> str:
    text = (
        f"{parameter_label(change.parameter)}: "
        f"{_value_text(change.old, change.parameter)} → {_value_text(change.new, change.parameter)}"
    )
    old, new = change.old, change.new
    if (
        old is not None
        and new is not None
        and old.value is not None
        and new.value is not None
        and old.value != 0
        and limit_of(old) == MeasurementLimit.NONE
        and limit_of(new) == MeasurementLimit.NONE
    ):
        fraction = (new.value - old.value) / old.value
        sign = "-" if fraction < 0 else "+"
        text += f" ({sign}{percent_4sf(fraction)} %)"
    return text


def _grouped(
    entries: list[DigestEntry], reference_of: Callable[[DigestEntry], str]
) -> list[tuple[str, list[str]]]:
    """Nombres de planeta agrupados por referencia: mayor grupo primero, luego por texto."""
    groups: dict[str, list[str]] = defaultdict(list)
    for entry in entries:
        groups[reference_of(entry)].append(inline(entry.pl_name))
    return sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))


def _group_lines(heading: str, entries: list[DigestEntry], side: str) -> list[str]:
    if not entries:
        return []
    lines = [f"detail {heading}:"]
    for reference, names in _grouped(
        entries, lambda e: _reference(e.new if side == "new" else e.old)
    ):
        shown = names[:MAX_NAMES_PER_GROUP]
        listing = ", ".join(shown)
        if len(names) > len(shown):
            listing += f" (y {len(names) - len(shown)} más)"
        lines.append(f"- reference: {reference}; planets: {len(names)}; names: {listing}")
    return lines


def _iso_range(week: str) -> tuple[date, date]:
    year, number = int(week[:4]), int(week[6:8])
    return date.fromisocalendar(year, number, 1), date.fromisocalendar(year, number, 7)


def build_digest_block(digest: WeeklyDigest) -> str:
    """Bloque `<weekly_digest>` de la entrada del redactor: determinista (mismos
    bytes para el mismo resumen) y con todo texto de terceros por `inline`, de
    modo que ningún dato pueda cerrar la marca ni abrir líneas. Valores con la
    regla de redondeo de T96 y porcentajes con `percent_4sf`. Nunca lleva
    `solution_key`, URLs ni `pl_pubdate`/`releasedate` del archivo."""
    first, last = _iso_range(digest.week)
    by_kind: dict[TransitionKind, list[DigestEntry]] = {k: [] for k in TransitionKind}
    for entry in digest.entries:
        by_kind[entry.kind].append(entry)
    lines = [
        "<weekly_digest>",
        f"week: {digest.week} ({first.isoformat()} a {last.isoformat()})",
        f"snapshots: {digest.snapshots}",
    ]
    lines += [f"{kind.value}: {len(by_kind[kind])}" for kind in TransitionKind]

    changed = by_kind[TransitionKind.CHANGED]
    if changed:
        lines.append("detail changed:")
        for entry in changed:
            lines.append(f"- planet: {inline(entry.pl_name)}")
            lines.append(f"  previous_reference: {_reference(entry.old)}")
            lines.append(f"  new_reference: {_reference(entry.new)}")
            if not entry.parameter_changes:
                lines.append("  no_change_in_mass_radius_period")
            for change in entry.parameter_changes:
                lines.append(f"  {_change_line(change)}")
                if change.old_mass_provenance != change.new_mass_provenance:
                    lines.append(
                        "  procedencia de la masa: "
                        f"{inline(change.old_mass_provenance or '-')} → "
                        f"{inline(change.new_mass_provenance or '-')}"
                    )
    lines += _group_lines("new_planet", by_kind[TransitionKind.NEW_PLANET], "new")
    lines += _group_lines("regained", by_kind[TransitionKind.REGAINED], "new")
    lines += _group_lines("lost", by_kind[TransitionKind.LOST], "old")
    lines.append("</weekly_digest>")
    return "\n".join(lines)


def block_sha256(block: str) -> str:
    return hashlib.sha256(block.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class DigestWriterWork:
    """Unidad de trabajo de solo lectura de `pending()`: sin guard ni proveedor."""

    reader: ArchiveDigestReader
    digest_texts: ArchiveDigestTextRepository


DigestWriterWorkFactory = Callable[[], AbstractContextManager[DigestWriterWork]]


@dataclass(frozen=True, slots=True)
class DigestCandidate:
    digest: WeeklyDigest
    block: str
    input_sha256: str


@dataclass(frozen=True, slots=True)
class PendingDigestWeeks:
    """`candidates` en el orden en que se redactan (semana más reciente primero);
    `skipped` son pares `(semana, motivo)` de las semanas con snapshot que no se redactan."""

    candidates: tuple[DigestCandidate, ...]
    skipped: tuple[tuple[str, str], ...]


class SelectDigestWeeks:
    """Elige, solo con lectura, qué semanas cerradas se redactan (T84.r)."""

    def __init__(
        self,
        *,
        work: DigestWriterWorkFactory,
        tz: ZoneInfo,
        since_week: str,
        clock: Clock,
    ) -> None:
        self._work = work
        self._tz = tz
        self._since_week = since_week
        self._clock = clock

    def pending(self) -> PendingDigestWeeks:
        """Semanas por redactar, la más reciente primero. No escribe ni autoriza gasto."""
        now = self._clock.now()
        candidates: list[DigestCandidate] = []
        skipped: list[tuple[str, str]] = []
        with self._work() as w:
            texts = w.digest_texts.all()
            get_digest = GetWeeklyDigest(w.reader, self._tz)
            for week, snapshots in reversed(w.reader.snapshot_weeks(self._tz)):
                if week < self._since_week:
                    # No hace falta calcular el resumen de una semana anterior a `since_week`.
                    digest: WeeklyDigest | None = WeeklyDigest(week, snapshots, ())
                else:
                    digest = get_digest(week)
                if digest is None:
                    continue
                reason = digest_writer_skip_reason(
                    digest,
                    now=now,
                    tz=self._tz,
                    since_week=self._since_week,
                    existing=texts.get(week),
                )
                if reason is not None:
                    skipped.append((week, reason.value))
                    continue
                block = build_digest_block(digest)
                candidates.append(DigestCandidate(digest, block, block_sha256(block)))
        return PendingDigestWeeks(candidates=tuple(candidates), skipped=tuple(skipped))


@dataclass(frozen=True, slots=True)
class WriteDigestResult:
    outcome: WriteOutcome
    text: DigestText | None
    attempts: int
    tokens_spent: int


class WriteDigestText:
    """Redacta el resumen de una semana con el redactor: una conversación nueva por semana.

    Toda la configuración llega por constructor desde `cli.py`. `max_attempts`
    es `limits.max_calls_per_item`; la estimación de coste es
    `budget.writer_estimated_tokens`, la misma que los otros dos redactores.
    """

    def __init__(
        self,
        *,
        work: AgentWorkFactory,
        provider: LLMProvider,
        system_prompt: str,
        prompt_version: str,
        model: str,
        max_turns: int,
        max_attempts: int,
        estimated_tokens: int,
        clock: Clock,
    ) -> None:
        self._work = work
        self._prompt_version = prompt_version
        self._clock = clock
        self._runner = AgentRunner(
            work=work,
            provider=provider,
            role=AgentRole.WRITER,
            model=model,
            system_prompt=system_prompt,
            prompt_version=prompt_version,
            estimated_tokens=estimated_tokens,
            max_turns=max_turns,
            max_attempts=max_attempts,
        )

    def _failed(self, candidate: DigestCandidate, run_id: UUID) -> DigestText:
        return DigestText(
            week=candidate.digest.week,
            status=DigestTextStatus.FAILED,
            input_sha256=candidate.input_sha256,
            prompt_version=self._prompt_version,
            run_id=run_id,
            created_at=self._clock.now(),
        )

    def _store(self, text: DigestText) -> None:
        with self._work() as w:
            if w.digest_texts is None:
                raise InvariantViolation("AgentWork.digest_texts no está configurado")
            w.digest_texts.add(text)

    async def __call__(self, candidate: DigestCandidate) -> WriteDigestResult:
        """Redacta `candidate` (hasta `max_attempts` intentos si el JSON no valida).
        `BudgetDenied` sale sin traducir."""
        week = candidate.digest.week

        # Salida inválida vista en el último intento: si el siguiente `authorize` se
        # deniega, `AgentRunner` lanza `BudgetDenied` y ya no devuelve resultado.
        invalid_run_id: UUID | None = None

        def _build(result: AgentResult, run_id: UUID) -> DigestText:
            nonlocal invalid_run_id
            try:
                parsed = parse_digest_writer_output(result.output_text)
                text = DigestText(
                    week=week,
                    status=DigestTextStatus.WRITTEN,
                    title=parsed.title,
                    summary=parsed.summary,
                    input_sha256=candidate.input_sha256,
                    prompt_version=self._prompt_version,
                    run_id=run_id,
                    created_at=self._clock.now(),
                )
            except (InvalidAgentOutput, InvariantViolation):
                invalid_run_id = run_id
                raise
            invalid_run_id = None
            return text

        try:
            runner_result = await self._runner.run(
                prompt=candidate.block, item_id=None, build=_build
            )
        except BudgetDenied as denial:
            # Un intento inválido y el reintento denegado: la semana queda `failed`
            # (como con dos inválidos) y la denegación sigue su curso en `RunNight`.
            # Si guardar la fila falla, se registra y sale la denegación original.
            if invalid_run_id is not None:
                try:
                    self._store(self._failed(candidate, invalid_run_id))
                except Exception as store_error:
                    _logger.exception(
                        "No se pudo guardar la fila failed del resumen semanal",
                        extra={
                            "event": "digest.failed_row_store_error",
                            "week": week,
                            "error_type": type(store_error).__name__,
                        },
                    )
            raise denial
        text = runner_result.value
        if runner_result.outcome is AttemptOutcome.INVALID_OUTPUT and runner_result.run_id:
            text = self._failed(candidate, runner_result.run_id)
        if text is not None:
            self._store(text)
        return WriteDigestResult(
            outcome=OUTCOME_BY_ATTEMPT[runner_result.outcome],
            text=text,
            attempts=runner_result.attempts,
            tokens_spent=runner_result.tokens_spent,
        )
