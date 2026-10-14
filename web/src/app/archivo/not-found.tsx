import Link from "next/link";

export default function ArchiveNotFound() {
  return (
    <div className="mx-auto max-w-3xl py-16 text-center">
      <h1 className="text-2xl font-semibold text-text">
        Esta semana no tiene resumen
      </h1>
      <p className="mt-4">
        <Link href="/archivo">Ver el resumen del archivo</Link>
      </p>
    </div>
  );
}
