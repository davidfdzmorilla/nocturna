/**
 * Tipos de hallazgo: etiquetas, slugs de `?tipo=` y reglas de presentación.
 * Funciones puras, sin IO. El slug (castellano, visible en la URL) se
 * traduce al valor del enum de la API igual que `?nivel=` en `levels.ts`.
 */

import type { FindingType } from "./api/types";

export const FINDING_TYPES: readonly FindingType[] = [
  "paper_explained",
  "catalog_tension",
  "primera_medida",
  "confirmacion_independiente",
];

export const TYPE_LABELS: Record<FindingType, string> = {
  paper_explained: "Artículo explicado",
  catalog_tension: "Tensión con el catálogo",
  primera_medida: "Primera medida",
  confirmacion_independiente: "Confirmación independiente",
};

export const TYPE_SLUGS: Record<FindingType, string> = {
  paper_explained: "articulo",
  catalog_tension: "tension",
  primera_medida: "primera-medida",
  confirmacion_independiente: "confirmacion",
};

export const ALL_LABEL = "Todos";

export const CANDIDATE_LABEL = "Candidato";

export const CANDIDATE_LEGEND =
  "Candidato: discrepancia calculada automáticamente frente al NASA Exoplanet Archive; ninguna persona la ha verificado.";

/**
 * Interpreta `?tipo=`. Ausente o desconocido (incluidas mayúsculas) da
 * `null` (todos): un tipo inválido no es un 404, igual que `page` y `nivel`.
 */
export function parseTypeParam(
  raw: string | string[] | undefined,
): FindingType | null {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (value === undefined) {
    return null;
  }
  return FINDING_TYPES.find((type) => TYPE_SLUGS[type] === value) ?? null;
}

/** "Candidato" solo para `catalog_tension` (ADR 0012 §6). */
export function isCandidate(type: FindingType): boolean {
  return type === "catalog_tension";
}

/** `"/"`, `"/?page=n"`, `"/?tipo=slug"` o `"/?tipo=slug&page=n"`. */
export function feedHref(page: number, type: FindingType | null): string {
  const query: string[] = [];
  if (type !== null) {
    query.push(`tipo=${TYPE_SLUGS[type]}`);
  }
  if (page > 1) {
    query.push(`page=${page}`);
  }
  return query.length === 0 ? "/" : `/?${query.join("&")}`;
}
