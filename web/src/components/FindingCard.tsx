import Link from "next/link";
import type { FindingSummary } from "@/lib/api/types";
import { formatPublishedAt } from "@/lib/dates";

type FindingCardProps = {
  finding: FindingSummary;
};

/**
 * Tarjeta del feed. Solo usa lo que trae `FindingSummary`: sin enlace a
 * arXiv ni a otros niveles (esos campos no están en el listado, y
 * pedirlos aparte por tarjeta reintroduciría el N+1 que T50 evitó a
 * propósito).
 *
 * El extracto de `level_curious` se recorta visualmente con
 * `line-clamp-4` (CSS): el texto completo sigue en el DOM, no se corta
 * la cadena en JS ni a mitad de palabra.
 */
export function FindingCard({ finding }: FindingCardProps) {
  const { iso, label } = formatPublishedAt(finding.published_at);

  return (
    <article className="rounded-md border border-border bg-surface p-4">
      <h2 className="text-lg font-semibold">
        <Link href={`/hallazgo/${finding.id}`}>{finding.title}</Link>
      </h2>
      <time dateTime={iso} className="mt-1 block text-sm text-text-muted">
        {label}
      </time>
      <p className="mt-3 line-clamp-4 text-text">{finding.level_curious}</p>
    </article>
  );
}
