import type { FindingType } from "@/lib/api/types";
import { CANDIDATE_LABEL, TYPE_LABELS, isCandidate } from "@/lib/findingTypes";

type TypeBadgeProps = {
  type: FindingType;
};

const CHIP =
  "inline-block rounded border border-border bg-surface px-2 py-0.5 text-xs font-medium text-text";

/**
 * Etiqueta de tipo. El significado va en el texto, no en el color; el
 * contraste usa los tokens de la paleta (>= 4.5:1). `catalog_tension`
 * añade una segunda etiqueta "Candidato" (ADR 0012 §6).
 */
export function TypeBadge({ type }: TypeBadgeProps) {
  return (
    <p className="mt-2 flex flex-wrap gap-2">
      <span className={CHIP}>{TYPE_LABELS[type]}</span>
      {isCandidate(type) ? (
        <span className="inline-block rounded border border-notice-border bg-notice-bg px-2 py-0.5 text-xs font-medium text-notice-text">
          {CANDIDATE_LABEL}
        </span>
      ) : null}
    </p>
  );
}
