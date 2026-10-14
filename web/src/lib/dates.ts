/**
 * Formateo de fechas de publicación. Locale y zona horaria fijos
 * (`es-ES` / `Europe/Madrid`) para que el resultado no dependa del
 * entorno donde corre el proceso ni difiera entre servidor y cliente.
 */

const FORMATTER = new Intl.DateTimeFormat("es-ES", {
  timeZone: "Europe/Madrid",
  dateStyle: "long",
  timeStyle: "short",
});

export type PublishedAt = {
  /** ISO tal cual llega de la API; alimenta `<time dateTime>`. */
  iso: string;
  /** Texto legible en español para mostrar. */
  label: string;
};

export function formatPublishedAt(iso: string): PublishedAt {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return { iso, label: iso };
  }
  return { iso, label: FORMATTER.format(date) };
}

// Europe/Madrid debe coincidir con `window.timezone` de config/pipeline.toml,
// con el que el backend agrupa las semanas (ADR 0028 §6).
const DAY_FORMATTER = new Intl.DateTimeFormat("es-ES", {
  timeZone: "Europe/Madrid",
  dateStyle: "long",
});
const DATE_ONLY_FORMATTER = new Intl.DateTimeFormat("es-ES", {
  timeZone: "UTC",
  dateStyle: "long",
});
const DATE_ONLY_RE = /^\d{4}-\d{2}-\d{2}$/;

/**
 * Día legible. Una fecha sin hora (`YYYY-MM-DD`, p. ej. `releasedate`) se
 * formatea en UTC para que no se corra de día; un instante se muestra en
 * Europe/Madrid como el resto de fechas del sitio.
 */
export function formatDay(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    return iso;
  }
  return (DATE_ONLY_RE.test(iso) ? DATE_ONLY_FORMATTER : DAY_FORMATTER).format(
    date,
  );
}
