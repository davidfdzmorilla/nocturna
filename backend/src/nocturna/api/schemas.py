"""Esquemas Pydantic de respuesta de la API de lectura (T50 paso 3).

Lo que **no** sale en ningún esquema, deliberadamente, es tan importante
como lo que sale:

- `confidence`: nota editorial interna del Editor. Junto a un texto
  generado por IA se leería como "grado de certeza científica", justo lo
  contrario del banner obligatorio de `CLAUDE.md` ("Análisis generado
  automáticamente por IA. No es un resultado científico validado.").
- `run_id`: telemetría del pipeline. Revela la cadencia y el tamaño de las
  noches del autor.
- `item_id`: el identificador público de un hallazgo es `finding.id`; no
  hay ninguna vista pública de `Item` en fase 1.

- `tension_evaluation_id`, `solution_key`, `soltype`, `pl_pubdate`,
  `releasedate` de la previa, `ttv_flag`, `window_days`,
  `paper_published_at`, `limit` y `origin`: metadatos internos del cálculo.

Los payloads de `catalog_tension`, `first_measurement` e
`independent_confirmation` se exponen con una lista blanca de campos
construida a mano en cada `from_domain` (T77): nunca se vuelca `to_json()`
ni `model_dump()` de una entidad, así un campo nuevo del dominio no cruza
hacia fuera sin una decisión explícita aquí.

Ningún esquema envuelve un `Reading` ni un `Run`: ninguno de los dos se
publica nunca.
"""

from collections.abc import Callable
from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from nocturna.domain.archive import ArchiveParameterValue, ArchiveSolution, limit_of, parameter_unit
from nocturna.domain.archive_digest import (
    DigestEntry,
    ParameterChange,
    TransitionKind,
    WeeklyDigest,
)
from nocturna.domain.entities import (
    ArchiveStatus,
    CatalogSolution,
    CatalogTension,
    CatalogTensionComparison,
    ConfirmationReference,
    FindingType,
    FirstMeasurement,
    IndependentConfirmation,
    MeasuredParameter,
    Measurement,
    MeasurementLimit,
    MeasurementUnit,
    PaperMeasurement,
)


def source_url(source: str, external_id: str) -> str | None:
    """URL pública del ítem original, o `None` si la fuente no se conoce.

    Fase 1 solo ingesta arXiv (`CLAUDE.md`); cualquier otra fuente futura
    que no sepamos enlazar debe degradar a `None` en vez de construir una
    URL inventada.
    """
    if source == "arxiv":
        return f"https://arxiv.org/abs/{external_id}"
    return None


class MeasurementValueOut(BaseModel):
    """Valor medido con sus dos errores y su unidad (medida del paper sin `evidence`)."""

    model_config = ConfigDict(frozen=True)

    value: float
    err_plus: float
    err_minus: float
    unit: MeasurementUnit

    @classmethod
    def from_domain(cls, measurement: PaperMeasurement) -> "MeasurementValueOut":
        return cls(
            value=measurement.value,
            err_plus=measurement.err_plus,
            err_minus=measurement.err_minus,
            unit=measurement.unit,
        )


class PaperMeasurementWithEvidenceOut(BaseModel):
    """Medida del paper en un `catalog_tension`, con la cita literal del abstract."""

    model_config = ConfigDict(frozen=True)

    planet_name: str
    value: float
    err_plus: float
    err_minus: float
    unit: MeasurementUnit
    evidence: str

    @classmethod
    def from_domain(cls, measurement: Measurement) -> "PaperMeasurementWithEvidenceOut":
        # `usable_for_tension` garantiza errores bilaterales; el dominio ya lo
        # exige en `CatalogTension`, esto solo estrecha el tipo.
        if measurement.err_plus is None or measurement.err_minus is None:
            raise ValueError("la medida de una tensión debe tener los dos errores")
        return cls(
            planet_name=measurement.planet_name,
            value=measurement.value,
            err_plus=measurement.err_plus,
            err_minus=measurement.err_minus,
            unit=measurement.unit,
            evidence=measurement.evidence,
        )


class PriorSolutionOut(BaseModel):
    """Solución previa del archivo frente a la que se compara la medida."""

    model_config = ConfigDict(frozen=True)

    reference: str
    value: float
    err_plus: float
    err_minus: float
    unit: MeasurementUnit
    is_default: bool
    arxiv_id: str | None

    @classmethod
    def from_domain(cls, solution: CatalogSolution) -> "PriorSolutionOut":
        if solution.err_plus is None or solution.err_minus is None:
            raise ValueError("la previa de una tensión debe tener los dos errores")
        return cls(
            reference=solution.reference,
            value=solution.value,
            err_plus=solution.err_plus,
            err_minus=solution.err_minus,
            unit=solution.unit,
            is_default=solution.is_default,
            arxiv_id=solution.arxiv_id,
        )


