import { safeArxivSourceUrl } from "@/lib/externalLinks";

type EvidenceQuoteProps = {
  evidence: string;
  /** `source_url` del hallazgo (arXiv). Se valida antes de enlazar. */
  sourceUrl: string | null;
};

/**
 * Cita literal del resumen en arXiv, en inglés y sin traducir. Texto
 * plano: React escapa el contenido (puede traer LaTeX crudo o `<`).
 */
export function EvidenceQuote({ evidence, sourceUrl }: EvidenceQuoteProps) {
  const href = safeArxivSourceUrl(sourceUrl);
  return (
    <figure className="mt-1">
      <blockquote
        lang="en"
        className="border-l-2 border-border pl-3 text-sm text-text"
      >
        {evidence}
      </blockquote>
      <figcaption className="mt-1 text-xs text-text-muted">
        {href ? (
          <a href={href} rel="noopener noreferrer">
            Cita literal del resumen en arXiv
          </a>
        ) : (
          "Cita literal del resumen en arXiv"
        )}
      </figcaption>
    </figure>
  );
}
