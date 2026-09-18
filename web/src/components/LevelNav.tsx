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
    <nav aria-label="Nivel de detalle" className="mt-4 flex gap-4">
      {LEVELS.map((level) => {
        const isActive = level === activeLevel;
        return (
          <Link
            key={level}
            href={levelHref(id, level)}
            aria-current={isActive ? "page" : undefined}
            className={isActive ? "font-semibold text-text" : undefined}
          >
            {LEVEL_LABELS[level]}
          </Link>
        );
      })}
    </nav>
  );
}
