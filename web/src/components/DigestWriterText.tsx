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
    <section
      aria-labelledby="resumen-ia"
      className="mt-6 border-l-2 border-ai pl-4 sm:pl-5"
    >
      <p>
        <span className="inline-block rounded border border-ai px-2 py-0.5 text-xs font-medium text-ai">
          {AI_TEXT_LABEL}
        </span>
      </p>
      <h2
        id="resumen-ia"
        className="mt-2 font-display text-xl break-words text-text sm:text-2xl"
      >
        {text.title}
      </h2>
      <div className="prose-block mt-2 flex flex-col gap-3 text-text">
        {summaryParagraphs(text.summary).map((p, i) => (
          <p key={i} className="whitespace-pre-line">
            {p}
          </p>
        ))}
      </div>
      <p className="prose-block mt-3 text-sm text-text-muted">{legend}</p>
    </section>
  );
}
