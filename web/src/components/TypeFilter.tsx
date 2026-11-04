import Link from "next/link";
import type { FindingType } from "@/lib/api/types";
import {
  ALL_LABEL,
  FINDING_TYPES,
  TYPE_LABELS,
  feedHref,
} from "@/lib/findingTypes";

type TypeFilterProps = {
  activeType: FindingType | null;
};

const PILL = "link-quiet inline-block rounded-full border px-3 py-1 text-sm";

/**
 * Filtro por tipo del feed. Enlaces reales a `?tipo=`, sin JavaScript,
 * como `LevelNav`. Lista estática con los cuatro tipos; cada enlace va a
 * la página 1.
 */
export function TypeFilter({ activeType }: TypeFilterProps) {
  const options: { type: FindingType | null; label: string }[] = [
    { type: null, label: ALL_LABEL },
    ...FINDING_TYPES.map((type) => ({ type, label: TYPE_LABELS[type] })),
  ];

  return (
    <nav aria-label="Filtrar por tipo" className="mt-4">
      <ul className="flex flex-wrap gap-2">
        {options.map(({ type, label }) => {
          const isActive = type === activeType;
          return (
            <li key={type ?? "todos"}>
              <Link
                href={feedHref(1, type)}
                aria-current={isActive ? "page" : undefined}
                className={
                  isActive
                    ? `${PILL} border-accent bg-surface-raised font-semibold`
                    : `${PILL} border-border text-text-muted hover:border-border-strong`
                }
              >
                {label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
