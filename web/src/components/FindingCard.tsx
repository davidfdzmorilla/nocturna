import Link from "next/link";
import type { FindingSummary } from "@/lib/api/types";
import { TypeBadge } from "./TypeBadge";

type FindingCardProps = {
  finding: FindingSummary;
};

/**
 * Tarjeta del feed. Solo usa lo que trae `FindingSummary`: sin enlace a
 * arXiv ni a otros niveles (esos campos no están en el listado, y
 * pedirlos aparte por tarjeta reintroduciría el N+1 que T50 evitó a
 * propósito).
 *
 * Una sola `<a>` por tarjeta: el enlace del título se extiende a toda la
 * tarjeta con un `::after` absoluto (stretched link). La fecha no va en la
 * tarjeta: el feed agrupa por noche (`feedGroups`).
 *
 * El extracto de `level_curious` se recorta visualmente con
 * `line-clamp-3` (CSS): el texto completo sigue en el DOM, no se corta
 * la cadena en JS ni a mitad de palabra.
 */
export function FindingCard({ finding }: FindingCardProps) {
  return (
    <article className="relative min-w-0 rounded-lg border border-border bg-surface p-5 hover:border-border-strong has-[a:focus-visible]:border-accent">
      <TypeBadge type={finding.type} />
      <h3 className="mt-2 text-lg font-semibold leading-snug break-words">
        <Link
          href={`/hallazgo/${finding.id}`}
          className="link-quiet after:absolute after:inset-0"
        >
          {finding.title}
        </Link>
      </h3>
      <p className="mt-3 line-clamp-3 break-words text-text-muted">
        {finding.level_curious}
      </p>
    </article>
  );
}
