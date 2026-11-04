import Link from "next/link";

/**
 * 404 genérico de todo el sitio. Deliberadamente NO distingue "no
 * existe" de "no publicado": la API de lectura (T50) aplica la misma
 * política, porque distinguirlas dejaría enumerar desde fuera qué
 * candidatos rechazó el Editor.
 */
export default function NotFound() {
  return (
    <div className="mx-auto max-w-3xl py-12 sm:py-16 text-center">
      <h1 className="font-display text-3xl leading-tight text-balance text-text sm:text-4xl">
        Este hallazgo no existe o no está publicado
      </h1>
      <p className="mt-4">
        <Link href="/">Volver al feed</Link>
      </p>
    </div>
  );
}
