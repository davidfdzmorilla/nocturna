import type { Metadata } from "next";
import Link from "next/link";
import { fetchArchiveWeeks } from "@/lib/api/client";
import { archiveIndexState } from "@/lib/archiveRoute";
import {
  AI_LISTING_NOTE,
  parseWeekParam,
  weekCounts,
  weekLabel,
  weekTotal,
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
      <h1 className="font-display text-3xl leading-tight text-balance break-words text-text sm:text-4xl">
        Resumen semanal del NASA Exoplanet Archive
      </h1>
      <p className="prose-block mt-3 text-sm text-text-muted">
        {AI_LISTING_NOTE}
      </p>
      {state.kind === "empty" ? (
        <div className="mt-6">
          <Notice title="Todavía no hay resúmenes semanales.">
            El archivo se consulta los viernes.
          </Notice>
        </div>
      ) : (
        <ul className="mt-6 divide-y divide-border border-y border-border">
          {state.data.weeks.map((w) => {
            const validWeek = parseWeekParam(w.week);
            const empty = weekTotal(w) === 0;
            return (
              <li
                key={w.week}
                className="flex flex-col gap-2 py-4 sm:flex-row sm:items-baseline sm:justify-between sm:gap-6"
              >
                <h2 className="font-display text-lg break-words sm:text-xl">
                  {validWeek === null ? (
                    w.week
                  ) : (
                    <Link
                      href={`/archivo/${validWeek}`}
                      className={`link-quiet ${empty ? "text-text-muted" : ""}`}
                    >
                      {weekLabel(validWeek)}
                    </Link>
                  )}
                </h2>
                {empty ? (
                  <p className="text-sm text-text-muted">Sin cambios</p>
                ) : (
                  <dl className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
                    {weekCounts(w).map(({ label, value }) => (
                      <div key={label} className="flex items-baseline gap-1.5">
                        <dt className="text-text-muted">{label}</dt>
                        <dd className="order-first font-semibold tabular-nums text-text">
                          {value}
                        </dd>
                      </div>
                    ))}
                  </dl>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </>
  );
}
