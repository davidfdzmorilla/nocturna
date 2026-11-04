import Link from "next/link";
import type { ApiResult } from "@/lib/api/client";
import type { FindingType, FindingsPage } from "@/lib/api/types";
import { PAGE_SIZE, totalPages as computeTotalPages } from "@/lib/pagination";
import { feedHref } from "@/lib/findingTypes";
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
        className="scroll-mt-32 text-xl font-semibold text-text"
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
      <div className="mt-6 flex flex-col gap-4">
        {items.map((finding) => (
          <FindingCard key={finding.id} finding={finding} />
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
