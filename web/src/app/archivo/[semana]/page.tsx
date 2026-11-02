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
import { Notice } from "@/components/Notice";

export const dynamic = "force-dynamic";

type Props = { params: Promise<{ semana: string }> };

const INTRO =
  "La referencia de un planeta es la solución por defecto del archivo: la que el NASA Exoplanet Archive marca como principal para ese planeta.";

/** Sin fetch: el título sale solo del parámetro de la URL. */
export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { semana } = await params;
  const week = parseWeekParam(semana);
  return { title: week === null ? "Resumen del archivo" : weekLabel(week).split(" (")[0] };
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
        <Link href="/archivo">Resumen del archivo</Link>
      </p>
      <h1 className="mt-2 text-2xl font-semibold text-text">
        {weekLabel(state.week)}
      </h1>
      {state.data.writer_text !== null && (
        <DigestWriterText text={state.data.writer_text} />
      )}
      <p className="mt-2 text-sm text-text-muted">
        {digestNote(state.data.writer_text)}
      </p>
      <p className="mt-2 text-sm text-text">{INTRO}</p>
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
