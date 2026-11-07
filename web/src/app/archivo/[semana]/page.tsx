import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { fetchArchiveWeek } from "@/lib/api/client";
import {
  digestNote,
  SECTION_ORDER,
  entriesByKind,
  parseWeekParam,
  weekLabel,
} from "@/lib/archiveDigest";
import { archiveWeekState } from "@/lib/archiveRoute";
import { DigestWriterText } from "@/components/DigestWriterText";
import { DigestSection } from "@/components/DigestSection";
import { NOINDEX } from "@/lib/noindex";
import { Notice } from "@/components/Notice";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ semana: string }> };

const INTRO =
  "La referencia de un planeta es la solución por defecto del archivo: la que el NASA Exoplanet Archive marca como principal para ese planeta.";

/** El título sale del parámetro; la petición (compartida vía `cache()`) solo decide `noindex`. */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { semana } = await params;
  const week = parseWeekParam(semana);
  const result = week === null ? null : await fetchArchiveWeek(week);
  const down = result !== null && !result.ok && result.reason === "unavailable";
  return {
    title:
      week === null ? "Resumen del archivo" : weekLabel(week).split(" (")[0],
    ...(down ? NOINDEX : {}),
  };
}

export default async function ArchiveWeekPage({ params }: Props) {
  const { semana } = await params;
  const week = parseWeekParam(semana);
  const result = week === null ? null : await fetchArchiveWeek(week);
  const state = archiveWeekState(semana, result);

  if (state.kind === "not_found") {
    notFound();
  }

  if (state.kind === "unavailable") {
    return (
      <>
        <h1 className="sr-only">Resumen del archivo</h1>
        <Notice
          title="No se ha podido cargar el resumen del archivo ahora mismo."
          tone="error"
        >
          Vuelve a intentarlo en unos minutos.
        </Notice>
      </>
    );
  }

  return (
    <>
      <p className="text-sm">
        <Link href="/archivo" className="link-quiet text-text-muted">
          ← Resumen del archivo
        </Link>
      </p>
      <h1 className="mt-3 font-display text-3xl leading-tight text-balance break-words text-text sm:text-4xl">
        {weekLabel(state.week)}
      </h1>
      {state.data.writer_text !== null && (
        <div className="mt-6 rounded-lg border border-border bg-surface-raised p-5 sm:p-6 [&>section]:mt-0">
          <DigestWriterText text={state.data.writer_text} />
        </div>
      )}
      <p className="prose-block mt-4 text-sm text-text-muted">
        {digestNote(state.data.writer_text)}
      </p>
      <p className="prose-block mt-2 text-text">{INTRO}</p>
      {SECTION_ORDER.map((kind) => (
        <DigestSection
          key={kind}
          kind={kind}
          entries={entriesByKind(state.data.entries, kind)}
        />
      ))}
    </>
  );
}
