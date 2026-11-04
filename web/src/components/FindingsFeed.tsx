import Link from "next/link";
import type { ApiResult } from "@/lib/api/client";
import type { FindingType, FindingsPage } from "@/lib/api/types";
import { PAGE_SIZE, totalPages as computeTotalPages } from "@/lib/pagination";
import { feedHref } from "@/lib/findingTypes";
import { groupByNight } from "@/lib/feedGroups";
import { FindingCard } from "./FindingCard";
import { Notice } from "./Notice";
import { Pagination } from "./Pagination";
import { TypeFilter } from "./TypeFilter";

type FindingsFeedProps = {
  result: ApiResult<FindingsPage>;
  page: number;
  type: FindingType | null;
};

/** Feed de hallazgos (sin fetch). Cada rama conserva los textos del feed original. */
export function FindingsFeed({ result, page, type }: FindingsFeedProps) {
  return (
    <section
      id="hallazgos"
      aria-labelledby="hallazgos-titulo"
      className="mt-10"
    >
      <h2
        id="hallazgos-titulo"
        className="scroll-mt-32 font-display text-xl break-words text-text sm:text-2xl"
      >
        Últimos hallazgos
      </h2>
      <FeedBody result={result} page={page} type={type} />
    </section>
  );
}

function FeedBody({ result, page, type }: FindingsFeedProps) {
  if (!result.ok) {
    return (
      <div className="mt-4">
        <Notice
          title="No se han podido cargar los hallazgos ahora mismo."
          tone="error"
        >
          Vuelve a intentarlo en unos minutos.
        </Notice>
      </div>
    );
  }

  const { items, total } = result.data;

  if (total === 0 && type !== null) {
    return (
      <>
        <TypeFilter activeType={type} />
        <div className="mt-6">
          <Notice title="No hay hallazgos de este tipo">
            <Link href="/">Ver todos</Link>
          </Notice>
        </div>
      </>
    );
  }

  if (total === 0) {
    return (
      <div className="mt-4">
        <Notice title="Todavía no hay hallazgos publicados.">
          El análisis corre de madrugada; vuelve mañana.
        </Notice>
      </div>
    );
  }

  if (items.length === 0) {
    return (
      <div className="mt-4">
        <Notice title="Esta página no tiene hallazgos.">
          <Link href={feedHref(1, type)}>Ir a la página 1</Link>
        </Notice>
      </div>
    );
  }

  return (
    <>
      <TypeFilter activeType={type} />
      <div className="mt-6 flex flex-col gap-8">
        {groupByNight(items).map((group) => (
          <div key={group.key}>
            <p className="text-sm text-text-muted">
              <time dateTime={group.isoDay}>Publicados el {group.key}</time>
            </p>
            <div className="mt-3 grid grid-cols-1 gap-4 lg:grid-cols-2">
              {group.items.map((finding) => (
                <FindingCard key={finding.id} finding={finding} />
              ))}
            </div>
          </div>
        ))}
      </div>
      <Pagination
        page={page}
        totalPages={computeTotalPages(total, PAGE_SIZE)}
        type={type}
      />
    </>
  );
}