class TensionComparisonOut(BaseModel):
    """Una medida del paper frente a una previa, con su diferencia en σ."""

    model_config = ConfigDict(frozen=True)

    paper: PaperMeasurementWithEvidenceOut
    prior: PriorSolutionOut
    sigma: float

    @classmethod
    def from_domain(cls, comparison: CatalogTensionComparison) -> "TensionComparisonOut":
        return cls(
            paper=PaperMeasurementWithEvidenceOut.from_domain(comparison.paper),
            prior=PriorSolutionOut.from_domain(comparison.prior),
            sigma=comparison.sigma,
        )


class CatalogTensionOut(BaseModel):
    """Datos del contraste de un `catalog_tension` (T72)."""

    model_config = ConfigDict(frozen=True)

    planet_name: str
    parameter: MeasuredParameter
    archive_url: str
    threshold_sigma: float
    reference_sigma: float
    comparisons: list[TensionComparisonOut]

    @classmethod
    def from_domain(cls, tension: CatalogTension) -> "CatalogTensionOut":
        return cls(
            planet_name=tension.planet_name,
            parameter=tension.parameter,
            archive_url=tension.archive_url,
            threshold_sigma=tension.threshold_sigma,
            reference_sigma=tension.reference_sigma,
            comparisons=[TensionComparisonOut.from_domain(c) for c in tension.comparisons],
        )


class FirstMeasurementOut(BaseModel):
    """Datos de una `primera_medida` (T89)."""

    model_config = ConfigDict(frozen=True)

    paper_planet_name: str
    archive_planet_name: str | None
    parameter: MeasuredParameter
    archive_status: ArchiveStatus
    archive_url: str | None
    measurements: list[MeasurementValueOut]

    @classmethod
    def from_domain(cls, first: FirstMeasurement) -> "FirstMeasurementOut":
        return cls(
            paper_planet_name=first.paper_planet_name,
            archive_planet_name=first.archive_planet_name,
            parameter=first.parameter,
            archive_status=first.archive_status,
            archive_url=first.archive_url,
            measurements=[MeasurementValueOut.from_domain(m) for m in first.measurements],
        )


class ConfirmationReferenceOut(BaseModel):
    """Solución del archivo frente a la que se confirma."""

    model_config = ConfigDict(frozen=True)

    refname: str
    arxiv_id: str | None
    value: float
    err_plus: float
    err_minus: float
    unit: MeasurementUnit
    releasedate: date

    @classmethod
    def from_domain(cls, reference: ConfirmationReference) -> "ConfirmationReferenceOut":
        return cls(
            refname=reference.refname,
            arxiv_id=reference.arxiv_id,
            value=reference.value,
            err_plus=reference.err_plus,
            err_minus=reference.err_minus,
            unit=reference.unit,
            releasedate=reference.releasedate,
        )


class IndependentConfirmationOut(BaseModel):
    """Datos de una `confirmacion_independiente` (T89)."""

    model_config = ConfigDict(frozen=True)

    paper_planet_name: str
    archive_planet_name: str
    parameter: MeasuredParameter
    archive_url: str
    measurements: list[MeasurementValueOut]
    reference: ConfirmationReferenceOut
    sigmas: list[float]
    max_sigma: float

    @classmethod
    def from_domain(cls, confirmation: IndependentConfirmation) -> "IndependentConfirmationOut":
        return cls(
            paper_planet_name=confirmation.paper_planet_name,
            archive_planet_name=confirmation.archive_planet_name,
            parameter=confirmation.parameter,
            archive_url=confirmation.archive_url,
            measurements=[MeasurementValueOut.from_domain(m) for m in confirmation.measurements],
            reference=ConfirmationReferenceOut.from_domain(confirmation.reference),
            sigmas=list(confirmation.sigmas),
            max_sigma=confirmation.max_sigma,
        )


class FindingSummary(BaseModel):
    """Entrada del listado paginado (`GET /findings`). Sin `source_url`:
    el listado no hace join con `Item`, así se evita el N+1. Lleva `type`
    pero no los payloads (T77)."""

    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    published_at: datetime
    type: FindingType
    level_curious: str


class FindingDetail(BaseModel):
    """Detalle de un hallazgo publicado (`GET /findings/{finding_id}`).

    Cada payload es `null` salvo el de su tipo, como en el dominio y en los
    CHECK "si y solo si" de la base (T77).
    """

    model_config = ConfigDict(frozen=True)

    id: UUID
    title: str
    published_at: datetime
    type: FindingType
    level_curious: str
    level_amateur: str
    level_technical: str
    source_url: str | None
    catalog_tension: CatalogTensionOut | None
    first_measurement: FirstMeasurementOut | None
    independent_confirmation: IndependentConfirmationOut | None


