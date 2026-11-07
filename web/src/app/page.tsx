import {
  fetchArchiveWeek,
  fetchArchiveWeeks,
  fetchFindingsPage,
} from "@/lib/api/client";
import { PAGE_SIZE, parsePageParam } from "@/lib/pagination";
import { parseTypeParam } from "@/lib/findingTypes";
import { homeDigestState, latestWeekWithChanges } from "@/lib/archiveRoute";
import type { Metadata } from "next";
import { NOINDEX } from "@/lib/noindex";
import { FindingsFeed } from "@/components/FindingsFeed";
import { HomeDigest } from "@/components/HomeDigest";

/**
 * SSR dinámico, no ISR (decisión del plan): los hallazgos se publican
 * en bloque de madrugada y no cambian después, así que ISR solo
 * aportaría latencia a cambio de tener que razonar sobre el full route
 * cache. Con SSR, `pnpm build` no toca la API nunca.
 */
export const dynamic = "force-dynamic";

type HomeProps = {
  searchParams: Promise<{
    page?: string | string[];
    tipo?: string | string[];
  }>;
};

/**
 * `noindex` solo si TODAS las peticiones de la portada fallan (resumen y
 * feed). Las peticiones se comparten con la página vía `cache()`.
 */
export async function generateMetadata({
  searchParams,
}: HomeProps): Promise<Metadata> {
  const params = await searchParams;
  const [weeks, findings] = await Promise.all([
    fetchArchiveWeeks(),
    fetchFindingsPage(
      parsePageParam(params.page),
      PAGE_SIZE,
      parseTypeParam(params.tipo),
    ),
  ]);
  const allDown =
    !weeks.ok &&
    weeks.reason === "unavailable" &&
    !findings.ok &&
    findings.reason === "unavailable";
  return allDown ? NOINDEX : {};
}

export default async function Home({ searchParams }: HomeProps) {
  const params = await searchParams;
  const page = parsePageParam(params.page);
  const type = parseTypeParam(params.tipo);

  const [weeks, findings] = await Promise.all([
    fetchArchiveWeeks(),
    fetchFindingsPage(page, PAGE_SIZE, type),
  ]);

  const latest = weeks.ok ? latestWeekWithChanges(weeks.data.weeks) : null;
  const detail = latest === null ? null : await fetchArchiveWeek(latest.week);

  return (
    <>
      <HomeDigest state={homeDigestState(weeks, detail)} />
      <FindingsFeed result={findings} page={page} type={type} />
    </>
  );
}
