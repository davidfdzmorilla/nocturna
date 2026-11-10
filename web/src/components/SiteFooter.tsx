import { fetchDataStatus } from "@/lib/api/client";
import { formatPublishedAt, type PublishedAt } from "@/lib/dates";

/**
 * Pie global (T108). Si `/data-status` falla o no trae fecha válida se
 * muestra el texto de reserva: nunca lanza ni cambia el estado HTTP ni el
 * `noindex` de la página (ADR 0048).
 */
export async function SiteFooter() {
  let updated: PublishedAt | null = null;
  try {
    const result = await fetchDataStatus();
    if (result.ok) {
      const iso = result.data?.data_updated_at;
      if (typeof iso === "string" && !Number.isNaN(new Date(iso).getTime())) {
        updated = formatPublishedAt(iso);
      }
    }
  } catch {
    updated = null;
  }

  return (
    <footer className="border-t border-border py-8 text-sm text-text-muted">
      <div className="mx-auto max-w-5xl space-y-3 px-4 sm:px-6">
        <p>
          Nocturna. Resumen semanal del NASA Exoplanet Archive y hallazgos
          generados automáticamente a partir de arXiv astro-ph.
        </p>
        <p>
          {updated ? (
            <>
              Datos actualizados el{" "}
              <time dateTime={updated.iso}>{updated.label}</time>
            </>
          ) : (
            "Fecha de actualización no disponible"
          )}
        </p>
        <p>
          Abstracts y metadatos de los artículos: arXiv, bajo{" "}
          <a href="https://creativecommons.org/publicdomain/zero/1.0/">
            CC0 1.0
          </a>
          . Datos de exoplanetas:{" "}
          <a href="https://exoplanetarchive.ipac.caltech.edu/">
            NASA Exoplanet Archive
          </a>
          . Gracias a arXiv por el uso de su interoperabilidad de acceso
          abierto.
        </p>
      </div>
    </footer>
  );
}
