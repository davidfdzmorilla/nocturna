import type { Metadata } from "next";
import Link from "next/link";
import { fetchArchiveWeeks } from "@/lib/api/client";
import { archiveIndexState } from "@/lib/archiveRoute";
import {
  AI_LISTING_NOTE,
  parseWeekParam,
  weekCountsLine,
  weekLabel,
} from "@/lib/archiveDigest";
import { Notice } from "@/components/Notice";

export const dynamic = "force-dynamic";

export const metadata: Metadata = { title: "Resumen del archivo" };

export default async function ArchivePage() {
  const state = archiveIndexState(await fetchArchiveWeeks());

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
      <h1 className="text-2xl font-semibold text-text">
        Resumen semanal del NASA Exoplanet Archive
      </h1>
      <p className="mt-2 text-sm text-text-muted">{AI_LISTING_NOTE}</p>
      {state.kind === "empty" ? (
        <div className="mt-6">
          <Notice title="Todavía no hay resúmenes semanales.">
            El archivo se consulta los viernes.
          </Notice>
        </div>
      ) : (
        <ul className="mt-6 flex flex-col gap-3">
          {state.data.weeks.map((w) => {
            const validWeek = parseWeekParam(w.week);
            return (
              <li
                key={w.week}
                className="rounded-md border border-border bg-surface p-4"
              >
                <h2 className="text-lg font-semibold">
                  {validWeek === null ? (
                    w.week
                  ) : (
                    <Link href={`/archivo/${validWeek}`}>
                      {weekLabel(validWeek)}
                    </Link>
                  )}
                </h2>
                <p className="mt-1 text-sm text-text-muted">
                  {weekCountsLine(w)}
                </p>
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
