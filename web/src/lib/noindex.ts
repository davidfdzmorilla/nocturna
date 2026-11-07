import type { Metadata } from "next";

/**
 * Metadata para páginas que sirven 200 con el aviso de API no disponible
 * (T109): que un buscador no indexe el aviso como contenido.
 */
export const NOINDEX: Pick<Metadata, "robots"> = {
  robots: { index: false, follow: false },
};
