import Link from "next/link";
import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { fetchFinding } from "@/lib/api/client";
import { formatPublishedAt } from "@/lib/dates";
import { parseLevelParam, levelText } from "@/lib/levels";
import { LevelNav } from "@/components/LevelNav";
import { Notice } from "@/components/Notice";

/**
 * Igual que el feed: SSR dinámico, no ISR. Ver `app/page.tsx`.
 */
export const dynamic = "force-dynamic";

/**
 * Un `id` que no tiene forma de UUID nunca puede existir: se descarta
 * antes de gastar una petición a la API. La API (T50) usa UUID como
 * identificador público de `Finding`.
 */
const UUID_RE =
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * La API (T50) garantiza que `source_url` siempre lleva este prefijo.
 * Se comprueba igualmente aquí antes de usarlo como `href`: hace
 * explícita esa confianza en un dato ajeno en vez de dar por hecho el
 * contrato de otro servicio sin comprobarlo en el punto de uso.
 */
const ARXIV_ABS_PREFIX = "https://arxiv.org/abs/";

type FindingPageProps = {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ nivel?: string | string[] }>;
};

/**
 * `generateMetadata` y el componente de página piden el mismo hallazgo.
 * Sin más, eso sería doble `fetch` a la API en cada visita: `client.ts`
 * arma cada llamada con un `AbortSignal.timeout()` propio, y la Request
 * Memoization de Next dedupea por referencia de opciones, así que dos
 * señales distintas no se consideran la misma petición aunque la URL
 * coincida (se comprobó contando peticiones reales en el log de
 * uvicorn). La solución es `fetchFinding` envuelto en `cache()` de React
 * (ver `lib/api/client.ts`): deduplica por argumentos dentro del mismo
 * render, así que las dos llamadas de aquí abajo comparten una única
 * petición HTTP.
 */
export async function generateMetadata({
  params,
}: FindingPageProps): Promise<Metadata> {
  const { id } = await params;
  if (!UUID_RE.test(id)) {
    return { title: "Hallazgo" };
  }

  const result = await fetchFinding(id);
  if (!result.ok) {
    return { title: "Hallazgo" };
  }

  return { title: result.data.title };
}

export default async function FindingPage({
  params,
  searchParams,
}: FindingPageProps) {
  const { id } = await params;
  if (!UUID_RE.test(id)) {
    notFound();
  }

  const result = await fetchFinding(id);

  if (!result.ok) {
    if (result.reason === "not_found") {
      // Renderiza `app/not-found.tsx`, que deliberadamente no
      // distingue "no existe" de "no publicado" (ver ese fichero).
      notFound();
    }

    return (
      <>
        <h1 className="sr-only">Hallazgo</h1>
        <Notice
          title="No se han podido cargar los hallazgos ahora mismo."
          tone="error"
        >
          Vuelve a intentarlo en unos minutos.
        </Notice>
      </>
    );
  }

  const finding = result.data;
  const { nivel } = await searchParams;
  const level = parseLevelParam(nivel);
  const { iso, label } = formatPublishedAt(finding.published_at);

  return (
    <>
      <h1 className="text-2xl font-semibold text-text">{finding.title}</h1>
      <time dateTime={iso} className="mt-1 block text-sm text-text-muted">
        {label}
      </time>

      <LevelNav id={finding.id} activeLevel={level} />

      <article className="mt-6 whitespace-pre-line text-text">
        {levelText(finding, level)}
      </article>

      {finding.source_url?.startsWith(ARXIV_ABS_PREFIX) ? (
        <p className="mt-6">
          <a href={finding.source_url} rel="noopener noreferrer">
            Ver el artículo original en arXiv
          </a>
        </p>
      ) : null}

      <p className="mt-8">
        <Link href="/">← Volver al feed</Link>
      </p>
    </>
  );
}
