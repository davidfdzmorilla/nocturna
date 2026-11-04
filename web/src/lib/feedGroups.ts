/**
 * Agrupación del feed por noche (T102). El día es el de Europe/Madrid
 * (el mismo con el que `formatDay` muestra las fechas), así que un
 * hallazgo publicado a las 23:30Z cae en el día siguiente. Conserva el
 * orden de llegada de la API; solo agrupa elementos consecutivos del
 * mismo día, sin reordenar.
 */

import type { FindingSummary } from "./api/types";
import { formatDay } from "./dates";

const ISO_DAY_FORMATTER = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Europe/Madrid",
  year: "numeric",
  month: "2-digit",
  day: "2-digit",
});

export type FindingGroup = {
  /** Día legible en español (`4 de noviembre de 2026`). */
  key: string;
  /** Día `YYYY-MM-DD` (Europe/Madrid); alimenta `<time dateTime>`. */
  isoDay: string;
  items: FindingSummary[];
};

function isoDay(iso: string): string {
  const date = new Date(iso);
  return Number.isNaN(date.getTime()) ? iso : ISO_DAY_FORMATTER.format(date);
}

export function groupByNight(items: readonly FindingSummary[]): FindingGroup[] {
  const groups: FindingGroup[] = [];
  for (const item of items) {
    const key = formatDay(item.published_at);
    const last = groups[groups.length - 1];
    if (last !== undefined && last.key === key) {
      last.items.push(item);
    } else {
      groups.push({ key, isoDay: isoDay(item.published_at), items: [item] });
    }
  }
  return groups;
}
