import type { ReactNode } from "react";

type NoticeTone = "info" | "error";

type NoticeProps = {
  title: string;
  tone?: NoticeTone;
  children?: ReactNode;
};

/**
 * Aviso de presentación genérico para estados sin contenido: feed
 * vacío, página de paginación sin resultados, API no disponible. Los
 * pasos 4 y 5 lo consumen; no añade más abstracción de la necesaria.
 *
 * `tone="error"` usa `role="alert"` (se anuncia de inmediato); el
 * resto usa `role="status"` (aviso pasivo, no interrumpe).
 */
export function Notice({ title, tone = "info", children }: NoticeProps) {
  return (
    <div
      role={tone === "error" ? "alert" : "status"}
      className="rounded-md border border-border bg-surface px-4 py-6 text-center"
    >
      <p className="font-medium text-text">{title}</p>
      {children ? (
        <div className="mt-2 text-sm text-text-muted">{children}</div>
      ) : null}
    </div>
  );
}
