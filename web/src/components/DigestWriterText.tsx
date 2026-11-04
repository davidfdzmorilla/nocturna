import type { DigestText } from "@/lib/api/types";
import {
  AI_TEXT_LABEL,
  AI_TEXT_LEGEND,
  summaryParagraphs,
} from "@/lib/archiveDigest";

/** Resumen semanal redactado por IA (T84.r.w). Siempre texto escapado. */
export function DigestWriterText({
  text,
  legend = AI_TEXT_LEGEND,
}: {
  text: DigestText;
  legend?: string;
}) {
  return (
    <section aria-labelledby="resumen-ia" className="mt-6">
      <p className="text-sm text-text-muted">{AI_TEXT_LABEL}</p>
      <h2 id="resumen-ia" className="mt-1 text-xl font-semibold text-text">
        {text.title}
      </h2>
      <div className="mt-2 flex flex-col gap-3 text-text">
        {summaryParagraphs(text.summary).map((p, i) => (
          <p key={i} className="whitespace-pre-line">
            {p}
          </p>
        ))}
      </div>
      <p className="mt-3 text-sm text-text-muted">{legend}</p>
    </section>
  );
}
