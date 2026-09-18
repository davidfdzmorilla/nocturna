import Link from "next/link";

/**
 * Cabecera fija del sitio. Server Component, sin JavaScript: el aviso
 * de IA no puede depender de estado ni de interacción, así que no hay
 * botón de cerrar ni forma de que desaparezca.
 *
 * `sticky top-0`, no `fixed`: se queda visible al hacer scroll sin
 * superponerse al contenido ni sacarlo del flujo del documento.
 */
export function SiteHeader() {
  return (
    <header className="sticky top-0 z-50 border-b border-border bg-surface">
      <div className="mx-auto flex w-full max-w-3xl flex-col gap-3 px-4 py-3">
        <nav aria-label="Navegación principal">
          <Link href="/" className="text-lg font-semibold">
            Nocturna
          </Link>
        </nav>
        <p
          role="note"
          className="rounded-md border border-notice-border bg-notice-bg px-3 py-2 text-sm text-notice-text"
        >
          Análisis generado automáticamente por IA. No es un resultado
          científico validado.
        </p>
      </div>
    </header>
  );
}
