/**
 * Textos de presentación de una tensión entre soluciones (T85.w, ADR 0030).
 * Funciones puras: la web no calcula σ ni extremos, solo rotula.
 */

import type { ReferenceRole, SolutionTensionLabel } from "./api/types";
import { formatDay } from "./dates";

export const SOLUTION_TENSION_LABELS: Record<
  SolutionTensionLabel,
  { chip: string; sentence: string }
> = {
  model_dependent: {
    chip: "Depende del modelo",
    sentence:
      "El mismo trabajo publica varias soluciones de este planeta en el archivo; la diferencia puede depender de la solución elegida.",
  },
  controversial: {
    chip: "Planeta controvertido",
    sentence:
      "El NASA Exoplanet Archive marca este planeta como controvertido.",
  },
};

export const SIGMA_NOTE =
  "La σ mide la diferencia en unidades de los errores publicados; con errores muy pequeños puede ser enorme aunque el cambio de valor sea lo relevante.";

export const NO_OTHERS_TEXT =
  "Ninguna otra solución publicada y confirmada para este parámetro.";

const REFERENCE_ROLE_TEXT: Record<ReferenceRole, string> = {
  default: "Referencia: la solución por defecto del archivo.",
  most_recent:
    "Referencia: la solución más reciente de otro trabajo, porque la nueva es la solución por defecto (o la por defecto es del mismo trabajo).",
};

/** Las etiquetas desconocidas (de una API más nueva) se descartan. */
export function knownLabels(labels: readonly string[]): SolutionTensionLabel[] {
  return labels.filter((l): l is SolutionTensionLabel =>
    Object.prototype.hasOwnProperty.call(SOLUTION_TENSION_LABELS, l),
  );
}

export function referenceRoleText(role: ReferenceRole): string {
  return REFERENCE_ROLE_TEXT[role];
}

/** "Entró en el archivo el 20 de agosto de 2026." o `null` sin fecha. */
export function formatReleaseDate(iso: string | null): string | null {
  return iso === null ? null : `Entró en el archivo el ${formatDay(iso)}.`;
}
