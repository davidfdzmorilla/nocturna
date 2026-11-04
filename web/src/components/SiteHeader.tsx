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
      <div className="mx-auto flex w-full max-w-5xl flex-col gap-2 px-4 pt-3 pb-2 sm:px-6">
        <nav
          aria-label="Navegación principal"
          className="flex flex-wrap items-center gap-x-6 gap-y-1"
        >
          <Link
            href="/"
            className="link-quiet flex items-center gap-2 font-display text-[1.25rem] tracking-wide"
          >
            <span
              aria-hidden="true"
              className="inline-block size-2 rounded-full bg-accent"
            />
            Nocturna
          </Link>
          <Link href="/archivo" className="link-quiet py-1 text-sm">
            Resumen del archivo
          </Link>
          <Link href="/#hallazgos" className="link-quiet py-1 text-sm">
            Hallazgos
          </Link>
        </nav>
        <p
          role="note"
          className="rounded-md border border-notice-border bg-notice-bg px-3 py-1.5 text-xs text-notice-text sm:text-sm"
        >
          Análisis generado automáticamente por IA. No es un resultado
          científico validado.
        </p>
      </div>
    </header>
  );
}
