import Link from "next/link";
import { fetchFindingsPage } from "@/lib/api/client";
import {
  PAGE_SIZE,
  parsePageParam,
  totalPages as computeTotalPages,
} from "@/lib/pagination";
import { FindingCard } from "@/components/FindingCard";
import { Pagination } from "@/components/Pagination";
import { Notice } from "@/components/Notice";

/**
 * SSR dinámico, no ISR (decisión del plan): los hallazgos se publican
 * en bloque de madrugada y no cambian después, así que ISR solo
 * aportaría latencia a cambio de tener que razonar sobre el full route
 * cache. Con SSR, `pnpm build` no toca la API nunca.
 */
export const dynamic = "force-dynamic";

type HomeProps = {
  searchParams: Promise<{ page?: string | string[] }>;
};

export default async function Home({ searchParams }: HomeProps) {
  const params = await searchParams;
  const page = parsePageParam(params.page);

  const result = await fetchFindingsPage(page, PAGE_SIZE);

  if (!result.ok) {
    return (
      <>
        <h1 className="sr-only">Nocturna</h1>
        <Notice
          title="No se han podido cargar los hallazgos ahora mismo."
          tone="error"
        >
          Vuelve a intentarlo en unos minutos.
        </Notice>
      </>
    );
  }

  const { items, total } = result.data;

  if (total === 0) {
    return (
      <>
        <h1 className="sr-only">Nocturna</h1>
        <Notice title="Todavía no hay hallazgos publicados.">
          El análisis corre de madrugada; vuelve mañana.
        </Notice>
      </>
    );
  }

  if (items.length === 0) {
    return (
      <>
        <h1 className="sr-only">Nocturna</h1>
        <Notice title="Esta página no tiene hallazgos.">
          <Link href="/">Ir a la página 1</Link>
        </Notice>
      </>
    );
  }

  const pages = computeTotalPages(total, PAGE_SIZE);

  return (
    <>
      <h1 className="text-2xl font-semibold text-text">Últimos hallazgos</h1>
      <div className="mt-6 flex flex-col gap-4">
        {items.map((finding) => (
          <FindingCard key={finding.id} finding={finding} />
        ))}
      </div>
      <Pagination page={page} totalPages={pages} />
    </>
  );
}
