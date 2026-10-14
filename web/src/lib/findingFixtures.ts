/** Fixtures de `solution_tension` para tests (no se importa desde la app). */

import type { FindingDetail, SolutionTension } from "./api/types";

const FICHA = "https://exoplanetarchive.ipac.caltech.edu/overview/";

export const HD_202206_C_MASS: SolutionTension = {
  planet_name: "HD 202206 c",
  parameter: "mass",
  archive_url: `${FICHA}HD%20202206%20c`,
  threshold_sigma: 3,
  sigma: 8.2,
  reference_role: "most_recent",
  labels: [],
  new: {
    reference: "Cao et al. 2026",
    value: 982.09,
    err_plus: 44.5,
    err_minus: 44.5,
    unit: "M_earth",
    is_default: true,
    arxiv_id: null,
  },
  reference: {
    reference: "Benedict & Harrison 2017",
    value: 5689,
    err_plus: 922,
    err_minus: 572,
    unit: "M_earth",
    is_default: false,
    arxiv_id: null,
  },
  others: [],
  new_releasedate: "2026-08-20",
};

export const HD_114082_B_PERIOD: SolutionTension = {
  planet_name: "HD 114082 b",
  parameter: "period",
  archive_url: `${FICHA}HD%20114082%20b`,
  threshold_sigma: 3,
  sigma: 289.5,
  reference_role: "most_recent",
  labels: [],
  new: {
    reference: "del Burgo et al. 2026",
    value: 225.5504,
    err_plus: 0.0004,
    err_minus: 0.0004,
    unit: "day",
    is_default: true,
    arxiv_id: null,
  },
  reference: {
    reference: "Zakhozhay et al. 2022",
    value: 109.75,
    err_plus: 0.4,
    err_minus: 0.37,
    unit: "day",
    is_default: false,
    arxiv_id: null,
  },
  others: [],
  new_releasedate: "2026-09-03",
};

export const SYNTHETIC_WITH_LABELS: SolutionTension = {
  planet_name: "Sintético b",
  parameter: "radius",
  archive_url: `${FICHA}Sint%C3%A9tico%20b`,
  threshold_sigma: 3,
  sigma: 4.5,
  reference_role: "default",
  labels: ["model_dependent", "controversial"],
  new: {
    reference: "Nueva et al. 2026",
    value: 12.5,
    err_plus: 0.5,
    err_minus: 0.5,
    unit: "R_earth",
    is_default: false,
    arxiv_id: "2610.01234",
  },
  reference: {
    reference: "Defecto et al. 2024",
    value: 10,
    err_plus: 0.3,
    err_minus: 0.2,
    unit: "R_earth",
    is_default: true,
    arxiv_id: "2401.05678",
  },
  others: [
    {
      solution: {
        reference: "Otra et al. 2023",
        value: 11,
        err_plus: 0.4,
        err_minus: 0.4,
        unit: "R_earth",
        is_default: false,
        arxiv_id: "javascript:alert(1)",
      },
      sigma: 2.1,
    },
    {
      solution: {
        reference: "Tercera et al. 2021",
        value: 9.5,
        err_plus: 0.6,
        err_minus: 0.5,
        unit: "R_earth",
        is_default: false,
        arxiv_id: null,
      },
      sigma: 3.9,
    },
  ],
  new_releasedate: null,
};

export function detailWith(solution: SolutionTension): FindingDetail {
  return {
    id: "1a2b3c4d-1111-2222-3333-444455556666",
    type: "solution_tension",
    title: "t",
    published_at: "2026-10-02T03:00:00Z",
    level_curious: "c",
    level_amateur: "a",
    level_technical: "t",
    source_url: null,
    catalog_tension: null,
    first_measurement: null,
    independent_confirmation: null,
    solution_tension: solution,
  };
}
