/**
 * Estado de las rutas `/archivo` y `/archivo/[semana]` como valor puro, para
 * poder probarlo sin renderizar páginas async con `notFound()`.
 */

import type { ApiResult } from "./api/client";
import type {
  DigestWeeksResponse,
  WeeklyDigest,
} from "./api/types";
import { parseWeekParam } from "./archiveDigest";

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
