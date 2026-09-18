/**
 * Configuración de acceso a la API de lectura.
 *
 * `NOCTURNA_API_URL` deliberadamente sin prefijo `NEXT_PUBLIC_`: todo el
 * `fetch` a la API ocurre en el servidor (ver `client.ts`); el prefijo
 * incrustaría la URL en el bundle del navegador y abriría el camino a
 * llamadas desde el cliente que `CLAUDE.md` quiere cerrado.
 *
 * Sin fallback a `http://localhost:8000` ni a ningún otro valor: un
 * default equivocado en producción es un fallo silencioso contra la API
 * de otra persona. Si falta la variable, falla ruidosamente.
 */

export class ApiConfigError extends Error {}

export function apiBaseUrl(): string {
  const raw = process.env.NOCTURNA_API_URL;
  if (!raw || raw.trim() === "") {
    throw new ApiConfigError("NOCTURNA_API_URL no está definida");
  }
  return raw.trim().replace(/\/+$/, "");
}
