import type { ReactNode } from "react";

/**
 * Contenedor con scroll horizontal para tablas anchas (T102). Es una
 * región con nombre (`role="region"` + `aria-labelledby` al `<caption>`)
 * y enfocable (`tabIndex=0`) para que quien navega con teclado pueda
 * desplazarla; el foco usa el anillo global de `globals.css`.
 */
export function TableScroll({
  labelledBy,
  children,
}: {
  labelledBy: string;
  children: ReactNode;
}) {
  return (
    <div
      role="region"
      tabIndex={0}
      aria-labelledby={labelledBy}
      className="overflow-x-auto rounded-md border border-border"
    >
      {children}
    </div>
  );
}

/** Clases compartidas de las tablas de datos. */
export const TABLE = "w-full border-collapse text-left text-sm tabular-nums";
export const CAPTION = "px-3 py-2 text-left text-sm text-text-muted";
export const TH =
  "border-b border-border px-3 py-2 text-xs font-medium uppercase tracking-wider text-text-muted";
export const TH_ROW = "border-b border-border px-3 py-2 text-left font-normal";
export const TD = "border-b border-border px-3 py-2";
/** Celda con un valor numérico: no se parte a mitad de cifra. */
export const TD_VALUE = "border-b border-border px-3 py-2 whitespace-nowrap";
