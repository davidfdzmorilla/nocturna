/**
 * Validación de los `href` hacia fuera. Un enlace construido con datos de
 * terceros solo sale de aquí: prefijo exacto o `null` (no se pinta enlace).
 * Mismo patrón que `ARXIV_ABS_PREFIX` del detalle.
 */

export const ARCHIVE_OVERVIEW_PREFIX =
  "https://exoplanetarchive.ipac.caltech.edu/overview/";
export const ARXIV_ABS_PREFIX = "https://arxiv.org/abs/";

// Ficha del planeta: sin espacios, comillas, `<`, `>`, `\`, `#` ni `?`.
const ARCHIVE_TAIL_RE = /^[^\s"'<>\\#?]+$/;
// Formato nuevo (2410.01234, con versión opcional) o antiguo (astro-ph/0601234).
const ARXIV_ID_RE =
  /^(?:\d{4}\.\d{4,5}|[a-z-]+(?:\.[A-Z]{2})?\/\d{7})(?:v\d+)?$/;

/** Devuelve la URL si es una ficha del NASA Exoplanet Archive; si no, `null`. */
export function safeArchiveUrl(url: string | null): string | null {
  if (url === null || !url.startsWith(ARCHIVE_OVERVIEW_PREFIX)) {
    return null;
  }
  const tail = url.slice(ARCHIVE_OVERVIEW_PREFIX.length);
  return ARCHIVE_TAIL_RE.test(tail) ? url : null;
}

/** Enlace a la página de resumen de arXiv; `null` si el id no tiene formato válido. */
export function arxivAbsUrl(id: string | null): string | null {
  if (id === null || !ARXIV_ID_RE.test(id)) {
    return null;
  }
  return `${ARXIV_ABS_PREFIX}${id}`;
}

/** `source_url` del hallazgo: solo con el prefijo de arXiv. */
export function safeArxivSourceUrl(url: string | null): string | null {
  if (url === null || !url.startsWith(ARXIV_ABS_PREFIX)) {
    return null;
  }
  return ARXIV_ID_RE.test(url.slice(ARXIV_ABS_PREFIX.length)) ? url : null;
}
