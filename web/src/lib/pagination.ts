/**
 * Paginación del listado de hallazgos. Funciones puras, sin IO: el
 * `fetch` vive en `api/client.ts`.
 */

export const PAGE_SIZE = 20;

/**
 * Cota superior de `page` aceptada desde la URL. No tiene relación con
 * cuántas páginas existen de verdad (eso lo decide `total`); es solo un
 * límite de saneado para que un entero absurdo nunca llegue a la API.
 * Seis dígitos (hasta 999.999) sobra de margen frente a cualquier
 * paginación real y cabe holgadamente en cualquier entero de backend.
 */
const MAX_PAGE = 999_999;

/**
 * Interpreta el parámetro `page` de la URL. Cualquier valor ausente,
 * no numérico, menor que 1 o mayor que `MAX_PAGE` cae al default `1`:
 * una página inválida no es un 404, es la primera página.
 *
 * Se valida con una regex de dígitos ASCII (`/^\d{1,6}$/`) antes de
 * convertir a número, en lugar de comprobar el resultado de `Number()`.
 * `Number()` acepta notación científica (`"1e21"` → `1e+21`, que la API
 * rechazaría con 422) y, sin acotar, un entero arbitrariamente grande
 * como `"100000000000000000000"` se reenvía tal cual a la API, que
 * responde 500 (`bigint out of range` en PostgreSQL) y vuelca una traza
 * completa en el log del servidor. La regex descarta ambos casos antes
 * de que el valor exista como número.
 */
export function parsePageParam(raw: string | string[] | undefined): number {
  const value = Array.isArray(raw) ? raw[0] : raw;
  if (value === undefined || !/^\d{1,6}$/.test(value)) {
    return 1;
  }
  const parsed = Number(value);
  if (parsed < 1 || parsed > MAX_PAGE) {
    return 1;
  }
  return parsed;
}

/** Siempre >= 1: incluso un listado vacío (`total === 0`) tiene una página. */
export function totalPages(total: number, size: number): number {
  if (size <= 0) {
    return 1;
  }
  return Math.max(1, Math.ceil(total / size));
}

/** `"/"` para la página 1, `"/?page=n"` para el resto. */
export function pageHref(page: number): string {
  return page <= 1 ? "/" : `/?page=${page}`;
}
