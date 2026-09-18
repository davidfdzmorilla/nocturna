/**
 * Único módulo del proyecto que hace `fetch` a la API de lectura.
 * Ninguna otra parte de la web habla con la API directamente (ver
 * `CLAUDE.md`: la web es de solo lectura y nunca llama a Claude ni
 * contiene lógica de análisis; esto incluye no dispersar el acceso a la
 * API por componentes).
 *
 * `cache: "no-store"` en cada petición: el plan fijó SSR dinámico, no
 * ISR, para esta fase.
 *
 * Política de errores, espejo de la de la API (T50: la API nunca
 * devuelve el detalle real a quien la llama, solo `{"detail": "internal
 * error"}`, y manda la traza a stderr):
 *  - `200` -> `ok`.
 *  - `404` -> `not_found`.
 *  - Cualquier otra cosa (5xx, red caída, timeout, JSON ilegible,
 *    `NOCTURNA_API_URL` sin definir) -> `unavailable`.
 * Esta función nunca lanza hacia quien la llama salvo bug propio, y
 * nunca le devuelve el cuerpo de la respuesta, el código de estado ni la
 * URL consultada: ese detalle va solo a `console.error` del servidor.
 */

import { cache } from "react";
import { apiBaseUrl } from "./config";
import type { FindingDetail, FindingsPage } from "./types";
import { PAGE_SIZE } from "../pagination";

export type ApiResult<T> =
  { ok: true; data: T } | { ok: false; reason: "not_found" | "unavailable" };

const TIMEOUT_MS = 5000;

async function getJson<T>(path: string): Promise<ApiResult<T>> {
  let baseUrl: string;
  try {
    baseUrl = apiBaseUrl();
  } catch (error) {
    console.error(
      `[nocturna:api] configuración inválida al pedir ${path}`,
      error,
    );
    return { ok: false, reason: "unavailable" };
  }

  let response: Response;
  try {
    response = await fetch(`${baseUrl}${path}`, {
      cache: "no-store",
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
  } catch (error) {
    console.error(`[nocturna:api] fetch a ${path} falló`, error);
    return { ok: false, reason: "unavailable" };
  }

  if (response.status === 404) {
    return { ok: false, reason: "not_found" };
  }

  if (!response.ok) {
    console.error(
      `[nocturna:api] respuesta ${response.status} al pedir ${path}`,
    );
    return { ok: false, reason: "unavailable" };
  }

  try {
    const data = (await response.json()) as T;
    return { ok: true, data };
  } catch (error) {
    console.error(`[nocturna:api] JSON ilegible al pedir ${path}`, error);
    return { ok: false, reason: "unavailable" };
  }
}

export async function fetchFindingsPage(
  page: number,
  size: number = PAGE_SIZE,
): Promise<ApiResult<FindingsPage>> {
  const params = new URLSearchParams({
    page: String(page),
    size: String(size),
  });
  return getJson<FindingsPage>(`/findings?${params.toString()}`);
}

/**
 * `cache()` de React deduplica por argumentos dentro del mismo render:
 * `generateMetadata` y el componente de página piden el mismo `id` en la
 * misma petición HTTP, y con esto comparten una única llamada a la API
 * (y el mismo `AbortSignal`, que es justo lo que se quiere: es la misma
 * petición). Sin `cache()`, cada llamada crea su propio
 * `AbortSignal.timeout()` y la Request Memoization de Next no dedupea
 * porque compara el objeto de opciones del `fetch`, no la URL.
 */
export const fetchFinding = cache(async function fetchFinding(
  id: string,
): Promise<ApiResult<FindingDetail>> {
  return getJson<FindingDetail>(`/findings/${encodeURIComponent(id)}`);
});
