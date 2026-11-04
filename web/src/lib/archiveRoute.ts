/**
 * Estado de las rutas `/archivo` y `/archivo/[semana]` como valor puro, para
 * poder probarlo sin renderizar páginas async con `notFound()`.
 */

import type { ApiResult } from "./api/client";
import type {
  DigestText,
  DigestWeek,
  DigestWeeksResponse,
  WeeklyDigest,
} from "./api/types";
import { parseWeekParam, weekTotal } from "./archiveDigest";

/** Primera semana del listado (más reciente primero) con cambios y semana válida. */
export function latestWeekWithChanges(
  weeks: readonly DigestWeek[],
): DigestWeek | null {
  return (
    weeks.find((w) => weekTotal(w) > 0 && parseWeekParam(w.week) !== null) ??
    null
  );
}

export type HomeDigestState =
  | { kind: "unavailable" }
  | { kind: "none" }
  | {
      kind: "week";
      week: string;
      summary: DigestWeek;
      text: DigestText | null;
    };

/** `detail` es `null` si no había semana con cambios y no se pidió el detalle. */
export function homeDigestState(
  weeks: ApiResult<DigestWeeksResponse>,
  detail: ApiResult<WeeklyDigest> | null,
): HomeDigestState {
  if (!weeks.ok) {
    return { kind: "unavailable" };
  }
  const summary = latestWeekWithChanges(weeks.data.weeks);
  if (summary === null) {
    return { kind: "none" };
  }
  return {
    kind: "week",
    week: summary.week,
    summary,
    text: detail !== null && detail.ok ? detail.data.writer_text : null,
  };
}

export type ArchiveIndexState =
  | { kind: "unavailable" }
  | { kind: "empty" }
  | { kind: "weeks"; data: DigestWeeksResponse };

export function archiveIndexState(
  result: ApiResult<DigestWeeksResponse>,
): ArchiveIndexState {
  if (!result.ok) {
    return { kind: "unavailable" };
  }
  return result.data.weeks.length === 0
    ? { kind: "empty" }
    : { kind: "weeks", data: result.data };
}

export type ArchiveWeekState =
  | { kind: "not_found" }
  | { kind: "unavailable" }
  | { kind: "digest"; week: string; data: WeeklyDigest };

/** `null` en `result` = la semana de la URL es inválida y no se llamó a la API. */
export function archiveWeekState(
  rawWeek: string | undefined,
  result: ApiResult<WeeklyDigest> | null,
): ArchiveWeekState {
  const week = parseWeekParam(rawWeek);
  if (week === null || result === null) {
    return { kind: "not_found" };
  }
  if (!result.ok) {
    return { kind: result.reason };
  }
  return { kind: "digest", week, data: result.data };
}