class FindingsPageResponse(BaseModel):
    """Página de hallazgos publicados."""

    model_config = ConfigDict(frozen=True)

    items: list[FindingSummary]
    page: int
    size: int
    total: int


class DigestReferenceOut(BaseModel):
    """Referencia (solución por defecto) de un lado de un cambio del archivo (T84).

    Lista blanca: texto visible de la referencia y sus fechas; nunca la clave
    de la solución ni metadatos internos del snapshot.
    """

    model_config = ConfigDict(frozen=True)

    reference: str
    pl_pubdate: str | None
    releasedate: date

    @classmethod
    def from_domain(cls, solution: ArchiveSolution) -> "DigestReferenceOut":
        return cls(
            reference=solution.ref_text.strip() or "(sin referencia)",
            pl_pubdate=solution.pl_pubdate,
            releasedate=solution.releasedate,
        )


class ArchiveValueOut(BaseModel):
    """Valor de masa, radio o periodo del archivo, con errores en valor absoluto,
    cota (`bound`) y unidad. `value` nulo si el archivo no lo da."""

    model_config = ConfigDict(frozen=True)

    value: float | None
    err_plus: float | None
    err_minus: float | None
    bound: MeasurementLimit | None
    unit: MeasurementUnit

    @classmethod
    def from_domain(
        cls, value: ArchiveParameterValue | None, parameter: MeasuredParameter
    ) -> "ArchiveValueOut | None":
        if value is None:
            return None
        return cls(
            value=value.value,
            err_plus=None if value.err1 is None else abs(value.err1),
            err_minus=None if value.err2 is None else abs(value.err2),
            bound=limit_of(value),
            unit=parameter_unit(parameter),
        )


class ParameterChangeOut(BaseModel):
    """Cambio de un parámetro entre la referencia anterior y la nueva. La
    procedencia de la masa (Mass, Msini...) solo se informa en `mass`."""

    model_config = ConfigDict(frozen=True)

    parameter: MeasuredParameter
    old: ArchiveValueOut | None
    new: ArchiveValueOut | None
    old_mass_provenance: str | None
    new_mass_provenance: str | None

    @classmethod
    def from_domain(cls, change: ParameterChange) -> "ParameterChangeOut":
        return cls(
            parameter=change.parameter,
            old=ArchiveValueOut.from_domain(change.old, change.parameter),
            new=ArchiveValueOut.from_domain(change.new, change.parameter),
            old_mass_provenance=change.old_mass_provenance,
            new_mass_provenance=change.new_mass_provenance,
        )


class DigestEntryOut(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: TransitionKind
    pl_name: str
    planet_url: str
    detected_at: datetime
    old: DigestReferenceOut | None
    new: DigestReferenceOut | None
    parameter_changes: list[ParameterChangeOut]

    @classmethod
    def from_domain(cls, entry: DigestEntry, *, planet_url: str) -> "DigestEntryOut":
        return cls(
            kind=entry.kind,
            pl_name=entry.pl_name,
            planet_url=planet_url,
            detected_at=entry.snapshot_taken_at,
            old=None if entry.old is None else DigestReferenceOut.from_domain(entry.old),
            new=None if entry.new is None else DigestReferenceOut.from_domain(entry.new),
            parameter_changes=[ParameterChangeOut.from_domain(c) for c in entry.parameter_changes],
        )


class WeeklyDigestOut(BaseModel):
    """`GET /archive/weeks/{week}`: cambios de referencia de una semana ISO."""

    model_config = ConfigDict(frozen=True)

    week: str
    snapshots: int
    entries: list[DigestEntryOut]

    @classmethod
    def from_domain(
        cls, digest: WeeklyDigest, *, planet_url: Callable[[str], str]
    ) -> "WeeklyDigestOut":
        return cls(
            week=digest.week,
            snapshots=digest.snapshots,
            entries=[
                DigestEntryOut.from_domain(e, planet_url=planet_url(e.pl_name))
                for e in digest.entries
            ],
        )


class DigestWeekOut(BaseModel):
    """Una semana con snapshot y el recuento de transiciones por tipo."""

    model_config = ConfigDict(frozen=True)

    week: str
    snapshots: int
    changed: int
    new_planet: int
    regained: int
    lost: int


class DigestWeeksResponse(BaseModel):
    """`GET /archive/weeks`: semanas con snapshot, más recientes primero."""

    model_config = ConfigDict(frozen=True)

    weeks: list[DigestWeekOut]


class HealthResponse(BaseModel):
    """Estado del proceso y de la base de datos (`GET /health`)."""

    model_config = ConfigDict(frozen=True)

    status: str
    database: str
