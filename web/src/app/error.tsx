"use client";

import Link from "next/link";

/**
 * Frontera de error de Next: único Client Component del proyecto, y
 * solo porque el propio framework lo exige (necesita `reset`, que es
 * estado de React). No hace IO ni muestra nada que venga del error:
 * ni `error.message`, ni `error.digest`, ni código de estado, ni URL.
 */
export default function GlobalError({
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <div className="mx-auto max-w-3xl py-16 text-center">
      <h1 className="text-2xl font-semibold text-text">Algo ha fallado</h1>
      <p className="mt-4 text-text-muted">
        Ha ocurrido un error inesperado al cargar esta página. Puedes reintentar
        o volver al feed.
      </p>
      <div className="mt-6 flex justify-center gap-6">
        <button
          type="button"
          onClick={() => reset()}
          className="rounded-md border border-border px-4 py-2 text-text underline underline-offset-4"
        >
          Reintentar
        </button>
        <Link href="/" className="self-center">
          Volver al feed
        </Link>
      </div>
    </div>
  );
}
