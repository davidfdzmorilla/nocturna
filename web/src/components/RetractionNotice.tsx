import { formatDay } from "@/lib/dates";

export const RETRACTION_PREFIX = "Retirado el";
export const RETRACTION_LEGEND =
  "Este hallazgo ya no aparece en el feed. Su texto original se conserva debajo, sin cambios.";

type RetractionNoticeProps = {
  retractedAt: string;
  note: string | null;
};

/**
 * Aviso de hallazgo retirado (T100, ADR 0040). La nota llega ya redactada
 * por el backend (con su prefijo «Retirado:») y se pinta tal cual, como
 * texto, nunca como HTML.
 */
export function RetractionNotice({ retractedAt, note }: RetractionNoticeProps) {
  return (
    <section
      aria-labelledby="retirada"
      className="mt-4 rounded-md border border-notice-border bg-notice-bg px-4 py-3 text-notice-text"
    >
      <h2 id="retirada" className="font-semibold">
        {RETRACTION_PREFIX}{" "}
        <time dateTime={retractedAt}>{formatDay(retractedAt)}</time>
      </h2>
      {note ? <p className="mt-1 whitespace-pre-line">{note}</p> : null}
      <p className="mt-2 text-sm">{RETRACTION_LEGEND}</p>
    </section>
  );
}
