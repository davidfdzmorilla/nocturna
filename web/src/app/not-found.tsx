import Link from "next/link";

/**
 * 404 genérico de todo el sitio. Deliberadamente NO distingue "no
 * existe" de "no publicado": la API de lectura (T50) aplica la misma
 * política, porque distinguirlas dejaría enumerar desde fuera qué
 * candidatos rechazó el Editor.
 */
export default function NotFound() {
  return (
    <div className="mx-auto max-w-3xl py-16 text-center">
      <h1 className="text-2xl font-semibold text-text">
        Este hallazgo no existe o no está publicado
      </h1>
      <p className="mt-4">
        <Link href="/">Volver al feed</Link>
      </p>
    </div>
  );
}
