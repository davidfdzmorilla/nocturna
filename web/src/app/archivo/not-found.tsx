import Link from "next/link";

export default function ArchiveNotFound() {
  return (
    <div className="mx-auto max-w-3xl py-12 sm:py-16 text-center">
      <h1 className="font-display text-3xl leading-tight text-balance text-text sm:text-4xl">
        Esta semana no tiene resumen
      </h1>
      <p className="mt-4">
        <Link href="/archivo">Ver el resumen del archivo</Link>
      </p>
    </div>
  );
}
