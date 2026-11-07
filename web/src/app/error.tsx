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
    <div className="mx-auto max-w-3xl py-12 sm:py-16 text-center">
      <h1 className="font-display text-3xl leading-tight text-balance text-text sm:text-4xl">
        No se ha podido cargar esta página
      </h1>
      <p className="mt-4 text-text-muted">
        Vuelve a intentarlo en unos minutos.
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
