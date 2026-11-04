import type { ReactNode } from "react";
import type { FindingType } from "@/lib/api/types";
import { CANDIDATE_LABEL, TYPE_LABELS, isCandidate } from "@/lib/findingTypes";

type TypeBadgeProps = {
  type: FindingType;
  /** Metadato que acompaña al tipo en la misma línea (p. ej. la fecha). */
  children?: ReactNode;
};

/**
 * Antetítulo de tipo. El significado va en el texto, no en el color; el
 * contraste usa los tokens de la paleta (>= 4.5:1). `catalog_tension` y
 * `solution_tension` añaden la etiqueta "Candidato" (ADR 0012 §6), que
 * conserva el chip de aviso.
 */
export function TypeBadge({ type, children }: TypeBadgeProps) {
  return (
    <p className="flex flex-wrap items-center gap-x-3 gap-y-1">
      <span className="text-xs uppercase tracking-wider text-text-muted">
        {TYPE_LABELS[type]}
      </span>
      {isCandidate(type) ? (
        <span className="inline-block rounded border border-notice-border bg-notice-bg px-2 py-0.5 text-xs font-medium text-notice-text">
          {CANDIDATE_LABEL}
        </span>
      ) : null}
      {children}
    </p>
  );
}
