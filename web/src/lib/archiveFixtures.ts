/**
 * Fixtures tipados del resumen semanal, solo para tests. Datos reales de las
 * semanas 2026-W40 y 2026-W41 del archivo (TOI-2427 b, HD 148797, Collier).
 */

import type {
  ArchiveValue,
  DigestEntry,
  DigestReference,
  ParameterChange,
  WeeklyDigest,
} from "./api/types";

const OVERVIEW = "https://exoplanetarchive.ipac.caltech.edu/overview/";
export const overview = (name: string) =>
  `${OVERVIEW}${encodeURIComponent(name)}`;

export const value = (
  v: number | null,
  plus: number | null,
  minus: number | null,
  unit: ArchiveValue["unit"],
  bound: ArchiveValue["bound"] = "none",
): ArchiveValue => ({ value: v, err_plus: plus, err_minus: minus, bound, unit });

export const GIACALONE: DigestReference = {
  reference: "Giacalone et al. 2022",
  pl_pubdate: "2022-02",
  releasedate: "2022-02-15",
};
export const SCHMERLING: DigestReference = {
  reference: "Schmerling et al. 2025",
  pl_pubdate: "2025-07",
  releasedate: "2026-10-01",
};
export const ALMENARA: DigestReference = {
  reference: "Almenara et al. 2026",
  pl_pubdate: "2026-03",
  releasedate: "2026-10-01",
};
export const COLLIER: DigestReference = {
  reference: "Collier et al. 2026",
  pl_pubdate: "2026-04",
  releasedate: "2026-10-08",
};

export const toi2427bChanges: ParameterChange[] = [
  {
    parameter: "mass",
    old: value(null, null, null, "M_earth"),
    new: value(5.69, 0.51, 0.5, "M_earth"),
    old_mass_provenance: null,
    new_mass_provenance: "Mass",
  },
  {
    parameter: "radius",
    old: value(1.8, 0.12, 0.12, "R_earth"),
    new: value(1.64, 0.12, 0.11, "R_earth"),
    old_mass_provenance: null,
    new_mass_provenance: null,
  },
  {
    parameter: "period",
    old: value(1.3060011, 0.0000102, 0.0000102, "day"),
    new: value(1.305962, 0.000071, 0.00007, "day"),
    old_mass_provenance: null,
    new_mass_provenance: null,
  },
];

const entry = (
  kind: DigestEntry["kind"],
  pl_name: string,
  oldRef: DigestReference | null,
  newRef: DigestReference | null,
  changes: ParameterChange[] = [],
  detected_at = "2026-10-02T03:00:00Z",
): DigestEntry => ({
  kind,
  pl_name,
  planet_url: overview(pl_name),
  detected_at,
  old: oldRef,
  new: newRef,
  parameter_changes: changes,
});

export const toi2427b = entry(
  "changed",
  "TOI-2427 b",
  GIACALONE,
  SCHMERLING,
  toi2427bChanges,
);

export const week40: WeeklyDigest = {
  week: "2026-W40",
  snapshots: 1,
  entries: [
    toi2427b,
    entry("new_planet", "HD 148797 b", null, ALMENARA),
    entry("new_planet", "HD 148797 c", null, ALMENARA),
    entry("new_planet", "TOI-2427 c", null, SCHMERLING),
  ],
};

export const collierNames = Array.from(
  { length: 70 },
  (_, i) => `Collier-${String(i + 1).padStart(2, "0")} b`,
);

export const week41: WeeklyDigest = {
  week: "2026-W41",
  snapshots: 1,
  entries: collierNames.map((n) =>
    entry("new_planet", n, null, COLLIER, [], "2026-10-09T03:00:00Z"),
  ),
};

export { entry as makeEntry };
