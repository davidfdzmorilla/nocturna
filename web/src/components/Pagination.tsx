import Link from "next/link";
import { pageHref } from "@/lib/pagination";

type PaginationProps = {
  page: number;
  totalPages: number;
};

/**
 * Paginación del feed. Enlaces reales (`<Link>` con `href`), funciona
 * sin JavaScript. Los bordes (no hay página anterior/siguiente) se
 * renderizan como `<span aria-disabled="true">`, no como enlaces
 * muertos o deshabilitados con JS.
 *
 * `size` es siempre `PAGE_SIZE`; no aparece en la URL ni como prop
 * aquí, así que no hay forma de que este componente lo exponga.
 */
export function Pagination({ page, totalPages }: PaginationProps) {
  const hasPrevious = page > 1;
  const hasNext = page < totalPages;

  return (
    <nav
      aria-label="Paginación de hallazgos"
      className="mt-8 flex items-center justify-between gap-4"
    >
      {hasPrevious ? (
        <Link href={pageHref(page - 1)}>← Anterior</Link>
      ) : (
        <span aria-disabled="true" className="text-text-muted">
          ← Anterior
        </span>
      )}

      <p className="text-sm text-text-muted">
        Página {page} de {totalPages}
      </p>

      {hasNext ? (
        <Link href={pageHref(page + 1)}>Siguiente →</Link>
      ) : (
        <span aria-disabled="true" className="text-text-muted">
          Siguiente →
        </span>
      )}
    </nav>
  );
}
