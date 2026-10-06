/**
 * Tipos del contrato de la API de lectura de Nocturna.
 *
 * Escritos a mano a partir de `backend/src/nocturna/api/schemas.py`
 * (fuente de verdad). Sin generación desde OpenAPI: para tres endpoints
 * de solo lectura sería sobrearquitectura.
 *
 * Deliberadamente fuera de estos tipos, igual que en el backend:
 * `confidence` (nota editorial interna), `run_id` y `item_id` (telemetría
 * del pipeline) y los metadatos internos del archivo. El identificador público de un hallazgo es `id`.
 */

export type FindingType =
  | "paper_explained"
  | "catalog_tension"
  | "primera_medida"
  | "confirmacion_independiente";

export type Unit = "M_jup" | "M_earth" | "R_jup" | "R_earth" | "day";

export type Parameter = "mass" | "radius" | "period";

export type FindingSummary = {
  id: string;
  type: FindingType;
  title: string;
  published_at: string;
  level_curious: string;
};

export type MeasurementValue = {
  value: number;
  err_plus: number;
  err_minus: number;
  unit: Unit;
};

export type PaperMeasurementWithEvidence = MeasurementValue & {
  planet_name: string;
  evidence: string;
};

export type PriorSolution = MeasurementValue & {
  reference: string;
  is_default: boolean;
  arxiv_id: string | null;
};

export type TensionComparison = {
  paper: PaperMeasurementWithEvidence;
  prior: PriorSolution;
  sigma: number;
};

export type CatalogTension = {
  planet_name: string;
  parameter: Parameter;
  archive_url: string;
  threshold_sigma: number;
  reference_sigma: number;
  comparisons: TensionComparison[];
};

export type FirstMeasurement = {
  paper_planet_name: string;
  archive_planet_name: string | null;
  parameter: Parameter;
  archive_status: "absent" | "no_comparable_solution";
  archive_url: string | null;
  measurements: MeasurementValue[];
};

export type ConfirmationReference = MeasurementValue & {
  refname: string;
  arxiv_id: string | null;
  releasedate: string;
};

export type IndependentConfirmation = {
  paper_planet_name: string;
  archive_planet_name: string;
  parameter: Parameter;
  archive_url: string;
  measurements: MeasurementValue[];
  reference: ConfirmationReference;
  sigmas: number[];
  max_sigma: number;
};

export type FindingDetail = FindingSummary & {
  level_amateur: string;
  level_technical: string;
  source_url: string | null;
  catalog_tension: CatalogTension | null;
  first_measurement: FirstMeasurement | null;
  independent_confirmation: IndependentConfirmation | null;
};

export type FindingsPage = {
  items: FindingSummary[];
  page: number;
  size: number;
  total: number;
};
