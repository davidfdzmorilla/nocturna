import Link from "next/link";
import type { HomeDigestState } from "@/lib/archiveRoute";
import {
  AI_FREE_NOTE,
  HOME_AI_TEXT_LEGEND,
  HOME_AI_TEXT_NOTE,
  weekCountsLine,
  weekLabel,
} from "@/lib/archiveDigest";
import { DigestWriterText } from "./DigestWriterText";
import { Notice } from "./Notice";

const TITLE = "Resumen semanal del NASA Exoplanet Archive";

/** Resumen semanal en portada (T101). Sin fetch: recibe el estado ya resuelto. */
export function HomeDigest({ state }: { state: HomeDigestState }) {
  if (state.kind === "unavailable") {
    return (
      <section aria-label={TITLE}>
        <h1 className="sr-only">{TITLE}</h1>
        <Notice
          title="No se ha podido cargar el resumen del archivo ahora mismo."
          tone="error"
        >
          Vuelve a intentarlo en unos minutos.
        </Notice>
      </section>
    );
  }

  if (state.kind === "none") {
    return (
      <section aria-label={TITLE}>
        <h1 className="sr-only">{TITLE}</h1>
        <Notice title="Todavía no hay semanas con cambios">
          <Link href="/archivo">Todas las semanas</Link>
        </Notice>
      </section>
    );
  }

  return (
    <section aria-label={TITLE}>
      <h1 className="text-2xl font-semibold text-text">{TITLE}</h1>
      <p className="mt-2 text-text">{weekLabel(state.week)}</p>
      {state.text !== null && (
        <DigestWriterText text={state.text} legend={HOME_AI_TEXT_LEGEND} />
      )}
      <p className="mt-3 text-sm text-text-muted">
        {weekCountsLine(state.summary)}
      </p>
      <p className="mt-2 text-sm text-text-muted">
        {state.text === null ? AI_FREE_NOTE : HOME_AI_TEXT_NOTE}
      </p>
      <p className="mt-3 flex flex-wrap gap-x-6 gap-y-1 text-sm">
        <Link href={`/archivo/${state.week}`}>Ver la semana completa</Link>
        <Link href="/archivo">Todas las semanas</Link>
      </p>
    </section>
  );
}
