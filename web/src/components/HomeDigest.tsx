import Link from "next/link";
import type { HomeDigestState } from "@/lib/archiveRoute";
import {
  AI_FREE_NOTE,
  HOME_AI_TEXT_LEGEND,
  HOME_AI_TEXT_NOTE,
  weekCounts,
  weekLabel,
} from "@/lib/archiveDigest";
import { DigestWriterText } from "./DigestWriterText";
import { Notice } from "./Notice";

const TITLE = "Resumen semanal del NASA Exoplanet Archive";

const H1_CLASS =
  "font-display text-3xl leading-tight text-balance break-words text-text sm:text-4xl";

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
    <section
      aria-label={TITLE}
      className="rounded-lg border border-border border-l-4 border-l-accent bg-surface-raised p-5 sm:p-8"
    >
      <h1 className={H1_CLASS}>{TITLE}</h1>
      <p className="mt-2 text-text-muted">{weekLabel(state.week)}</p>
      <dl className="mt-6 grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
        {weekCounts(state.summary).map(({ label, value }) => (
          <div key={label}>
            <dt className="text-xs uppercase tracking-wider text-text-muted">
              {label}
            </dt>
            <dd className="mt-1 text-3xl font-semibold tabular-nums text-text">
              {value}
            </dd>
          </div>
        ))}
      </dl>
      {state.text !== null && (
        <DigestWriterText text={state.text} legend={HOME_AI_TEXT_LEGEND} />
      )}
      <p className="mt-6 text-sm text-text-muted">
        {state.text === null ? AI_FREE_NOTE : HOME_AI_TEXT_NOTE}
      </p>
      <p className="mt-4 flex flex-wrap gap-3">
        <Link href={`/archivo/${state.week}`} className="btn-outline">
          Ver la semana completa
        </Link>
        <Link href="/archivo" className="btn-outline">
          Todas las semanas
        </Link>
      </p>
    </section>
  );
}
