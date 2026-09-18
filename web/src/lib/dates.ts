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
