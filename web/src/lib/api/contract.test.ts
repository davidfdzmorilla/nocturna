import { describe, expect, it } from "vitest";
import type {
  CatalogTension,
  ConfirmationReference,
  FindingDetail,
  FindingSummary,
  FindingsPage,
  FirstMeasurement,
  IndependentConfirmation,
  MeasurementValue,
  PaperMeasurementWithEvidence,
  PriorSolution,
  TensionComparison,
} from "./types";

/**
 * Contrato con `backend/src/nocturna/api/schemas.py`: mismos conjuntos de
 * claves que `backend/tests/test_api_schemas.py`. Los fixtures están tipados,
 * así que una clave de más o de menos rompe `tsc` (build) y este test.
 */

const keys = (o: object) => Object.keys(o).sort();
const expected = (...k: string[]) => [...k].sort();

const measurement: MeasurementValue = {
  value: 1.5,
  err_plus: 0.2,
  err_minus: 0.1,
  unit: "M_jup",
};
const paper: PaperMeasurementWithEvidence = {
  planet_name: "X b",
  ...measurement,
  evidence: "We find 1.5 Mjup",
};
const prior: PriorSolution = {
  reference: "Doe et al. 2020",
  ...measurement,
  is_default: true,
  arxiv_id: null,
};
const comparison: TensionComparison = { paper, prior, sigma: 3.4 };
const ficha = "https://exoplanetarchive.ipac.caltech.edu/overview/X%20b";

const tension: CatalogTension = {
  planet_name: "X b",
  parameter: "mass",
  archive_url: ficha,
  threshold_sigma: 3,
  reference_sigma: 3.4,
  comparisons: [comparison],
};
const first: FirstMeasurement = {
  paper_planet_name: "X b",
  archive_planet_name: null,
  parameter: "radius",
  archive_status: "absent",
  archive_url: null,
  measurements: [measurement],
};
const reference: ConfirmationReference = {
  refname: "Smith et al. 2019",
  arxiv_id: null,
  ...measurement,
  releasedate: "2019-05-01",
};
const confirmation: IndependentConfirmation = {
  paper_planet_name: "X b",
  archive_planet_name: "X b",
  parameter: "radius",
  archive_url: ficha,
  measurements: [measurement],
  reference,
  sigmas: [0.3],
  max_sigma: 0.3,
};
const summary: FindingSummary = {
  id: "1a2b3c4d-1111-2222-3333-444455556666",
  type: "paper_explained",
  title: "t",
  published_at: "2026-10-02T03:00:00Z",
  level_curious: "c",
};
const detail: FindingDetail = {
  ...summary,
  level_amateur: "a",
  level_technical: "t",
  source_url: null,
  catalog_tension: tension,
  first_measurement: first,
  independent_confirmation: confirmation,
};
const page: FindingsPage = { items: [summary], page: 1, size: 20, total: 1 };

describe("contrato de la API (claves exactas)", () => {
  it("FindingSummary y página", () => {
    expect(keys(summary)).toEqual(
      expected("id", "title", "published_at", "type", "level_curious"),
    );
    expect(keys(page)).toEqual(expected("items", "page", "size", "total"));
  });

  it("FindingDetail", () => {
    expect(keys(detail)).toEqual(
      expected(
        "id", "title", "published_at", "type", "level_curious",
        "level_amateur", "level_technical", "source_url",
        "catalog_tension", "first_measurement", "independent_confirmation",
      ),
    );
  });

  it("CatalogTension y anidados", () => {
    expect(keys(tension)).toEqual(
      expected(
        "planet_name", "parameter", "archive_url", "threshold_sigma",
        "reference_sigma", "comparisons",
      ),
    );
    expect(keys(comparison)).toEqual(expected("paper", "prior", "sigma"));
    expect(keys(paper)).toEqual(
      expected("planet_name", "value", "err_plus", "err_minus", "unit", "evidence"),
    );
    expect(keys(prior)).toEqual(
      expected("reference", "value", "err_plus", "err_minus", "unit", "is_default", "arxiv_id"),
    );
  });

  it("FirstMeasurement: las medidas no llevan planet_name", () => {
    expect(keys(first)).toEqual(
      expected(
        "paper_planet_name", "archive_planet_name", "parameter",
        "archive_status", "archive_url", "measurements",
      ),
    );
    expect(keys(first.measurements[0])).toEqual(
      expected("value", "err_plus", "err_minus", "unit"),
    );
  });

  it("IndependentConfirmation y anidados", () => {
    expect(keys(confirmation)).toEqual(
      expected(
        "paper_planet_name", "archive_planet_name", "parameter", "archive_url",
        "measurements", "reference", "sigmas", "max_sigma",
      ),
    );
    expect(keys(confirmation.measurements[0])).toEqual(
      expected("value", "err_plus", "err_minus", "unit"),
    );
    expect(keys(confirmation.reference)).toEqual(
      expected("refname", "arxiv_id", "value", "err_plus", "err_minus", "unit", "releasedate"),
    );
  });
});
