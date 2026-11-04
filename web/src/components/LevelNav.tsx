import Link from "next/link";
import type { Level } from "@/lib/levels";
import { LEVEL_LABELS, levelHref } from "@/lib/levels";

const LEVELS: readonly Level[] = ["curioso", "aficionado", "tecnico"];

type LevelNavProps = {
  id: string;
  activeLevel: Level;
};

/**
 * Selector de nivel de lectura de un hallazgo. Tres `<Link>` reales a
 * `?nivel=`, no pestañas de cliente: cada nivel es una URL compartible
 * y el contenido tiene que ser legible con JavaScript desactivado (ver
 * `CLAUDE.md`, accesibilidad). Un `role="tablist"` de cliente exigiría
 * JS para ver los dos niveles no activos y más ARIA para el mismo
 * resultado, sin ganar nada.
 */
export function LevelNav({ id, activeLevel }: LevelNavProps) {
  return (
    <nav
      aria-label="Nivel de detalle"
      className="mt-5 inline-flex max-w-full flex-wrap gap-1 rounded-lg border border-border bg-surface p-1"
    >
      {LEVELS.map((level) => {
        const isActive = level === activeLevel;
        return (
          <Link
            key={level}
            href={levelHref(id, level)}
            aria-current={isActive ? "page" : undefined}
            className={`link-quiet rounded-md px-3 py-1.5 text-sm ${
              isActive
                ? "bg-surface-raised font-semibold"
                : "text-text-muted hover:bg-surface-raised"
            }`}
          >
            {LEVEL_LABELS[level]}
          </Link>
        );
      })}
    </nav>
  );
}
