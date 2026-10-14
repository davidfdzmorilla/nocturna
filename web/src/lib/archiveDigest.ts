/**
 * Lógica pura del resumen semanal del archivo (T84.w): validación de la
 * semana de la URL, etiquetas y agrupaciones. Sin IO.
 */

import type { DigestEntry, TransitionKind } from "./api/types";

export const AI_FREE_NOTE =
  "Datos del NASA Exoplanet Archive presentados con plantillas fijas; esta sección no contiene texto generado por IA.";

const WEEK_RE = /^\d{4}-W(0[1-9]|[1-4]\d|5[0-3])$/;

/** Devuelve la semana si tiene formato `YYYY-Www`; `null` si no. */
export function parseWeekParam(raw: string | undefined): string | null {
  return raw !== undefined && WEEK_RE.test(raw) ? raw : null;
}

export const SECTION_ORDER: readonly TransitionKind[] = [
  "changed",
  "new_planet",
  "regained",
  "lost",
];

export const SECTION_LABELS: Record<TransitionKind, string> = {
  changed: "Referencia cambiada",
  new_planet: "Planetas nuevos",
  regained: "Planetas recuperados",
  lost: "Planetas que pierden la referencia",
};

/** Entradas de un tipo, en el orden en que llegan de la API. */
export function entriesByKind(
  entries: readonly DigestEntry[],
  kind: TransitionKind,
): DigestEntry[] {
  return entries.filter((e) => e.kind === kind);
}

const MONTHS = [
  "enero",
  "febrero",
  "marzo",
  "abril",
  "mayo",
  "junio",
  "julio",
  "agosto",
  "septiembre",
  "octubre",
  "noviembre",
  "diciembre",
];

const DAY_MS = 86_400_000;

function parts(week: string): { year: number; number: number } {
  return { year: Number(week.slice(0, 4)), number: Number(week.slice(6)) };
}

/** Lunes y domingo (UTC) de una semana ISO. */
function weekBounds(week: string): { monday: Date; sunday: Date } {
  const { year, number } = parts(week);
  // El 4 de enero siempre cae en la semana 1.
  const jan4 = new Date(Date.UTC(year, 0, 4));
  const jan4Weekday = (jan4.getUTCDay() + 6) % 7; // lunes = 0
  const monday = new Date(
    jan4.getTime() - jan4Weekday * DAY_MS + (number - 1) * 7 * DAY_MS,
  );
  return { monday, sunday: new Date(monday.getTime() + 6 * DAY_MS) };
}

function isoDay(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** `2026-W41` -> `{ start: "2026-10-05", end: "2026-10-11" }`. */
export function isoWeekRange(week: string): { start: string; end: string } {
  const { monday, sunday } = weekBounds(week);
  return { start: isoDay(monday), end: isoDay(sunday) };
}

/** `2026-W41` -> `"Semana 41 de 2026 (5–11 de octubre)"`. */
export function weekLabel(week: string): string {
  const { year, number } = parts(week);
  const { monday, sunday } = weekBounds(week);
  const d1 = monday.getUTCDate();
  const d2 = sunday.getUTCDate();
  const m1 = MONTHS[monday.getUTCMonth()];
  const m2 = MONTHS[sunday.getUTCMonth()];
  const span =
    monday.getUTCMonth() === sunday.getUTCMonth()
      ? `${d1}–${d2} de ${m2}`
      : `${d1} de ${m1} – ${d2} de ${m2}`;
  return `Semana ${number} de ${year} (${span})`;
}

export type ReferenceGroup = {
  reference: string;
  entries: DigestEntry[];
};

/**
 * Agrupa por la referencia nueva (`new.reference`), conservando el orden de
 * primera aparición y el orden de la API dentro de cada grupo. Sin
 * referencia nueva, el grupo es `null`-referencia ("(sin referencia)").
 */
export function groupByReference(
  entries: readonly DigestEntry[],
): ReferenceGroup[] {
  const groups = new Map<string, DigestEntry[]>();
  for (const entry of entries) {
    const key = entry.new?.reference ?? "(sin referencia)";
    const bucket = groups.get(key);
    if (bucket === undefined) {
      groups.set(key, [entry]);
    } else {
      bucket.push(entry);
    }
  }
  return [...groups].map(([reference, list]) => ({
    reference,
    entries: list,
  }));
}
